"""
Meta-модель: «торговать / не торговать» по расширенному вектору (фичи + режим + edge).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from app.config import get_settings
from app.ml.predictor import FEATURE_ORDER
from app.ml.regime import MarketRegime


def regime_one_hot(reg: MarketRegime) -> dict[str, float]:
    return {
        "regime_high": 1.0 if reg == MarketRegime.HIGH_VOLATILITY else 0.0,
        "regime_tu": 1.0 if reg == MarketRegime.TREND_UP else 0.0,
        "regime_td": 1.0 if reg == MarketRegime.TREND_DOWN else 0.0,
        "regime_flat": 1.0 if reg == MarketRegime.FLAT else 0.0,
    }


META_ORDER = list(FEATURE_ORDER) + ["regime_high", "regime_tu", "regime_td", "regime_flat", "combined_edge", "adx14", "vol_cluster_ratio"]


class MetaFilter:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.model = None
        self.path = Path(self.settings.model_dir) / "meta_xgboost.json"
        self._load()

    def reload(self) -> None:
        self.model = None
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            import xgboost as xgb

            self.model = xgb.XGBClassifier()
            self.model.load_model(str(self.path))
        except Exception:
            self.model = None

    def build_row(
        self,
        feats: dict[str, float],
        reg: MarketRegime,
        combined_edge: float,
    ) -> np.ndarray:
        oh = regime_one_hot(reg)
        row: list[float] = [float(feats[k]) for k in FEATURE_ORDER]
        row += [oh["regime_high"], oh["regime_tu"], oh["regime_td"], oh["regime_flat"]]
        row.append(float(combined_edge))
        row.append(float(feats.get("adx14", 20.0)))
        row.append(float(feats.get("vol_cluster_ratio", 1.0)))
        return np.array([row], dtype=np.float32)

    def predict_trade_proba(self, feats: dict[str, float], reg: MarketRegime, combined_edge: float) -> tuple[float, float]:
        if self.model is None:
            return 0.0, 1.0
        X = self.build_row(feats, reg, combined_edge)
        try:
            p = self.model.predict_proba(X)[0]
            return float(p[0]), float(p[1])
        except Exception:
            return 0.0, 1.0


_meta: MetaFilter | None = None


def get_meta_filter() -> MetaFilter:
    global _meta
    if _meta is None:
        _meta = MetaFilter()
    return _meta
