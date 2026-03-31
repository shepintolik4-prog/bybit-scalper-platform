"""
Walk-forward validation: скользящие окна, purge gap между train и test (без look-ahead).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WFWindow:
    train_start: int
    train_end: int  # exclusive
    test_start: int
    test_end: int  # exclusive


def generate_walk_forward_indices(
    n_samples: int,
    train_len: int,
    test_len: int,
    step: int,
    purge: int,
) -> list[WFWindow]:
    """
    Генерирует окна [train][purge][test] по времени (индексы по возрастанию времени).
    purge — баров между концом train и началом test, чтобы исключить утечку меток.
    """
    out: list[WFWindow] = []
    i = 0
    while True:
        tr_s = i
        tr_e = tr_s + train_len
        te_s = tr_e + purge
        te_e = te_s + test_len
        if te_e > n_samples:
            break
        out.append(WFWindow(train_start=tr_s, train_end=tr_e, test_start=te_s, test_end=te_e))
        i += step
    return out


def split_train_test_purged(
    n: int,
    train_ratio: float = 0.7,
    purge_bars: int = 5,
    embargo_bars: int = 0,
) -> tuple[slice, slice]:
    """
    Одноразовое разбиение: train, purge, test. embargo_bars — отрезать от конца train (опционально).
    """
    train_end = int(n * train_ratio) - embargo_bars
    test_start = train_end + purge_bars
    return slice(0, max(train_end, 0)), slice(test_start, n)
