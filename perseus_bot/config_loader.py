"""
config_loader.py — Perseus configuration access

Loads perseus_bot/config.yaml once and hands the same dict to every module
(parser, benchmarks, variance_engine, fee_engine, report_generator, main).

Author: Camelot OS
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

logger = logging.getLogger("perseus_bot.config")

CONFIG_PATH = Path(__file__).parent / "config.yaml"


@lru_cache(maxsize=1)
def load_config() -> Dict[str, Any]:
    """Return the parsed config.yaml. Cached for the process lifetime."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Perseus config not found: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg


def reports_output_dir() -> str:
    """Resolve the PDF output directory (env override wins)."""
    cfg = load_config()
    return os.getenv("PERSEUS_OUTPUT_DIR", cfg.get("reports_output_dir", "output/perseus"))


def periods_per_year(cadence: str) -> int:
    """
    How many periods of the given cadence make a year. Used both to prorate the
    annual budget down to the period and to annualize the period's actuals.
    """
    table = load_config()["variance"]["periods_per_year"]
    if cadence not in table:
        raise ValueError(
            f"Unknown cadence '{cadence}'. Configured cadences: {sorted(table)}."
        )
    return int(table[cadence])
