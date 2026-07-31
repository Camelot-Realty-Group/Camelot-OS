"""
config_loader.py — CostBeat Bot configuration access

Loads costbeat_bot/config.yaml once and hands the same dict to every
module (parser, benchmarks, analyzer, fee_engine, report_generator, main).

Author: Camelot OS
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

logger = logging.getLogger("costbeat_bot.config")

CONFIG_PATH = Path(__file__).parent / "config.yaml"


@lru_cache(maxsize=1)
def load_config() -> Dict[str, Any]:
    """Return the parsed config.yaml. Cached for the process lifetime."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"CostBeat config not found: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg


def reports_output_dir() -> str:
    """Resolve the PDF output directory (env override wins)."""
    cfg = load_config()
    return os.getenv("COSTBEAT_OUTPUT_DIR", cfg.get("reports_output_dir", "output/costbeat"))
