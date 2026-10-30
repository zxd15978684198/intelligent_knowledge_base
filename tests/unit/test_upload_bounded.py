"""上传体的有界读取：超限必须在读完之前中断。

挡的是"先 `await file.read()` 再比大小"——那等于把服务端一次分配多少内存的决定权
交给客户端：一个 10GB 的请求会先把内存吃满，然后才回一个 422。
"""

from __future__ import annotations

import pytest

from pyknora.api.v1.documents import _read_bounded
from pyknora.core.errors import AppError, ErrorCode


class _ChunkedFile:
    """按块吐字节的假上传体。

    `size` 模拟 multipart 头里声明的长度（客户端可以不声明或谎报）；
    `reads` 记录实际被调用的次数，用来证明"超限即停"而不是"读完才判"。
    """

    def __init__(self, total: int, *, chunk: int = 8, declared: int | None = None) -> None:
        self.size = declared
        self.reads = 0
        self._left = total
        self._chunk = chunk

    async def read(self, n: int = -1) -> bytes:
        self.reads += 1
        if self._left <= 0:
            return b""
        take = min(self._chunk, self._left)
        self._left -= take
        return b"x" * take


async def test_declared_size_over_limit_is_rejected_before_any_read() -> None:
    """声明长度超限就直接拒，一个字节都不读。"""
    fake = _ChunkedFile(total=100, declared=1_000)

    with pytest.raises(AppError) as exc:
        await _read_bounded(fake, 512)

    assert exc.value.code == ErrorCode.INVALID_REQUEST
    assert fake.reads == 0
    assert exc.value.details["declared_bytes"] == 1_000


async def test_under_limit_reads_to_eof_and_returns_bytes() -> None:
    fake = _ChunkedFile(total=40, declared=None)

    data = await _read_bounded(fake, 512)

    assert data == b"x" * 40
    assert fake.reads > 0


async def test_over_limit_without_declared_size_stops_early() -> None:
    """没声明长度（或谎报）时的兜底：读到超过上限立刻中断，不把整体读进来。"""
    fake = _ChunkedFile(total=10_000, declared=None)

    with pytest.raises(AppError) as exc:
        await _read_bounded(fake, 100)

    assert exc.value.code == ErrorCode.INVALID_REQUEST
    assert exc.value.details["at_least_bytes"] > 100
    # 100 字节上限 / 每块 8 字节 → 第 13 块越界，远小于 10_000/8 次
    assert fake.reads < 20
