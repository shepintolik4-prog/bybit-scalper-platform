from fastapi import Header, HTTPException

from app.config import get_settings


def verify_api_secret(x_api_secret: str | None = Header(default=None, alias="X-API-Secret")) -> None:
    s = get_settings()
    if not x_api_secret or x_api_secret != s.api_secret:
        raise HTTPException(status_code=401, detail="Неверный API секрет")
