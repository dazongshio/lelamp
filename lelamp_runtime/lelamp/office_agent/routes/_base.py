from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ApiError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    actor: str
    source_ip: str
    actor_name: str = "本机用户"
    host: str = ""
    forwarded_proto: str = ""

NOT_HANDLED = object()


def exact(server: Any, path: str, ctx: Any, routes: dict[str, str]) -> Any:
    method_name = routes.get(path)
    return NOT_HANDLED if method_name is None else getattr(server, method_name)(ctx)


def exact_payload(server: Any, path: str, payload: dict[str, Any], ctx: Any, routes: dict[str, str]) -> Any:
    method_name = routes.get(path)
    return NOT_HANDLED if method_name is None else getattr(server, method_name)(payload, ctx)
