"""S03：Scope 的 fail-closed 语义。"""

from __future__ import annotations

import dataclasses
from uuid import UUID

import pytest

from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B, Scope

PRINCIPAL = UUID("33333333-3333-3333-3333-333333333333")


def test_owns_none_is_false() -> None:
    """没有记录归属的行不在 scope 内——此前缺失字段被读成"属于所有人"是真实事故。"""
    scope = Scope(workspace_id=WORKSPACE_A, principal_id=PRINCIPAL)
    assert scope.owns(None) is False


def test_owns_self_is_true() -> None:
    scope = Scope(workspace_id=WORKSPACE_A, principal_id=PRINCIPAL)
    assert scope.owns(WORKSPACE_A) is True
    assert scope.owns(PRINCIPAL) is True


def test_owns_other_workspace_is_false() -> None:
    scope = Scope(workspace_id=WORKSPACE_A, principal_id=PRINCIPAL)
    assert scope.owns(WORKSPACE_B) is False


def test_scope_rejects_non_uuid() -> None:
    """身份必须来自已认证 token 解析出的 UUID，字符串一律拒绝。"""
    with pytest.raises(TypeError):
        Scope(workspace_id="ws-a", principal_id=PRINCIPAL)  # type: ignore[arg-type]


def test_scope_is_frozen() -> None:
    scope = Scope(workspace_id=WORKSPACE_A, principal_id=PRINCIPAL)
    with pytest.raises(dataclasses.FrozenInstanceError):
        scope.workspace_id = WORKSPACE_B  # type: ignore[misc]
