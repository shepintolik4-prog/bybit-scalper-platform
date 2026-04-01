from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import ccxt

from app.core.retry import RetryPolicy, retry_call
from app.exchange.bybit_client import BybitClient
from app.exchange.errors import ExchangeTemporaryError

logger = logging.getLogger("scalper.execution.order_manager")


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    order_id: str | None
    raw: dict[str, Any] | None
    error: str | None = None


class OrderManager:
    """
    Execution v1: idempotent client order IDs + retries on transient errors.
    Partial fill handling/reconciliation will be integrated in engine-migration.
    """

    def __init__(self, client: BybitClient | None = None, *, policy: RetryPolicy | None = None) -> None:
        self._client = client or BybitClient()
        self._policy = policy or RetryPolicy(max_attempts=4, base_delay_sec=0.4, max_delay_sec=5.0, jitter_frac=0.25)

    def _retryable_ccxt(self, e: Exception) -> bool:
        return isinstance(
            e,
            (
                ccxt.NetworkError,
                ccxt.ExchangeNotAvailable,
                ccxt.RequestTimeout,
                ccxt.DDoSProtection,
                ccxt.RateLimitExceeded,
            ),
        ) or "10006" in str(e)

    def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        *,
        params: dict[str, Any] | None = None,
        client_order_id: str | None = None,
    ) -> OrderResult:
        ex = self._client.create_trading_exchange()
        cid = client_order_id or f"cid_{uuid.uuid4().hex[:18]}"
        p = dict(params or {})
        # ccxt Bybit uses orderLinkId in params for client id
        p.setdefault("orderLinkId", cid)

        def _do():
            try:
                o = ex.create_order(symbol, "market", side, amount, None, p)
                oid = str(o.get("id") or "")
                return OrderResult(True, oid or None, o)
            except Exception as e:
                if self._retryable_ccxt(e):
                    raise ExchangeTemporaryError(str(e))
                return OrderResult(False, None, None, error=str(e))

        try:
            return retry_call(_do, policy=self._policy, should_retry=lambda e: isinstance(e, ExchangeTemporaryError))
        except Exception as e:
            logger.warning("create_market_order failed symbol=%s side=%s err=%s", symbol, side, e)
            return OrderResult(False, None, None, error=str(e))

    # Compatibility: keep existing paper execution model behavior
    def apply_execution_price(self, mid_price: float, side: str) -> float:
        from app.services.execution_model import apply_execution_price

        return float(apply_execution_price(mid_price, side))

