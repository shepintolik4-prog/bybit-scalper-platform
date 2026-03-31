"""
Журнал исходов сделок (JSONL) для последующего дообучения / анализа ошибок.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from app.config import get_settings


def _path() -> Path:
    p = get_settings().trade_outcomes_path
    return Path(p)


def append_trade_outcome(payload: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": time.time(), **payload}, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        try:
            f.flush()
            os.fsync(f.fileno())
        except OSError:
            pass
