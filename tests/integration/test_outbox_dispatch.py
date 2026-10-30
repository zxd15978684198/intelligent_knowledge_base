"""S11 的第七步：发件箱派发。

`0001_init.sql:207-209` 承诺"与业务同事务登记，dispatcher 至少一次派发"。
本轮之前 `outbox` 只被写过、从没被读过，`dispatched_at` 恒为 NULL。
这些用例钉住派发器的四条行为：确实派发、确实打标记、不重复派发、失败保持 pending。
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from pyknora.services.outbox.dispatcher import (
    DEFAULT_BATCH,
    DispatchReport,
    OutboxEvent,
    dispatch_pending,
    log_delivery,
)


async def _insert_event(session, *, payload: str = '{"chunk_count":3}') -> UUID:
    """直接插一行 outbox。表上没有外键，构造事件不需要先造文档。"""
    event_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO outbox (event_id, workspace_id, aggregate_type, aggregate_id,"
            " event_type, payload) VALUES (:e, :w, 'document', :a,"
            " 'document.index_published', CAST(:p AS jsonb))"
        ),
        {"e": event_id, "w": uuid4(), "a": uuid4(), "p": payload},
    )
    return event_id


async def _dispatched_at(session, event_id: UUID):
    return (
        await session.execute(
            text("SELECT dispatched_at FROM outbox WHERE event_id = :e"), {"e": event_id}
        )
    ).scalar_one()


async def test_dispatch_delivers_event_and_marks_dispatched(session):
    seen: list[OutboxEvent] = []

    async def deliver(event: OutboxEvent) -> None:
        seen.append(event)

    event_id = await _insert_event(session)
    report = await dispatch_pending(session, deliver=deliver)

    assert report == DispatchReport(dispatched=1, failed=0)
    assert len(seen) == 1
    event = seen[0]
    assert event.event_id == event_id
    assert event.event_type == "document.index_published"
    assert event.aggregate_type == "document"
    # jsonb 必须被反序列化成 dict：asyncpg 对 text() 查询不保证注册 codec，
    # 派发器两种都收（见 dispatcher._coerce_payload）。
    assert event.payload == {"chunk_count": 3}

    assert await _dispatched_at(session, event_id) is not None


async def test_second_round_does_not_redeliver(session):
    calls: list[UUID] = []

    async def deliver(event: OutboxEvent) -> None:
        calls.append(event.event_id)

    await _insert_event(session)
    first = await dispatch_pending(session, deliver=deliver)
    second = await dispatch_pending(session, deliver=deliver)

    assert first.dispatched == 1
    assert second == DispatchReport(dispatched=0, failed=0)
    assert len(calls) == 1, "已派发的行不得被再次选中"


async def test_failed_delivery_stays_pending(session):
    async def boom(event: OutboxEvent) -> None:
        raise RuntimeError("downstream down")

    event_id = await _insert_event(session)
    report = await dispatch_pending(session, deliver=boom)

    assert report == DispatchReport(dispatched=0, failed=1)
    assert await _dispatched_at(session, event_id) is None, "派发失败不能打标记，否则事件永久丢失"


async def test_one_bad_event_does_not_block_the_batch(session):
    """毒事件只毒它自己：同一批里其它事件照常派发。"""
    delivered: list[UUID] = []

    async def deliver(event: OutboxEvent) -> None:
        if event.payload.get("poison"):
            raise RuntimeError("this one is bad")
        delivered.append(event.event_id)

    bad = await _insert_event(session, payload='{"poison":true}')
    good = await _insert_event(session, payload='{"poison":false}')
    report = await dispatch_pending(session, deliver=deliver)

    assert report == DispatchReport(dispatched=1, failed=1)
    assert delivered == [good]
    assert await _dispatched_at(session, bad) is None


async def test_batch_limit_bounds_one_round(session):
    calls: list[UUID] = []

    async def deliver(event: OutboxEvent) -> None:
        calls.append(event.event_id)

    for _ in range(3):
        await _insert_event(session)

    first = await dispatch_pending(session, deliver=deliver, limit=2)
    second = await dispatch_pending(session, deliver=deliver, limit=2)

    assert first.dispatched == 2
    assert second.dispatched == 1
    assert len(calls) == 3


async def test_limit_zero_is_a_noop(session):
    """`limit<=0` 直接返回，不能把限制拼进 SQL 变成全表派发。"""
    await _insert_event(session)
    assert await dispatch_pending(session, limit=0) == DispatchReport()
    assert await dispatch_pending(session, limit=-5) == DispatchReport()


async def test_default_delivery_is_log_only(session, caplog: pytest.LogCaptureFixture):
    """没有订阅者时默认投递就是写日志——不静默丢事件，也不假装送到了别处。"""
    await _insert_event(session)
    with caplog.at_level(logging.INFO, logger="pyknora.services.outbox.dispatcher"):
        report = await dispatch_pending(session)

    assert report == DispatchReport(dispatched=1, failed=0)
    assert any("document.index_published" in record.getMessage() for record in caplog.records)


def test_default_batch_matches_documented_constant():
    """常量本身也是契约：注释说 100，就断言 100，别让文档与代码漂移。"""
    assert DEFAULT_BATCH == 100
    assert asyncio.iscoroutinefunction(log_delivery)
