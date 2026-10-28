"""可信作用域。身份只来自已认证 token，绝不来自请求体或查询串。

语义红线（见 docs/plans/成员2执行计划.md §4.5）：
- `owns(None) is False`：没有记录归属的行不在 scope 内（fail-closed）。
- `scope` 在所有 repository 签名里是必填第一个参数，无默认值。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Scope:
    """一次请求的可信授权边界。

    workspace_id 由 Auth 从 JWT 解析后写入，请求体里的同名字段一律忽略。
    """

    workspace_id: UUID
    principal_id: UUID

    def owns(self, owner_id: UUID | None) -> bool:
        """判断某行是否落在本 scope 内。None 视为不可见。"""
        if owner_id is None:
            return False
        return owner_id == self.workspace_id or owner_id == self.principal_id

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_id, UUID):
            raise TypeError("workspace_id must be a UUID parsed from a verified token")
        if not isinstance(self.principal_id, UUID):
            raise TypeError("principal_id must be a UUID parsed from a verified token")


WORKSPACE_A = UUID("11111111-1111-1111-1111-111111111111")
WORKSPACE_B = UUID("22222222-2222-2222-2222-222222222222")
