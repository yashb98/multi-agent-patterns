"""Bearer token authentication middleware for FastAPI apps."""

from __future__ import annotations

import os

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from shared.logging_config import get_logger

logger = get_logger(__name__)

PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str):
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return Response(content="Unauthorized", status_code=401)

        provided_token = auth_header[7:]
        if provided_token != self._token:
            return Response(content="Unauthorized", status_code=401)

        return await call_next(request)


def require_auth(app: FastAPI) -> None:
    required = os.environ.get("API_AUTH_REQUIRED", "true").lower()
    if required == "false":
        return
    token = os.environ.get("API_AUTH_TOKEN", "")
    if not token:
        logger.warning("API_AUTH_TOKEN not set — authentication disabled")
        return
    app.add_middleware(BearerAuthMiddleware, token=token)
    logger.info("Bearer auth middleware enabled")
