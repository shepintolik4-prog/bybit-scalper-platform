"""
Бэктест: walk-forward, реалистичное исполнение (комиссии, slippage, задержка).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import accuracy_score
from sklearn.utils.class_weight import compute_sample_weight

from app.config import get_settings
from app.ml.features import build_feature_frame, feature_vector_last
from app.ml.predictor import FEATURE_ORDER, get_predictor
from app.ml.regime import classify_regime_row
from app.ml.walk_forward import generate_walk_forward_indices
from app.services import bybit_exchange
from app.services.execution_model import apply_execution_price, latency_shift_index
from app.services.risk import default_stops, update_trail


@dataclass
class BacktestSummary:
    symbol: str
    trades: int
    winrate: float
    pnl_pct: float
    max_dd_pct: float
    model_accuracy: float


@dataclass
class RealisticBacktestSummary:
    symbol: str
    trades: int
    winrate: float
    pnl_pct: float
    max_dd_pct: float
    wf_windows: int
    avg_accuracy: float


def _empty_realistic(symbol: str) -> RealisticBacktestSummary:
    return RealisticBacktestSummary(symbol, 0, 0.0, 0.0, 0.0, 0, 0.0)


def run_backtest(symbol: str, bars: int = 800, horizon: int = 3, thresh: float = 0.0004) -> BacktestSummary:
    raw = bybit_exchange.fetch_ohlcv(symbol, "5m", bars)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    feat = build_feature_frame(df)
    if len(feat) < 120:
        return BacktestSummary(symbol, 0, 0.0, 0.0, 0.0, 0.0)
    close = feat["close"].values
    future_ret = (np.roll(close, -horizon) - close) / close
    y = (future_ret > thresh).astype(int)
    y[-horizon:] = 0
    feat_xy = feat.iloc[: len(y)].copy()
    feat_xy["y"] = y
    feat_xy = feat_xy.iloc[:-horizon]
    X = feat_xy[list(FEATURE_ORDER)].values
    y_clean = feat_xy["y"].values
    mask = ~np.isnan(X).any(axis=1)
    X, y_clean = X[mask], y_clean[mask]
    if len(X) < 80:
        return BacktestSummary(symbol, 0, 0.0, 0.0, 0.0, 0.0)
    split = int(len(X) * 0.75)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y_clean[:split], y_clean[split:]
    sw = compute_sample_weight(class_weight="balanced", y=y_train)
    clf = xgb.XGBClassifier(
        n_estimators=120,
        max_depth=5,
        learning_rate=0.06,
        subsample=0.85,
        colsample_bytree=0.85,
        min_child_weight=3,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=42,
    )
    clf.fit(X_train, y_train, sample_weight=sw)
    pred = clf.predict(X_test)
    acc = float(accuracy_score(y_test, pred))

    equity = 10000.0
    peak = equity
    max_dd = 0.0
    wins = 0
    trades = 0
    feat_reset = feat.reset_index(drop=True)
    start_i = split + 50
    s = get_settings()
    for j in range(len(X_test)):
        row_idx = start_i + j
        if row_idx >= len(feat_reset) - 15:
            break
        price = float(feat_reset["close"].iloc[row_idx])
        atr = float(feat_reset["atr14"].iloc[row_idx])
        proba = clf.predict_proba(X_test[j : j + 1])[0]
        side = "buy" if proba[1] >= proba[0] else "sell"
        rd = default_stops(side, price, atr, regime=None)
        margin = max(equity * 0.005, 5.0)
        notional = margin * 5
        entry = price
        hi, lo, trail = entry, entry, None
        exit_px = None
        for k in range(1, 16):
            idx = row_idx + k
            if idx >= len(feat_reset):
                break
            cur = float(feat_reset["close"].iloc[idx])
            hi, lo, trail = update_trail(
                side,
                entry,
                hi,
                lo,
                cur,
                trail,
                rd.trail_trigger_pct,
                rd.trail_offset_pct,
            )
            if side == "buy":
                if cur <= rd.stop_price:
                    exit_px = cur
                    break
                if cur >= rd.take_profit_price:
                    exit_px = cur
                    break
                if trail and cur <= trail:
                    exit_px = cur
                    break
            else:
                if cur >= rd.stop_price:
                    exit_px = cur
                    break
                if cur <= rd.take_profit_price:
                    exit_px = cur
                    break
                if trail and cur >= trail:
                    exit_px = cur
                    break
        if exit_px is None:
            exit_px = float(feat_reset["close"].iloc[min(row_idx + 5, len(feat_reset) - 1)])
        fee = notional * (s.exec_fee_roundtrip_pct / 100.0)
        pnl = (exit_px - entry) / entry * (1 if side == "buy" else -1) * notional - fee
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)
        trades += 1
        if pnl > 0:
            wins += 1
    winrate = wins / trades if trades else 0.0
    pnl_pct = (equity - 10000) / 10000 * 100
    return BacktestSummary(symbol, trades, winrate, pnl_pct, max_dd, acc)


def run_realistic_backtest(symbol: str, bars: int = 1200) -> RealisticBacktestSummary:
    """
    Walk-forward обучение, тест без look-ahead (purge), исполнение с spread/slippage/latency.
    """
    s = get_settings()
    raw = bybit_exchange.fetch_ohlcv(symbol, "5m", bars)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    feat = build_feature_frame(df)
    if len(feat) < s.wf_train_bars + s.wf_test_bars + s.wf_purge_bars + 50:
        return _empty_realistic(symbol)
    close = feat["close"].values
    horizon = 3
    thresh = 0.0004
    future_ret = (np.roll(close, -horizon) - close) / close
    y = (future_ret > thresh).astype(int)
    y[-horizon:] = 0
    X = feat[list(FEATURE_ORDER)].values[: len(feat) - horizon]
    y = y[: len(X)]

    n = len(X)
    windows = generate_walk_forward_indices(
        n,
        train_len=s.wf_train_bars,
        test_len=s.wf_test_bars,
        step=s.wf_step_bars,
        purge=s.wf_purge_bars,
    )
    if not windows:
        return _empty_realistic(symbol)

    equity = 10000.0
    peak = equity
    max_dd = 0.0
    wins = 0
    trades = 0
    accs: list[float] = []
    feat_i = feat.reset_index(drop=True)

    for w in windows:
        Xtr, ytr = X[w.train_start : w.train_end], y[w.train_start : w.train_end]
        Xte, yte = X[w.test_start : w.test_end], y[w.test_start : w.test_end]
        if len(Xtr) < 40 or len(Xte) < 5:
            continue
        sw = compute_sample_weight(class_weight="balanced", y=ytr)
        clf = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.07,
            subsample=0.85,
            colsample_bytree=0.85,
            random_state=42,
        )
        clf.fit(Xtr, ytr, sample_weight=sw)
        pred = clf.predict(Xte)
        accs.append(float(accuracy_score(yte, pred)))

        for j in range(len(Xte)):
            row_idx = w.test_start + j + feat_i.index[0]  # align to feat_i rows
            # map to feat_i position: test indices are relative to X which aligns with feat rows after dropna
            # X row j corresponds to feat.iloc[w.test_start + j]
            ri = w.test_start + j
            if ri >= len(feat_i) - 5:
                continue
            row_idx = ri
            mid = float(feat_i["close"].iloc[row_idx])
            atr = float(feat_i["atr14"].iloc[row_idx])
            proba = clf.predict_proba(Xte[j : j + 1])[0]
            side = "buy" if proba[1] >= proba[0] else "sell"
            lat = latency_shift_index(row_idx, s.exec_latency_bars, len(feat_i))
            mid = float(feat_i["close"].iloc[lat])
            fr = apply_execution_price(mid, side)
            entry = fr.fill_price
            rd = default_stops(side, entry, atr, regime=None)
            margin = max(equity * 0.005, 5.0)
            notional = margin * 5
            hi, lo, trail = entry, entry, None
            exit_px = None
            for k in range(1, 20):
                idx = ri + k
                if idx >= len(feat_i):
                    break
                cur = float(feat_i["close"].iloc[idx])
                hi, lo, trail = update_trail(
                    side,
                    entry,
                    hi,
                    lo,
                    cur,
                    trail,
                    rd.trail_trigger_pct,
                    rd.trail_offset_pct,
                )
                if side == "buy":
                    if cur <= rd.stop_price:
                        exit_px = cur
                        break
                    if cur >= rd.take_profit_price:
                        exit_px = cur
                        break
                    if trail and cur <= trail:
                        exit_px = cur
                        break
                else:
                    if cur >= rd.stop_price:
                        exit_px = cur
                        break
                    if cur <= rd.take_profit_price:
                        exit_px = cur
                        break
                    if trail and cur >= trail:
                        exit_px = cur
                        break
            if exit_px is None:
                exit_px = float(feat_i["close"].iloc[min(ri + 5, len(feat_i) - 1)])
            frx = apply_execution_price(exit_px, "sell" if side == "buy" else "buy")
            exit_fill = frx.fill_price
            fee = notional * (s.exec_fee_roundtrip_pct / 100.0)
            pnl = (exit_fill - entry) / entry * (1 if side == "buy" else -1) * notional - fee
            equity += pnl
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak * 100)
            trades += 1
            if pnl > 0:
                wins += 1

    winrate = wins / trades if trades else 0.0
    pnl_pct = (equity - 10000) / 10000 * 100
    return RealisticBacktestSummary(
        symbol=symbol,
        trades=trades,
        winrate=winrate,
        pnl_pct=pnl_pct,
        max_dd_pct=max_dd,
        wf_windows=len(windows),
        avg_accuracy=float(np.mean(accs)) if accs else 0.0,
    )


def train_and_save(symbol: str, out_dir: str) -> str:
    import os

    raw = bybit_exchange.fetch_ohlcv(symbol, "5m", 2000)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    feat = build_feature_frame(df)
    close = feat["close"].values
    horizon = 3
    thresh = 0.0004
    future_ret = (np.roll(close, -horizon) - close) / close
    y = (future_ret > thresh).astype(int)
    y[-horizon:] = 0
    n = len(y) - horizon
    X = feat[list(FEATURE_ORDER)].values[:n]
    y = y[: len(X)]
    sw = compute_sample_weight(class_weight="balanced", y=y)
    clf = xgb.XGBClassifier(
        n_estimators=180,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.85,
        colsample_bytree=0.85,
        min_child_weight=3,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=42,
    )
    clf.fit(X, y, sample_weight=sw)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "xgboost_signal.json")
    clf.save_model(path)
    return path


def train_meta_and_save(symbol: str, out_dir: str) -> str:
    import os

    from app.ml.meta_filter import MetaFilter

    pred = get_predictor()
    mf = MetaFilter()
    raw = bybit_exchange.fetch_ohlcv(symbol, "5m", 1800)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    feat = build_feature_frame(df)
    close = feat["close"].values
    horizon = 3
    thresh = 0.0004
    future_ret = (np.roll(close, -horizon) - close) / close
    y = (future_ret > thresh).astype(int)
    y[-horizon:] = 0
    X_rows: list[list[float]] = []
    ys: list[int] = []
    step = 3
    for i in range(80, len(feat) - horizon - 1, step):
        sub = feat.iloc[: i + 1]
        feats = feature_vector_last(sub)
        p_down, p_up = pred.predict_proba_row(feats)
        edge = pred.edge_score(p_down, p_up)
        reg = classify_regime_row(sub.iloc[-1]).regime
        row = mf.build_row(feats, reg, edge)[0].tolist()
        X_rows.append(row)
        ys.append(int(y[i]))
    if len(ys) < 80:
        raise ValueError("Недостаточно данных для meta-модели")
    X = np.array(X_rows, dtype=np.float32)
    y = np.array(ys, dtype=np.int32)
    sw = compute_sample_weight(class_weight="balanced", y=y)
    clf = xgb.XGBClassifier(
        n_estimators=120,
        max_depth=4,
        learning_rate=0.08,
        random_state=42,
    )
    clf.fit(X, y, sample_weight=sw)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "meta_xgboost.json")
    clf.save_model(path)
    return path
