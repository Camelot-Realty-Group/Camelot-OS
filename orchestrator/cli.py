"""
cli.py — Camelot OS Command-Line Interface

Direct terminal access to the Camelot OS Orchestrator.

Usage:
    # Single command
    python orchestrator/cli.py "Find PM companies in Westchester"

    # Interactive REPL mode
    python orchestrator/cli.py --interactive

    # Route only (don't execute)
    python orchestrator/cli.py --dry-run "Draft an LOI for 123 Main St at $2M"

    # Use a specific session ID
    python orchestrator/cli.py --session my-session-id --interactive

    # Named pipeline
    python orchestrator/cli.py --pipeline lead_to_crm --params '{"region": "CT"}'

    # JSON output (for scripting)
    python orchestrator/cli.py --json "Check violations for 456 Park Ave"
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Rich imports — graceful fallback if not installed
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.prompt import Prompt
    from rich.rule import Rule
    from rich.live import Live
    from rich.spinner import Spinner
    from rich.columns import Columns
    from rich.markup import escape
    _RICH = True
except ImportError:
    _RICH = False
    print(
        "[WARNING] 'rich' library not installed. Run: pip install rich\n"
        "Falling back to plain output.\n",
        file=sys.stderr,
    )

# ---------------------------------------------------------------------------
# Camelot OS modules
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(__file__))

from bot_registry import BOTS, get_bot_summary
from memory import OrchestratorMemory
from pipeline import NAMED_PIPELINES, run_pipeline
from router import RouterError, RoutingDecision, classify_intent, explain_routing

# ---------------------------------------------------------------------------
# Color palette (Camelot branding)
# ---------------------------------------------------------------------------
GOLD = "bold yellow"
NAVY = "bold blue"
WHITE = "white"
GREY = "dim white"
RED = "bold red"
GREEN = "bold green"
CYAN = "bold cyan"

BOT_COLORS = {
    "scout":      "cyan",
    "broker":     "yellow",
    "compliance": "red",
    "frontdesk":  "green",
    "index":      "magenta",
    "report":     "bright_blue",
    "deal":       "bright_cyan",
}

# ---------------------------------------------------------------------------
# Console setup
# ---------------------------------------------------------------------------
console = Console(highlight=False) if _RICH else None


def _print(text: str = "", style: str = "", markup: bool = True):
    """Print to console with optional rich styling."""
    if _RICH and console:
        if markup:
            console.print(text, style=style)
        else:
            console.print(escape(text), style=style)
    else:
        print(text)


def _rule(title: str = ""):
    if _RICH and console:
        console.print(Rule(title, style=GOLD))
    else:
        width = 60
        if title:
            pad = (width - len(title) - 2) // 2
            print("─" * pad + f" {title} " + "─" * pad)
        else:
            print("─" * width)


def _header():
    """Print the Camelot OS ASCII banner."""
    if _RICH and console:
        banner = Text()
        banner.append("\n  ╔═══════════════════════════════════╗\n", style=GOLD)
        banner.append("  ║       CAMELOT OS  v1.0.0          ║\n", style=GOLD)
        banner.append("  ║  AI-Driven Property Operations    ║\n", style=GREY)
        banner.append("  ╚═══════════════════════════════════╝\n", style=GOLD)
        console.print(banner)
    else:
        print("\n  === CAMELOT OS v1.0.0 ===\n")


def _bot_badge(bot_name: str) -> str:
    """Return a rich-formatted bot name badge."""
    color = BOT_COLORS.get(bot_name, "white")
    return f"[{color}][ {bot_name.upper()} ][/{color}]"


# ---------------------------------------------------------------------------
# Core execution logic
# ---------------------------------------------------------------------------

def route_and_display(
    user_input: str,
    session_id: str,
    memory: OrchestratorMemory,
    dry_run: bool = False,
    json_output: bool = False,
) -> dict:
    """
    Classify intent, optionally execute, and display results.

    Returns a dict with the result data (for JSON mode / scripting).
    """
    t0 = time.monotonic()

    # Store user message
    memory.add_message(session_id, "user", user_input)

    # Classify
    routing = classify_intent(user_input)

    if isinstance(routing, RouterError):
        result = {
            "status": "error",
            "error": routing.message,
            "suggestions": routing.suggestions,
        }
        if json_output:
            print(json.dumps(result, indent=2))
            return result

        _rule("ROUTING ERROR")
        _print(f"[{RED}]✗ Could not classify intent: {escape(routing.message)}[/{RED}]", markup=True)
        if routing.suggestions:
            _print("\n[dim]Suggestions:[/dim]", markup=True)
            for s in routing.suggestions:
                _print(f"  [grey50]• {escape(s)}[/grey50]", markup=True)
        memory.add_message(session_id, "assistant", routing.message, metadata={"error": True})
        return result

    # Display routing decision
    duration_ms = round((time.monotonic() - t0) * 1000, 1)

    if json_output:
        result = {
            "status": "routed",
            "bot": routing.bot_name,
            "action": routing.action,
            "params": routing.params,
            "confidence": routing.confidence,
            "pipeline": routing.pipeline,
            "rationale": routing.rationale,
            "dry_run": dry_run,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        print(json.dumps(result, indent=2))
        return result

    # Rich routing panel
    _rule()
    if _RICH and console:
        bot_color = BOT_COLORS.get(routing.bot_name, "white")
        bot_label = f"[{bot_color}]● {routing.bot_name.upper()} BOT[/{bot_color}]"
        action_label = f"[{GOLD}]{routing.action}[/{GOLD}]"
        confidence_label = f"[dim]{routing.confidence:.0%} confidence[/dim]"

        lines = [
            f"  {bot_label}  →  {action_label}  {confidence_label}",
        ]
        if routing.params:
            params_str = "  ".join(f"[dim]{k}=[/dim][white]{escape(str(v))}[/white]"
                                    for k, v in routing.params.items() if v is not None)
            lines.append(f"  [dim]params:[/dim]  {params_str}")
        if routing.pipeline:
            lines.append(f"  [dim]pipeline:[/dim] [{bot_color}]{routing.pipeline}[/{bot_color}]")

        lines.append(f"\n  [dim italic]{escape(routing.rationale)}[/dim italic]")

        console.print(Panel(
            "\n".join(lines),
            title="[bold yellow]ROUTING DECISION[/bold yellow]",
            border_style="dim yellow",
            padding=(0, 1),
        ))
    else:
        print(explain_routing(routing))

    # Dry run — stop here
    if dry_run:
        _print("\n[dim italic]Dry run mode — bot not executed.[/dim italic]", markup=True)
        memory.add_message(session_id, "assistant",
                           explain_routing(routing),
                           metadata={"dry_run": True, "bot": routing.bot_name})
        return {"status": "dry_run", "routing": routing.__dict__}

    # Execute
    _print(f"\n[dim]Executing {routing.bot_name}.{routing.action}...[/dim]", markup=True)

    try:
        # Import and invoke the BotExecutor
        from pipeline import BotExecutor
        executor = BotExecutor()
        exec_t0 = time.monotonic()
        bot_output = executor.execute(
            bot_name=routing.bot_name,
            action=routing.action,
            params=routing.params,
        )
        exec_ms = round((time.monotonic() - exec_t0) * 1000, 1)

        # Format and display response
        _rule("BOT RESPONSE")

        if isinstance(bot_output, dict):
            message = bot_output.get("message") or bot_output.get("result") or ""
            if message:
                if _RICH and console:
                    console.print(Panel(
                        escape(str(message)),
                        title=_bot_badge(routing.bot_name),
                        border_style=BOT_COLORS.get(routing.bot_name, "white"),
                        padding=(1, 2),
                    ))
                else:
                    print(f"\n[{routing.bot_name.upper()}] {message}\n")
            else:
                # Fallback: pretty-print the full dict
                if _RICH and console:
                    console.print_json(json.dumps(bot_output, default=str))
                else:
                    print(json.dumps(bot_output, indent=2, default=str))
        else:
            _print(str(bot_output), style=WHITE)

        _print(f"\n[{GREY}]⏱  {exec_ms}ms[/{GREY}]", markup=True)

        # Store in memory
        response_content = (
            bot_output.get("message", json.dumps(bot_output, default=str))
            if isinstance(bot_output, dict) else str(bot_output)
        )
        memory.add_message(session_id, "assistant", response_content,
                           metadata={"bot": routing.bot_name, "action": routing.action,
                                     "duration_ms": exec_ms})

        return {"status": "success", "bot": routing.bot_name,
                "action": routing.action, "output": bot_output}

    except Exception as exc:
        error_str = str(exc)
        _rule("EXECUTION ERROR")
        _print(f"[{RED}]✗ {escape(error_str)}[/{RED}]", markup=True)
        memory.add_message(session_id, "assistant", f"Error: {error_str}",
                           metadata={"error": True, "bot": routing.bot_name})
        return {"status": "error", "error": error_str}


def run_pipeline_cli(
    pipeline_name: str,
    params: dict,
    session_id: str,
    memory: OrchestratorMemory,
    json_output: bool = False,
):
    """Execute a named pipeline from the CLI."""
    if pipeline_name not in NAMED_PIPELINES:
        _print(f"[{RED}]✗ Unknown pipeline '{pipeline_name}'.[/{RED}]", markup=True)
        _print(f"[dim]Available: {', '.join(NAMED_PIPELINES.keys())}[/dim]", markup=True)
        sys.exit(1)

    meta = NAMED_PIPELINES[pipeline_name]
    _print(f"\n[{GOLD}]▶ Pipeline:[/{GOLD}] {pipeline_name}", markup=True)
    _print(f"[{GREY}]  {meta['description']}[/{GREY}]", markup=True)
    _print(f"[{GREY}]  Bots: {', '.join(meta['bots_involved'])}[/{GREY}]\n", markup=True)

    t0 = time.monotonic()
    result = run_pipeline(pipeline_name, params)
    elapsed = time.monotonic() - t0

    if json_output:
        print(json.dumps(result.summary(), indent=2, default=str))
        return

    _rule("PIPELINE RESULT")
    status_color = GREEN if result.status.value == "completed" else RED
    _print(f"[{status_color}]{result.status.value.upper()}[/{status_color}]  "
           f"[{GREY}]{result.succeeded_steps}/{len(result.steps)} steps[/{GREY}]  "
           f"[{GREY}]{elapsed:.1f}s[/{GREY}]", markup=True)

    if _RICH and console:
        table = Table(show_header=True, header_style=GOLD, box=None, padding=(0, 2))
        table.add_column("Step", style="white")
        table.add_column("Bot", style="cyan")
        table.add_column("Status", style="white")
        table.add_column("Duration", style="dim")
        table.add_column("Error", style="red")

        for sr in result.steps:
            st_color = GREEN if sr.status.value == "completed" else (
                RED if sr.status.value == "failed" else GREY
            )
            table.add_row(
                sr.step.step_name,
                sr.step.bot_name,
                f"[{st_color}]{sr.status.value}[/{st_color}]",
                f"{sr.duration_seconds:.1f}s" if sr.duration_seconds else "—",
                escape(sr.error or ""),
            )
        console.print(table)

    if result.error:
        _print(f"\n[{RED}]Error: {escape(result.error)}[/{RED}]", markup=True)


def show_bots():
    """Display all registered bots in a table."""
    _header()
    if _RICH and console:
        table = Table(
            title="[bold yellow]Registered Bots[/bold yellow]",
            show_header=True,
            header_style=GOLD,
            border_style="dim yellow",
            padding=(0, 1),
        )
        table.add_column("Bot", style="bold white", width=12)
        table.add_column("Domain", style="white", width=30)
        table.add_column("Capabilities", style="dim", width=8)
        table.add_column("API", style="dim", width=6)

        for name, meta in BOTS.items():
            color = BOT_COLORS.get(name, "white")
            bot_cell = f"[{color}]{meta['icon']} {meta['name']}[/{color}]"
            api_cell = "[green]HTTP[/green]" if meta.get("api_endpoint") else "[dim]local[/dim]"
            table.add_row(
                bot_cell,
                meta["description"][:60] + ("…" if len(meta["description"]) > 60 else ""),
                str(len(meta.get("capabilities", []))),
                api_cell,
            )
        console.print(table)
    else:
        for name, meta in BOTS.items():
            print(f"{meta['name']:12} — {meta['description'][:60]}")


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------

def interactive_repl(session_id: str, memory: OrchestratorMemory, json_output: bool):
    """Run the interactive chat REPL."""
    _header()
    _rule("INTERACTIVE MODE")
    _print(f"[{GREY}]Session: {session_id}[/{GREY}]", markup=True)
    _print(f"[{GREY}]Type a command or question. Commands: /bots /pipelines /clear /history /exit[/{GREY}]\n",
           markup=True)

    while True:
        try:
            if _RICH and console:
                user_input = Prompt.ask(f"[{GOLD}]camelot[/{GOLD}]")
            else:
                user_input = input("camelot> ").strip()
        except (KeyboardInterrupt, EOFError):
            _print(f"\n[{GOLD}]Goodbye.[/{GOLD}]", markup=True)
            break

        if not user_input.strip():
            continue

        # Built-in REPL commands
        cmd = user_input.strip().lower()

        if cmd in ("/exit", "/quit", "exit", "quit"):
            _print(f"[{GOLD}]Goodbye.[/{GOLD}]", markup=True)
            break

        elif cmd == "/bots":
            show_bots()
            continue

        elif cmd == "/pipelines":
            _rule("AVAILABLE PIPELINES")
            for name, meta in NAMED_PIPELINES.items():
                _print(f"[{GOLD}]{name}[/{GOLD}]  [dim]{meta['description']}[/dim]", markup=True)
            _print("", markup=False)
            continue

        elif cmd == "/clear":
            memory.clear_session(session_id)
            _print(f"[{GREEN}]Session cleared.[/{GREEN}]", markup=True)
            continue

        elif cmd == "/history":
            msgs = memory.get_history(session_id, last_n=10)
            _rule("CONVERSATION HISTORY")
            for m in msgs:
                role_color = GOLD if m.role == "user" else (GREEN if m.role == "assistant" else GREY)
                label = m.role.upper()
                _print(f"[{role_color}]{label}[/{role_color}]  [dim]{m.content[:120]}[/dim]",
                       markup=True)
            _print("", markup=False)
            continue

        elif cmd.startswith("/pipeline "):
            parts = cmd[len("/pipeline "):].strip().split(maxsplit=1)
            pname = parts[0]
            raw_params = parts[1] if len(parts) > 1 else "{}"
            try:
                pparams = json.loads(raw_params)
            except json.JSONDecodeError:
                _print(f"[{RED}]Invalid JSON params.[/{RED}]", markup=True)
                continue
            run_pipeline_cli(pname, pparams, session_id, memory, json_output)
            continue

        elif cmd.startswith("/help") or cmd == "?":
            _rule("HELP")
            help_lines = [
                ("/bots",               "List all registered bots"),
                ("/pipelines",          "List available named pipelines"),
                ("/pipeline NAME {...}", "Run a named pipeline with JSON params"),
                ("/history",            "Show recent conversation history"),
                ("/clear",              "Clear this session's history"),
                ("/exit",               "Quit the REPL"),
                ("<any text>",          "Route to the appropriate bot and execute"),
            ]
            for cmd_name, desc in help_lines:
                _print(f"  [{GOLD}]{cmd_name:<30}[/{GOLD}]  [dim]{desc}[/dim]", markup=True)
            _print("", markup=False)
            continue

        # Regular input — route and execute
        route_and_display(user_input, session_id, memory,
                          dry_run=False, json_output=json_output)
        print()  # spacing


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="camelot",
        description="Camelot OS Command-Line Interface — AI property management orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py "Find PM companies in Westchester"
  python cli.py --interactive
  python cli.py --dry-run "Draft an LOI for 123 Main St at $2.5M"
  python cli.py --pipeline lead_to_crm --params '{"region": "CT"}'
  python cli.py --bots
  python cli.py --json "Check violations for 456 Park Ave"
        """,
    )

    parser.add_argument(
        "input",
        nargs="?",
        help="Natural-language command to route and execute",
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Start interactive REPL mode",
    )
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Route the request but do not execute the bot action",
    )
    parser.add_argument(
        "--json", "-J",
        action="store_true",
        help="Output results as JSON (useful for scripting)",
    )
    parser.add_argument(
        "--session", "-s",
        type=str,
        default=None,
        help="Session ID for conversation context (auto-generated if omitted)",
    )
    parser.add_argument(
        "--pipeline", "-p",
        type=str,
        default=None,
        help="Run a named pipeline (e.g., lead_to_crm, property_audit)",
    )
    parser.add_argument(
        "--params",
        type=str,
        default="{}",
        help="JSON-encoded parameters for --pipeline",
    )
    parser.add_argument(
        "--bots",
        action="store_true",
        help="List all registered bots and exit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="Camelot OS CLI v1.0.0",
    )

    args = parser.parse_args()

    # Session ID
    session_id = args.session or f"cli-{str(uuid.uuid4())[:8]}"

    # Memory layer
    mem = OrchestratorMemory(use_local_fallback=True)

    # --bots flag
    if args.bots:
        show_bots()
        sys.exit(0)

    # --interactive flag
    if args.interactive:
        interactive_repl(session_id, mem, json_output=args.json)
        sys.exit(0)

    # --pipeline flag
    if args.pipeline:
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as exc:
            _print(f"[{RED}]Invalid --params JSON: {exc}[/{RED}]", markup=True)
            sys.exit(1)
        run_pipeline_cli(args.pipeline, params, session_id, mem, json_output=args.json)
        sys.exit(0)

    # Single-shot command
    if args.input:
        if not args.json:
            _header()
        result = route_and_display(
            user_input=args.input,
            session_id=session_id,
            memory=mem,
            dry_run=args.dry_run,
            json_output=args.json,
        )
        sys.exit(0 if result.get("status") != "error" else 1)

    # No arguments — show help
    parser.print_help()
    sys.exit(0)


if __name__ == "__main__":
    main()
