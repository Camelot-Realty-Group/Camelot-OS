"""
api_server.py — Camelot OS Orchestrator REST API + WebSocket Server

Exposes the Camelot OS Orchestrator as a FastAPI HTTP service.

Endpoints:
    POST   /chat                  → Route user input to the correct bot
    POST   /pipeline              → Execute a named multi-bot pipeline
    GET    /bots                  → List all registered bots and capabilities
    GET    /status                → Health check all bots
    GET    /pipelines             → List available named pipelines
    DELETE /session/{session_id}  → Clear a conversation session
    WS     /ws/{session_id}       → Streaming chat over WebSocket

Run:
    uvicorn orchestrator.api_server:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import (
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Camelot OS internal modules
from bot_registry import BOTS, get_bot_summary, get_bots_with_api
from memory import OrchestratorMemory
from pipeline import NAMED_PIPELINES, PipelineResult, run_pipeline
from router import RouterError, RoutingDecision, classify_intent, explain_routing

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("camelot.api")

# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------

memory = OrchestratorMemory(use_local_fallback=True)
_bot_health_cache: Dict[str, Dict[str, Any]] = {}
_health_cache_ttl: float = 30.0  # seconds


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("Camelot OS Orchestrator API starting up...")
    # Warm the health cache in the background
    asyncio.create_task(_refresh_health_cache())
    yield
    logger.info("Camelot OS Orchestrator API shutting down.")


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Camelot OS Orchestrator API",
    description=(
        "Central AI router and command layer for Camelot Property Management. "
        "Routes requests to Scout, Broker, Compliance, Front Desk, Index, Report, and Deal bots."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow all origins so the dashboard and any client can connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique conversation session identifier. Auto-generated if omitted.",
        example="user-nyc-ops-20260419",
    )
    user_input: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Natural-language request from a Camelot team member.",
        example="Find property management companies in Westchester with 100+ units",
    )
    execute: bool = Field(
        default=True,
        description=(
            "If True, actually execute the routed bot action. "
            "If False, return only the routing decision without executing."
        ),
    )


class ChatResponse(BaseModel):
    session_id: str
    request_id: str
    bot_name: str
    action: str
    params: Dict[str, Any]
    confidence: float
    pipeline: Optional[str]
    rationale: str
    response: Optional[Any]
    error: Optional[str]
    duration_ms: float
    timestamp: str


class PipelineRequest(BaseModel):
    pipeline_name: str = Field(
        ...,
        description="Named pipeline to execute (e.g., 'lead_to_crm', 'property_audit')",
        example="lead_to_crm",
    )
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Seed parameters passed to the pipeline.",
        example={"region": "CT", "property_type": "multifamily"},
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Optional session to associate pipeline execution with.",
    )


class PipelineResponse(BaseModel):
    pipeline_name: str
    pipeline_id: str
    status: str
    steps_total: int
    steps_succeeded: int
    steps_failed: int
    final_output: Optional[Any]
    error: Optional[str]
    duration_seconds: float
    timestamp: str


class BotStatusEntry(BaseModel):
    id: str
    name: str
    status: str          # "online" | "offline" | "unknown"
    latency_ms: Optional[float]
    api_endpoint: Optional[str]
    last_checked: str
    capabilities_count: int


class SystemStatusResponse(BaseModel):
    orchestrator: str
    timestamp: str
    bots: List[BotStatusEntry]
    bots_online: int
    bots_offline: int


# ---------------------------------------------------------------------------
# Bot Execution Helper
# ---------------------------------------------------------------------------

async def _execute_bot_action(
    bot_name: str,
    action: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Dispatch a bot action asynchronously.

    For bots with an HTTP API endpoint, sends a POST request.
    For local/subprocess bots, returns a structured stub response that
    bot-specific modules should override via BotExecutor.register_callable().

    Returns a dict with at minimum: {"status": ..., "data": ..., "bot": bot_name}
    """
    bot_meta = BOTS.get(bot_name)
    if not bot_meta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bot '{bot_name}' not found in registry.",
        )

    api_endpoint = bot_meta.get("api_endpoint")

    if api_endpoint:
        # Dispatch to the bot's HTTP API
        url = f"{api_endpoint.rstrip('/')}/run"
        payload = {"action": action, "params": params}
        timeout = bot_meta.get("timeout_seconds", 60)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Bot '{bot_name}' timed out after {timeout}s.",
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Bot '{bot_name}' returned error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Bot '{bot_name}' communication error: {exc}",
            )

    else:
        # Local/subprocess bot — return a structured acknowledgment.
        # In production, replace this block with BotExecutor.execute() calls.
        logger.info(
            "Local bot dispatch: %s.%s params=%s",
            bot_name, action, list(params.keys())
        )
        return {
            "status": "dispatched",
            "bot": bot_name,
            "action": action,
            "params": params,
            "message": (
                f"{bot_meta['name']} bot received '{action}' request. "
                f"Processing with params: {params}. "
                f"Results will be delivered via the configured output channel."
            ),
            "execution_mode": "subprocess",
            "entry_point": bot_meta.get("entry_point"),
        }


# ---------------------------------------------------------------------------
# Health Check Helper
# ---------------------------------------------------------------------------

async def _check_bot_health(bot_name: str, bot_meta: Dict[str, Any]) -> BotStatusEntry:
    """Ping a bot's health endpoint and return its status."""
    health_url = bot_meta.get("health_check")
    now = datetime.utcnow().isoformat() + "Z"

    if not health_url:
        # Local/subprocess bots don't have HTTP health checks
        return BotStatusEntry(
            id=bot_name,
            name=bot_meta["name"],
            status="local",
            latency_ms=None,
            api_endpoint=None,
            last_checked=now,
            capabilities_count=len(bot_meta.get("capabilities", [])),
        )

    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(health_url)
        latency = (time.monotonic() - t0) * 1000
        bot_status = "online" if resp.status_code == 200 else "degraded"
        return BotStatusEntry(
            id=bot_name,
            name=bot_meta["name"],
            status=bot_status,
            latency_ms=round(latency, 1),
            api_endpoint=health_url,
            last_checked=now,
            capabilities_count=len(bot_meta.get("capabilities", [])),
        )
    except Exception:
        return BotStatusEntry(
            id=bot_name,
            name=bot_meta["name"],
            status="offline",
            latency_ms=None,
            api_endpoint=health_url,
            last_checked=now,
            capabilities_count=len(bot_meta.get("capabilities", [])),
        )


async def _refresh_health_cache():
    """Refresh all bot health statuses into the in-memory cache."""
    global _bot_health_cache
    tasks = [
        _check_bot_health(name, meta)
        for name, meta in BOTS.items()
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for entry in results:
        if isinstance(entry, BotStatusEntry):
            _bot_health_cache[entry.id] = {
                "entry": entry,
                "cached_at": time.monotonic(),
            }
    logger.debug("Bot health cache refreshed for %d bots.", len(results))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", tags=["Meta"])
async def root():
    """API root — confirms the orchestrator is live."""
    return {
        "service": "Camelot OS Orchestrator",
        "version": "1.0.0",
        "status": "online",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "endpoints": ["/chat", "/pipeline", "/bots", "/status", "/pipelines", "/ws/{session_id}"],
    }


@app.post("/chat", response_model=ChatResponse, tags=["Orchestration"])
async def chat(req: ChatRequest):
    """
    Route a natural-language request to the appropriate specialist bot.

    The orchestrator classifies intent, routes to the correct bot, stores
    conversation context in memory, and returns the bot's response.
    """
    request_id = str(uuid.uuid4())
    t0 = time.monotonic()
    logger.info(
        "[%s] Chat request: session=%s input='%s...'",
        request_id, req.session_id, req.user_input[:60]
    )

    # Store the user's message in memory
    memory.add_message(
        session_id=req.session_id,
        role="user",
        content=req.user_input,
        metadata={"request_id": request_id},
    )

    # Classify intent
    routing = classify_intent(req.user_input)

    if isinstance(routing, RouterError):
        # Could not classify — store the error response and return it
        error_msg = routing.message
        suggestions_text = " Try: " + " | ".join(routing.suggestions[:3]) if routing.suggestions else ""
        assistant_content = f"I couldn't determine what you're asking. {error_msg}{suggestions_text}"

        memory.add_message(
            session_id=req.session_id,
            role="assistant",
            content=assistant_content,
            metadata={"request_id": request_id, "error": True},
        )

        duration_ms = (time.monotonic() - t0) * 1000
        return ChatResponse(
            session_id=req.session_id,
            request_id=request_id,
            bot_name="orchestrator",
            action="classify_intent",
            params={},
            confidence=0.0,
            pipeline=None,
            rationale=routing.message,
            response=None,
            error=error_msg,
            duration_ms=round(duration_ms, 1),
            timestamp=datetime.utcnow().isoformat() + "Z",
        )

    # We have a valid routing decision
    bot_response: Optional[Any] = None
    error_detail: Optional[str] = None

    if req.execute:
        try:
            bot_response = await _execute_bot_action(
                bot_name=routing.bot_name,
                action=routing.action,
                params=routing.params,
            )
        except HTTPException as exc:
            error_detail = exc.detail
            bot_response = None
        except Exception as exc:
            error_detail = str(exc)
            bot_response = None

    # Build assistant response content for memory
    if bot_response:
        if isinstance(bot_response, dict) and "message" in bot_response:
            assistant_content = bot_response["message"]
        else:
            assistant_content = json.dumps(bot_response, default=str)
    elif not req.execute:
        assistant_content = explain_routing(routing)
    else:
        assistant_content = f"Bot execution failed: {error_detail}"

    memory.add_message(
        session_id=req.session_id,
        role="assistant",
        content=assistant_content,
        metadata={
            "request_id": request_id,
            "bot": routing.bot_name,
            "action": routing.action,
            "params": routing.params,
            "pipeline": routing.pipeline,
            "confidence": routing.confidence,
        },
    )

    duration_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "[%s] Completed: bot=%s action=%s duration=%.0fms",
        request_id, routing.bot_name, routing.action, duration_ms
    )

    return ChatResponse(
        session_id=req.session_id,
        request_id=request_id,
        bot_name=routing.bot_name,
        action=routing.action,
        params=routing.params,
        confidence=routing.confidence,
        pipeline=routing.pipeline,
        rationale=routing.rationale,
        response=bot_response,
        error=error_detail,
        duration_ms=round(duration_ms, 1),
        timestamp=datetime.utcnow().isoformat() + "Z",
    )


@app.post("/pipeline", response_model=PipelineResponse, tags=["Orchestration"])
async def run_named_pipeline(req: PipelineRequest):
    """
    Execute a named multi-bot pipeline.

    Available pipelines: lead_to_crm, property_audit, deal_outreach,
    new_acquisition_dd, weekly_ops_rhythm, lease_audit.
    """
    if req.pipeline_name not in NAMED_PIPELINES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Pipeline '{req.pipeline_name}' not found. "
                f"Available: {list(NAMED_PIPELINES.keys())}"
            ),
        )

    logger.info(
        "Pipeline request: name=%s params=%s session=%s",
        req.pipeline_name, req.params, req.session_id
    )

    # Log pipeline start to session memory if session provided
    if req.session_id:
        memory.add_message(
            session_id=req.session_id,
            role="system",
            content=f"Pipeline '{req.pipeline_name}' started with params: {req.params}",
            metadata={"pipeline": req.pipeline_name, "params": req.params},
        )

    # Run in a thread pool to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    result: PipelineResult = await loop.run_in_executor(
        None,
        lambda: run_pipeline(req.pipeline_name, req.params),
    )

    # Log pipeline completion to session memory
    if req.session_id:
        memory.add_message(
            session_id=req.session_id,
            role="assistant",
            content=(
                f"Pipeline '{req.pipeline_name}' completed with status '{result.status.value}'. "
                f"{result.succeeded_steps}/{len(result.steps)} steps succeeded."
            ),
            metadata=result.summary(),
        )

    return PipelineResponse(
        pipeline_name=result.pipeline_name,
        pipeline_id=result.pipeline_id,
        status=result.status.value,
        steps_total=len(result.steps),
        steps_succeeded=result.succeeded_steps,
        steps_failed=result.failed_steps,
        final_output=result.final_output,
        error=result.error,
        duration_seconds=round(result.duration_seconds, 2),
        timestamp=datetime.utcnow().isoformat() + "Z",
    )


@app.get("/bots", tags=["Registry"])
async def list_bots():
    """
    Return all registered bots with their metadata and capabilities.
    """
    return {
        "bots": get_bot_summary(),
        "total": len(BOTS),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/pipelines", tags=["Registry"])
async def list_pipelines():
    """Return all available named pipelines and their metadata."""
    return {
        "pipelines": list(NAMED_PIPELINES.values()),
        "total": len(NAMED_PIPELINES),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/status", response_model=SystemStatusResponse, tags=["Health"])
async def system_status():
    """
    Health check all bots and return system status.

    Results are cached for 30 seconds to avoid hammering bot endpoints.
    """
    now = time.monotonic()
    needs_refresh = any(
        (now - v.get("cached_at", 0)) > _health_cache_ttl
        for v in _bot_health_cache.values()
    ) or len(_bot_health_cache) < len(BOTS)

    if needs_refresh:
        await _refresh_health_cache()

    entries: List[BotStatusEntry] = [
        v["entry"]
        for v in _bot_health_cache.values()
        if "entry" in v
    ]

    # Count online vs offline (local = not counted as offline)
    online = sum(1 for e in entries if e.status in ("online", "local"))
    offline = sum(1 for e in entries if e.status == "offline")

    return SystemStatusResponse(
        orchestrator="online",
        timestamp=datetime.utcnow().isoformat() + "Z",
        bots=entries,
        bots_online=online,
        bots_offline=offline,
    )


@app.get("/history/{session_id}", tags=["Memory"])
async def get_session_history(session_id: str, last_n: int = 20):
    """Retrieve conversation history for a session."""
    msgs = memory.get_history(session_id, last_n=last_n)
    return {
        "session_id": session_id,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "metadata": m.metadata,
                "created_at": m.created_at,
            }
            for m in msgs
        ],
        "count": len(msgs),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@app.delete("/session/{session_id}", tags=["Memory"])
async def clear_session(session_id: str):
    """Clear all conversation history for a session."""
    success = memory.clear_session(session_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear session '{session_id}'.",
        )
    return {
        "session_id": session_id,
        "cleared": True,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# WebSocket — Streaming Chat
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages active WebSocket connections keyed by session_id."""

    def __init__(self):
        self.active: Dict[str, WebSocket] = {}

    async def connect(self, session_id: str, ws: WebSocket):
        await ws.accept()
        self.active[session_id] = ws
        logger.info("WebSocket connected: session=%s", session_id)

    def disconnect(self, session_id: str):
        self.active.pop(session_id, None)
        logger.info("WebSocket disconnected: session=%s", session_id)

    async def send(self, session_id: str, data: Dict[str, Any]):
        ws = self.active.get(session_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception as exc:
                logger.warning("WS send failed for session %s: %s", session_id, exc)


manager = ConnectionManager()


@app.websocket("/ws/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    """
    Streaming chat interface over WebSocket.

    Client sends: {"user_input": "...", "execute": true}
    Server sends a sequence of events:
        {"type": "routing",  "data": {...}}
        {"type": "progress", "data": {"message": "..."}}
        {"type": "response", "data": {...}}
        {"type": "error",    "data": {"message": "..."}}
        {"type": "done"}
    """
    await manager.connect(session_id, websocket)

    async def emit(event_type: str, data: Any = None):
        """Send a typed event to the client."""
        await manager.send(session_id, {"type": event_type, "data": data})

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await emit("error", {"message": "Invalid JSON payload."})
                continue

            user_input = msg.get("user_input", "").strip()
            execute = msg.get("execute", True)

            if not user_input:
                await emit("error", {"message": "Empty user_input."})
                continue

            request_id = str(uuid.uuid4())
            t0 = time.monotonic()

            # Store user message
            memory.add_message(session_id, "user", user_input,
                               metadata={"request_id": request_id})

            # Classify intent
            await emit("progress", {"message": "Analyzing your request..."})
            routing = classify_intent(user_input)

            if isinstance(routing, RouterError):
                await emit("error", {
                    "message": routing.message,
                    "suggestions": routing.suggestions,
                })
                memory.add_message(session_id, "assistant",
                                   routing.message, metadata={"error": True})
                await emit("done")
                continue

            # Emit the routing decision
            await emit("routing", {
                "bot": routing.bot_name,
                "action": routing.action,
                "params": routing.params,
                "confidence": routing.confidence,
                "pipeline": routing.pipeline,
                "rationale": routing.rationale,
            })

            # Execute (or just route if execute=False)
            bot_response = None
            error_detail = None

            if execute:
                await emit("progress", {
                    "message": f"Dispatching to {routing.bot_name.title()} bot..."
                })
                try:
                    bot_response = await _execute_bot_action(
                        routing.bot_name, routing.action, routing.params
                    )
                except HTTPException as exc:
                    error_detail = exc.detail
                except Exception as exc:
                    error_detail = str(exc)

            duration_ms = round((time.monotonic() - t0) * 1000, 1)

            if error_detail:
                await emit("error", {"message": error_detail})
                memory.add_message(session_id, "assistant",
                                   f"Error: {error_detail}",
                                   metadata={"request_id": request_id, "error": True})
            else:
                # Assemble clean response content
                if bot_response and isinstance(bot_response, dict) and "message" in bot_response:
                    display = bot_response["message"]
                elif not execute:
                    display = explain_routing(routing)
                else:
                    display = json.dumps(bot_response, default=str, indent=2)

                await emit("response", {
                    "request_id": request_id,
                    "bot": routing.bot_name,
                    "action": routing.action,
                    "content": display,
                    "raw": bot_response,
                    "duration_ms": duration_ms,
                })

                memory.add_message(session_id, "assistant", display,
                                   metadata={
                                       "request_id": request_id,
                                       "bot": routing.bot_name,
                                       "action": routing.action,
                                       "duration_ms": duration_ms,
                                   })

            await emit("done")

    except WebSocketDisconnect:
        manager.disconnect(session_id)
    except Exception as exc:
        logger.exception("Unhandled WS error for session %s: %s", session_id, exc)
        try:
            await emit("error", {"message": f"Internal error: {exc}"})
        except Exception:
            pass
        manager.disconnect(session_id)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    reload = os.environ.get("ENV", "production").lower() == "development"

    logger.info("Starting Camelot OS Orchestrator on %s:%d", host, port)
    uvicorn.run(
        "api_server:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
