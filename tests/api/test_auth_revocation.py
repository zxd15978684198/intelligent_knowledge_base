"""P0-2 验收：停用账号后，**已签发的旧令牌**必须在下一次请求就失效。

为什么这条非有不可：`decode_token` 只看签名与 `exp`，令牌一旦发出，服务端没有"召回"
通道。`find_membership` 里 join `principals.status` 是唯一的失效点——把它去掉，被停用
的人照样能拿旧令牌把 1 小时 TTL 用完，而且没有任何报错。这条用例就是那个 join 的护栏。

先断言"停用前能用"，再断言"停用后 403"：否则一个恒 403 的坏接口也能让用例通过。
"""

from __future__ import annotations

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pyknora.core.scope import WORKSPACE_A
from tests.api.tokens import bearer

KBS = "/api/v1/knowledge-bases"


async def test_disabled_principal_token_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    headers = bearer()

    before = await client.get(KBS, headers=headers)
    assert before.status_code == 200, before.text

    await db_session.execute(
        text("UPDATE principals SET status = 'disabled' WHERE principal_id = :pid"),
        {"pid": WORKSPACE_A},
    )
    await db_session.commit()

    after = await client.get(KBS, headers=headers)
    assert after.status_code == 403, after.text


async def test_re_enabled_principal_recovers(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """停用不是永久封禁：status 回到 active 后旧令牌应当重新可用。

    这条防止把"停用"实现成"删成员关系"——那会让恢复正常态变成不可逆操作。
    """
    headers = bearer()

    await db_session.execute(
        text("UPDATE principals SET status = 'disabled' WHERE principal_id = :pid"),
        {"pid": WORKSPACE_A},
    )
    await db_session.commit()
    assert (await client.get(KBS, headers=headers)).status_code == 403

    await db_session.execute(
        text("UPDATE principals SET status = 'active' WHERE principal_id = :pid"),
        {"pid": WORKSPACE_A},
    )
    await db_session.commit()
    assert (await client.get(KBS, headers=headers)).status_code == 200
