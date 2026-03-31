import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from app.config import get_settings

FEATURE_ORDER = [
    "rsi14",
    "macd",
    "macd_hist",
    "atr14",
    "atr_pct",
    "ret5",
    "volatility20",
    "vol_ratio",
    "adx14",
    "vol_cluster_ratio",
]


class SignalPredictor:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.model = None
        self.model_path = Path(self.settings.model_dir) / "xgboost_signal.json"
        self._load()

    def reload(self) -> None:
        self.model = None
        self._load()

    def _load(self) -> None:
        p = self.model_path
        if p.exists():
            try:
                import xgboost as xgb

                self.model = xgb.XGBClassifier()
                self.model.load_model(str(p))
            except Exception:
                self.model = None
        else:
            alt = Path(self.settings.model_dir) / "xgboost_signal.pkl"
            if alt.exists():
                self.model = joblib.load(alt)

    def predict_proba_row(self, features: dict[str, float]) -> tuple[float, float]:
        if self.model is None:
            return 0.5, 0.5
        try:
            row = [float(features[k]) for k in FEATURE_ORDER]
        except KeyError:
            return 0.5, 0.5
        X = np.array([row], dtype=np.float32)
        try:
            if hasattr(self.model, "predict_proba"):
                proba = self.model.predict_proba(X)[0]
                return float(proba[0]), float(proba[1])
            pred = self.model.predict(X)
            return float(1 - pred[0]), float(pred[0])
        except Exception:
            return 0.5, 0.5

    def edge_score(self, p_down: float, p_up: float) -> float:
        """Смещение сигнала: +1 long, -1 short (для согласования с LSTM)."""
        return p_up - p_down

    def explain(self, features: dict[str, float], p_down: float, p_up: float) -> dict[str, Any]:
        out: dict[str, Any] = {
            "features": features,
            "p_down": round(p_down, 4),
            "p_up": round(p_up, 4),
            "model": "xgboost" if self.model is not None else "fallback_neutral",
        }
        if self.model is not None and hasattr(self.model, "feature_importances_"):
            imp = self.model.feature_importances_
            n = min(len(FEATURE_ORDER), len(imp))
            pairs = sorted(zip(FEATURE_ORDER[:n], imp[:n]), key=lambda x: -x[1])[:5]
            out["top_features"] = [{"name": fname, "importance": float(v)} for fname, v in pairs]
        out["confidence"] = round(max(p_down, p_up), 4)
        return out


def explanation_to_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


_predictor: SignalPredictor | None = None


def get_predictor() -> SignalPredictor:
    global _predictor
    if _predictor is None:
        _predictor = SignalPredictor()
    return _predictor
