"""D6 第一道闸：真正解析之前的纯逻辑预检。

把「压缩后很小、解压后巨大」（ZIP 炸弹）、「ODF 靠重复声明制造海量行」以及
「页数/行数/幻灯片数爆表」三类资源炸弹挡在内存之外。上传侧的 `_read_bounded`
只卡**压缩后**的 50 MiB，管不到解压后的体积，所以这一层必须有。

三个函数都是**纯同步、纯内存**的：不打开文件、不 import 任何解析库、不起线程或
进程。入参已经是内存里的 `zipfile.ZipFile` 或 `bytes`/`str`，因此可以在认领作业
之后、把字节丢进解析进程池之前随手调用。

**边界语义（三个函数一致，闭区间）**：`actual == limit` **放行**，
`actual > limit` 才抛 :class:`ParseGuardError`。理由：ADR-0008 D6 的措辞是
「超过 200 MiB」「单值上限 10 000」「上限 5 000 行 / 2 000 页 / 2 000 张幻灯片」——
「上限」本身即允许值，越过它才算超限。因此恰好 200 MiB、恰好 10 000 次重复、
恰好 5 000 行都合法，只有 200 MiB + 1 字节、10 001、5 001 才拒。

错误码固定用已存在的 `ErrorCode.PARSE_FAILED`（`details` 带 `limit` / `actual` /
`metric`），**不新增错误码**；`PARSE_TIMEOUT` 属于第二道闸（进程池硬超时），不归这里。

注意：这是**预检**，不是安全边界。`ZipInfo.file_size` 来自归档头、可被伪造，
真正的兜底是第二道闸的进程隔离与硬超时（D6）。
"""

from __future__ import annotations

import re
import zipfile
from typing import Any

from pyknora.core.errors import AppError, ErrorCode

#: ZIP 类格式解压后累计体积上限（200 MiB）。
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
#: ODF `number-rows-repeated` / `number-columns-repeated` 的单值上限。
MAX_ODF_REPEATED = 10_000
#: 单文档行数上限。
MAX_ROWS = 5_000
#: 单文档页数上限。
MAX_PAGES = 2_000
#: 单文档幻灯片数上限。
MAX_SLIDES = 2_000

# ODF 属性带 `table:` 命名空间前缀，但部分生成器会写出别的（或没有）前缀，
# 所以前缀按可选处理、只认属性名本身。值只接受数字，非数字交给后续解析器报错。
_REPEATED_RE = re.compile(
    r"(?:[A-Za-z_][\w.-]*:)?(?P<attr>number-(?:rows|columns)-repeated)"
    r"\s*=\s*[\"'](?P<value>\d+)[\"']"
)


class ParseGuardError(AppError):
    """结构预检不通过。统一 `PARSE_FAILED`，`details` 说明越界的是哪一项。"""

    def __init__(
        self,
        message: str,
        *,
        metric: str,
        limit: int,
        actual: int | str,
    ) -> None:
        super().__init__(
            ErrorCode.PARSE_FAILED,
            message,
            details={"limit": limit, "actual": actual, "metric": metric},
        )


def _digits_gt(raw: str, limit: int) -> bool:
    """比较十进制字符串与整数上限，不建 `int`。

    避免 Python 3.12 对超长数字串（>4300 位）的 `int()` 转换上限直接抛
    `ValueError`：预检函数不该因为输入更离谱而崩掉。
    """
    digits = raw.lstrip("0") or "0"
    cap = str(limit)
    if len(digits) != len(cap):
        return len(digits) > len(cap)
    return digits > cap


def check_zip_size(zf: zipfile.ZipFile) -> None:
    """累加归档内所有条目的 `file_size`，超 200 MiB 抛 `ParseGuardError`。

    判定的是**累计值**而非单个条目：100 个 3 MiB 的小文件同样会顶到 300 MiB。
    边界为闭区间——`total == MAX_UNCOMPRESSED_BYTES` 放行，
    `total > MAX_UNCOMPRESSED_BYTES` 抛错，`details["actual"]` 是真实总和。

    只读归档目录，不解压、不读条目内容。
    """
    total = sum(info.file_size for info in zf.infolist())
    if total > MAX_UNCOMPRESSED_BYTES:
        raise ParseGuardError(
            f"解压后累计体积 {total} 字节，超过上限 {MAX_UNCOMPRESSED_BYTES} 字节",
            metric="uncompressed_bytes",
            limit=MAX_UNCOMPRESSED_BYTES,
            actual=total,
        )


def check_odf_repeated(xml: bytes | str) -> None:
    """扫描 ODF `content.xml` 文本里成对的重复声明，单值超 10 000 抛错。

    覆盖 `number-rows-repeated` 与 `number-columns-repeated`，带不带 `table:`
    前缀都算。用 `re` 而不是 XML 解析器：AGENTS.md §2 禁止直接 `import lxml`
    做 XML 解析，而这里只需要看属性字面量，正则足够且没有实体膨胀风险。

    边界为闭区间——`"10000"` 放行，`"10001"` 抛错。`bytes` 入参按 UTF-8 解码，
    非法字节用替换字符兜住（不影响 ASCII 属性名与数字的匹配）。
    """
    text = xml.decode("utf-8", "replace") if isinstance(xml, bytes) else xml
    for match in _REPEATED_RE.finditer(text):
        raw = match.group("value")
        if not _digits_gt(raw, MAX_ODF_REPEATED):
            continue
        digits = raw.lstrip("0") or "0"
        # 超出 int 安全长度时原样回报，不伪造一个近似数字。
        actual: Any = int(digits) if len(digits) <= 19 else digits
        raise ParseGuardError(
            f"ODF 属性 {match.group('attr')} 的值 {digits} 超过上限 {MAX_ODF_REPEATED}",
            metric=match.group("attr"),
            limit=MAX_ODF_REPEATED,
            actual=actual,
        )


def check_document_limits(
    *,
    rows: int | None = None,
    pages: int | None = None,
    slides: int | None = None,
) -> None:
    """单文档规模上限：5 000 行 / 2 000 页 / 2 000 张幻灯片。

    `None` 表示该维度未知或不适用（例如 PPTX 没有行数），跳过检查。
    边界为闭区间——`rows=5000`、`pages=2000`、`slides=2000` 放行，
    再多 1 就抛错。三项都超时只报第一项（rows → pages → slides）。
    """
    checks: tuple[tuple[str, int | None, int], ...] = (
        ("rows", rows, MAX_ROWS),
        ("pages", pages, MAX_PAGES),
        ("slides", slides, MAX_SLIDES),
    )
    for metric, actual, limit in checks:
        if actual is not None and actual > limit:
            raise ParseGuardError(
                f"文档 {metric} 为 {actual}，超过上限 {limit}",
                metric=metric,
                limit=limit,
                actual=actual,
            )
