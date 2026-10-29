"""知识库列表的有签名游标；绑定 Scope，避免跨身份重放。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import Scope
from pyknora.core.settings import settings


@dataclass(frozen=True, slots=True)
class KnowledgeBaseCursor:
    created_at: datetime
    kb_id: UUID


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def encode_cursor(
    scope: Scope,
    *,
    created_at: datetime,
    kb_id: UUID,
    now: datetime | None = None,
) -> str:
    issued_at = now or datetime.now(UTC)
    payload = {
        "workspace_id": str(scope.workspace_id),
        "principal_id": str(scope.principal_id),
        "created_at": created_at.isoformat(),
        "kb_id": str(kb_id),
        "expires_at": (issued_at + timedelta(seconds=settings.cursor_ttl_seconds)).timestamp(),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(settings.cursor_secret.encode(), body, hashlib.sha256).digest()
    return f"{_encode(body)}.{_encode(signature)}"


def decode_cursor(
    scope: Scope,
    cursor: str,
    *,
    now: datetime | None = None,
) -> KnowledgeBaseCursor:
    try:
        body_part, signature_part = cursor.split(".", maxsplit=1)
        body = _decode(body_part)
        signature = _decode(signature_part)
        expected = hmac.new(settings.cursor_secret.encode(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("bad signature")
        payload = json.loads(body)
        if payload["workspace_id"] != str(scope.workspace_id):
            raise ValueError("workspace mismatch")
        if payload["principal_id"] != str(scope.principal_id):
            raise ValueError("principal mismatch")
        current_time = (now or datetime.now(UTC)).timestamp()
        if float(payload["expires_at"]) <= current_time:
            raise ValueError("expired")
        created_at = datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is None:
            raise ValueError("naive timestamp")
        return KnowledgeBaseCursor(created_at=created_at, kb_id=UUID(payload["kb_id"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AppError(ErrorCode.CURSOR_EXPIRED, "invalid or expired cursor") from exc
