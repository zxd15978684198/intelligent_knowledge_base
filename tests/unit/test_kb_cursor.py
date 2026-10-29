"""知识库列表游标的签名、作用域绑定和过期语义。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B, Scope
from pyknora.services.kb.cursor import decode_cursor, encode_cursor

KB_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _scope(workspace_id: UUID) -> Scope:
    return Scope(workspace_id=workspace_id, principal_id=workspace_id)


def test_cursor_round_trip() -> None:
    now = datetime(2026, 10, 28, tzinfo=UTC)
    created_at = now - timedelta(hours=1)
    cursor = encode_cursor(_scope(WORKSPACE_A), created_at=created_at, kb_id=KB_ID, now=now)
    decoded = decode_cursor(_scope(WORKSPACE_A), cursor, now=now)
    assert decoded.created_at == created_at
    assert decoded.kb_id == KB_ID


@pytest.mark.parametrize("mode", ["tampered", "cross_scope", "expired"])
def test_invalid_cursor_is_cursor_expired(mode: str) -> None:
    now = datetime(2026, 10, 28, tzinfo=UTC)
    cursor = encode_cursor(_scope(WORKSPACE_A), created_at=now, kb_id=KB_ID, now=now)
    scope = _scope(WORKSPACE_A)
    decode_at = now
    if mode == "tampered":
        cursor = f"x{cursor[1:]}"
    elif mode == "cross_scope":
        scope = _scope(WORKSPACE_B)
    else:
        decode_at = now + timedelta(days=2)

    with pytest.raises(AppError) as exc:
        decode_cursor(scope, cursor, now=decode_at)
    assert exc.value.code == ErrorCode.CURSOR_EXPIRED
