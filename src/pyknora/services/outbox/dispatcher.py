"""发件箱派发器（S11 的第七步，之前一直是缺的）。

**为什么需要它**：`0001_init.sql:207-209` 已经写明设计——"与业务同事务登记，
dispatcher 至少一次派发"。`services/ingest/publish.py:275-295` 与
`services/kb/service.py:88-105` 也都在事务里往 `outbox` 写了行。但仓库里
**没有任何代码读过 `outbox`**：`dispatched_at` 永远是 NULL，`outbox_pending_idx`
这个部分索引只增不减，"通知下游"这件事从来没有发生过。

这不是"少了一个可选功能"：发布事务的设计前提是"指针翻转的那一刻下游就会被通知"，
前提不成立时，任何依赖事件的消费方（缓存失效、webhook、索引迁移触发）都只能靠轮询
业务表自己发现变化，而轮询的间隔就是脏数据窗口。

**为什么派发器自己不做业务**：它只负责"取未派发的、交给订阅者、成功就打标记"。
订阅者通过 `deliver` 传进来，默认实现只记日志——仓里目前确实还没有第二个订阅者。
这样安排是为了以后接 webhook 时不用改派发逻辑，也不至于假装已经送到了什么东西。

**至少一次，不是恰好一次**（`0001_init.sql:208-209` 的原文承诺）：先调用订阅者、
再打 `dispatched_at`。中间崩溃会导致下一次重复投递同一事件，消费方必须幂等。
反过来（先打标记再派发）会丢事件，那是更坏的错误。

**并发安全**：`FOR UPDATE SKIP LOCKED` 让多个派发进程各领一批，不会抢同一行、
也不会互相阻塞。打完 `dispatched_at` 的行不会再被选中。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

DEFAULT_BATCH: int = 100
"""一批最多取多少行。

取太大：一个事务里串行调用订阅者，锁持有时间随批次线性增长；
取太小：空转次数多。100 是"一次网络往返能带回的合理上限"，不是压测结论。
"""


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """一行待派发事件。字段与 `outbox` 表列一一对应，不做裁剪。"""

    event_id: UUID
    workspace_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


Deliver = Callable[[OutboxEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class DispatchReport:
    """一批派发的结果。`failed` 不为零时那些行仍然是 pending，下一轮会重试。"""

    dispatched: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.dispatched + self.failed


async def log_delivery(event: OutboxEvent) -> None:
    """默认订阅者：只写日志。

    它**不是**占位实现的一部分——它是当前唯一的真实投递方式。仓里没有 Redis、
    没有 webhook、没有下游服务，事件唯一的读者就是运维的日志与 lineage 视角。
    接入真订阅者时传自己的 `deliver`，本模块不用改。
    """
    logger.info(
        "outbox %s aggregate=%s/%s payload=%s",
        event.event_type,
        event.aggregate_type,
        event.aggregate_id,
        event.payload,
    )


def _coerce_payload(raw: object) -> dict[str, Any]:
    """把 jsonb 列读成 dict。

    asyncpg 对 `text()` 查询里的 `jsonb` 不保证反序列化（取决于方言是否注册了
    codec），可能直接给回 JSON 字符串。两种都收，避免派发器因为一个 str 就崩。
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            logger.warning("outbox payload 不是合法 JSON，按空对象处理: %r", raw)
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


async def dispatch_pending(
    session: AsyncSession,
    *,
    deliver: Deliver | None = None,
    limit: int = DEFAULT_BATCH,
) -> DispatchReport:
    """派发一批未派发事件。调用方负责 commit。

    单行失败（订阅者抛异常）不会中断整批：那行保持 pending 等下一轮重试，
    其余行照常派发。整批一起失败会让一个毒事件卡住后面所有事件。
    """
    if limit <= 0:
        return DispatchReport()

    rows = (
        await session.execute(
            text(
                """
                SELECT event_id, workspace_id, aggregate_type, aggregate_id,
                       event_type, payload, created_at
                FROM outbox
                WHERE dispatched_at IS NULL
                ORDER BY created_at, event_id
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
    ).mappings()

    handler = deliver or log_delivery
    dispatched = 0
    failed = 0
    for row in rows:
        event = OutboxEvent(
            event_id=row["event_id"],
            workspace_id=row["workspace_id"],
            aggregate_type=row["aggregate_type"],
            aggregate_id=row["aggregate_id"],
            event_type=row["event_type"],
            payload=_coerce_payload(row["payload"]),
            created_at=row["created_at"],
        )
        try:
            await handler(event)
        except Exception:  # noqa: BLE001 —— 单个订阅者失败不能拖垮整批
            failed += 1
            logger.exception(
                "outbox 派发失败 event_id=%s event_type=%s（保持 pending，下一轮重试）",
                event.event_id,
                event.event_type,
            )
            continue
        await session.execute(
            text("UPDATE outbox SET dispatched_at = :now WHERE event_id = :event_id"),
            {"now": datetime.now(UTC), "event_id": event.event_id},
        )
        dispatched += 1

    return DispatchReport(dispatched=dispatched, failed=failed)
