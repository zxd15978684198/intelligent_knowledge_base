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

from pyknora.core.scope import WORKSPACE_A, WORKSPACE_B
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


async def test_removed_membership_revokes_existing_token(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """把成员关系删掉，旧令牌下一次请求就必须 403。

    这是撤权的另一条通道，和 `principals.status` 不同：账号还活着，只是不再属于
    这个空间。`find_membership` 每次请求都查 `workspace_memberships`，没有缓存也
    没有令牌吊销列表，所以"删行"就是唯一且立即生效的撤权手段。

    为什么要在 HTTP 层测：`tests/unit/test_auth_a4.py` 把 `load_membership` 换成了
    桩，那条用例证明的是 `get_scope` 的**分支**正确，证明不了 SQL 真能查到行。
    这里不桩任何东西，全靠真库。
    """
    headers = bearer()
    assert (await client.get(KBS, headers=headers)).status_code == 200

    removed = await db_session.execute(
        text(
            """
            DELETE FROM workspace_memberships
            WHERE workspace_id = :ws AND user_id = :uid
            """
        ),
        {"ws": WORKSPACE_A, "uid": WORKSPACE_A},
    )
    await db_session.commit()
    assert removed.rowcount == 1, "测试数据没删掉，后面那条 403 会变成假绿"

    after = await client.get(KBS, headers=headers)
    assert after.status_code == 403, after.text
    assert after.json()["error"]["code"] == "FORBIDDEN"


async def test_header_cannot_select_a_foreign_workspace(client: httpx.AsyncClient) -> None:
    """`X-Workspace-ID` 指向别人的空间 → 403，而不是"切过去了但看不见数据"。

    这条防的是最典型的越权写法：把 header 里的 workspace_id 直接塞进 Scope，
    然后用它去过滤 SQL——那样换了 header 就能读到别人空间的行。
    """
    own = bearer()
    assert (await client.get(KBS, headers=own)).status_code == 200

    response = await client.get(KBS, headers={**own, "X-Workspace-ID": str(WORKSPACE_B)})
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "FORBIDDEN"
