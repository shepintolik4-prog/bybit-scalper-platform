"""Файл состояния self-improve (смещение порога edge)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.config import get_settings


def _path() -> Path:
    s = get_settings()
    p = Path(s.adaptive_state_path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent.parent / p
    return p


def load_adaptive() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {"edge_bias": 0.0, "last_self_improve_ts": None}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"edge_bias": 0.0, "last_self_improve_ts": None}


def save_adaptive(data: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def get_edge_bias() -> float:
    return float(load_adaptive().get("edge_bias", 0.0))
