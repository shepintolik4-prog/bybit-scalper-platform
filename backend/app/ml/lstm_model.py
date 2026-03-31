"""
Лёгкая LSTM-голова для последовательностей дообучения (опционально).
Использует последние N баров нормализованных returns + объёма.
"""
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn

from app.config import get_settings


class TinyLSTM(nn.Module):
    def __init__(self, input_dim: int = 3, hidden: int = 32) -> None:
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden, batch_first=True, num_layers=1)
        self.fc = nn.Linear(hidden, 2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        last = out[:, -1, :]
        return self.fc(last)


class LSTMScorer:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.path = Path(self.settings.model_dir) / "lstm_scalper.pt"
        self.model: TinyLSTM | None = None
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            ck = torch.load(self.path, map_location="cpu", weights_only=False)
            m = TinyLSTM()
            m.load_state_dict(ck["state_dict"])
            m.eval()
            self.model = m
        except Exception:
            self.model = None

    def sequence_from_ohlcv(self, closes: list[float], volumes: list[float], seq: int = 32) -> np.ndarray | None:
        if len(closes) < seq + 1:
            return None
        c = np.array(closes[-seq - 1 :], dtype=np.float64)
        v = np.array(volumes[-seq - 1 :], dtype=np.float64)
        ret = np.diff(c) / c[:-1]
        vnorm = v[1:] / (np.mean(v[1:]) + 1e-9)
        x = np.stack([ret, vnorm, np.zeros_like(ret)], axis=-1)
        return x[-seq:].astype(np.float32)

    def score(self, closes: list[float], volumes: list[float]) -> dict[str, Any]:
        if self.model is None:
            return {"lstm_active": False, "lstm_p_up": None}
        arr = self.sequence_from_ohlcv(closes, volumes)
        if arr is None:
            return {"lstm_active": False, "lstm_p_up": None}
        with torch.no_grad():
            t = torch.from_numpy(arr).unsqueeze(0)
            logits = self.model(t)
            prob = torch.softmax(logits, dim=-1)[0].numpy()
        return {
            "lstm_active": True,
            "lstm_p_down": float(prob[0]),
            "lstm_p_up": float(prob[1]),
        }
