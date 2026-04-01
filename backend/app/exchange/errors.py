from __future__ import annotations


class ExchangeError(Exception):
    pass


class ExchangeTemporaryError(ExchangeError):
    """Retryable errors: network, rate limit, transient exchange issues."""


class ExchangeRateLimitError(ExchangeTemporaryError):
    pass


class ExchangeAuthError(ExchangeError):
    pass


class ExchangeBadRequest(ExchangeError):
    pass

