from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_sec: float = 0.35
    max_delay_sec: float = 6.0
    jitter_frac: float = 0.25


def _sleep_with_jitter(delay: float, *, jitter_frac: float) -> None:
    j = float(jitter_frac)
    if j <= 0:
        time.sleep(max(0.0, delay))
        return
    lo = max(0.0, delay * (1.0 - j))
    hi = max(lo, delay * (1.0 + j))
    time.sleep(random.uniform(lo, hi))


def retry_call(fn: Callable[[], T], *, policy: RetryPolicy, should_retry: Callable[[Exception], bool]) -> T:
    last: Exception | None = None
    for attempt in range(1, int(policy.max_attempts) + 1):
        try:
            return fn()
        except Exception as e:
            last = e
            if attempt >= int(policy.max_attempts) or not should_retry(e):
                raise
            backoff = float(policy.base_delay_sec) * (2 ** (attempt - 1))
            delay = min(float(policy.max_delay_sec), backoff)
            _sleep_with_jitter(delay, jitter_frac=float(policy.jitter_frac))
    assert last is not None
    raise last

