"""P0/P1 安全护栏的单元测试：JWT 密钥护栏与"表 owner 绕 RLS"判定。

两条都不需要数据库：一条是 pydantic 的构造期校验，一条是纯函数。放在 `tests/unit`
就是为了让"生产用了默认密钥"这种事故在门禁里 3 秒内暴露，而不是等部署后才发现。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pyknora.api.main import _is_rls_bypassed
from pyknora.core.settings import DEV_CURSOR_SECRET, DEV_JWT_SECRET, Settings


def _kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "env": "dev",
        "jwt_secret": "",
        "cursor_secret": DEV_CURSOR_SECRET,
    }
    base.update(overrides)
    return base


def test_dev_env_allows_default_secrets() -> None:
    """开发环境不配也能跑——这是本地开发体验的底线。"""
    s = Settings(**_kwargs())
    assert s.jwt_secret == ""


def test_production_rejects_empty_jwt_secret() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_kwargs(env="prod"))
    assert "PYKNORA_JWT_SECRET" in str(excinfo.value)


def test_production_rejects_dev_jwt_secret() -> None:
    """关键一条：配了但配成开发默认值，等同没配。"""
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_kwargs(env="prod", jwt_secret=DEV_JWT_SECRET))
    assert "PYKNORA_JWT_SECRET" in str(excinfo.value)


def test_production_accepts_explicit_secrets() -> None:
    s = Settings(**_kwargs(env="prod", jwt_secret="j" * 32, cursor_secret="c" * 32))
    assert s.jwt_secret == "j" * 32
    assert s.cursor_secret == "c" * 32


def test_production_rejects_default_cursor_secret() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_kwargs(env="prod", jwt_secret="j" * 32))
    assert "PYKNORA_CURSOR_SECRET" in str(excinfo.value)


@pytest.mark.parametrize(
    ("rolsuper", "rolbypassrls", "expected"),
    [
        (False, False, False),
        (True, False, True),
        (False, True, True),
        (True, True, True),
    ],
)
def test_rls_bypass_detection(rolsuper: bool, rolbypassrls: bool, expected: bool) -> None:
    assert _is_rls_bypassed(rolsuper=rolsuper, rolbypassrls=rolbypassrls) is expected
