"""日志底座：统一 `pyknora` 的 handler / 级别 / 形态，并提供结构化事件与请求上下文。

## 为什么需要这个模块

改造前 root logger 没有 handler、`pyknora` 也没设级别，`logger.info(...)` 全部落进
`logging.lastResort`（只有裸 message，且 level=WARNING）：INFO 直接消失，ERROR 也看不出
时间、logger 名与级别。这里一次性给出两种可被 grep / 被采集器解析的形态。

## 两种形态

text（默认，便于 `tail -f`；为了不超 100 列，下面折成两行，实际输出是一行）::

    2026-11-02 17:40:00,123 INFO pyknora.retrieval event=retrieval_completed
    request_id=r-8f2a duration_ms=812

json（`PYKNORA_LOG_FORMAT=json`，采集器可直接 `json.loads`）::

    {"ts": "2026-11-02T17:40:00.123+08:00", "level": "INFO", "logger": "pyknora.retrieval",
     "event": "retrieval_completed", "request_id": "r-8f2a", "duration_ms": 812}

时间戳两种形态都用**本机时区**：text 是 `logging` 的默认 `asctime`（`...,123`），
json 的 `ts` 是带偏移量的 ISO 8601；两者同一时钟，对日志时不用心算时差。

未采集到的数值一律写 `null`，**不写 0**：0 的含义是"测到了且是零"，null 是"没测到"。
把两者混起来，`duration_ms=0` 就会被读成一次极快的检索，而不是数据缺失。

## 隐私红线

- **绝不记录**：`Authorization` 头、API Key、密码、JWT 原文、完整文档原文、
  未脱敏的 Prompt / 用户问题 / 模型回答。
- 需要定位问题时只记可复算的代理量：长度、hash 前缀、条数、耗时、错误码。
- `fields` 只放短值与标识符；任何来自用户或文档的文本进日志前必须先脱敏。
- 本模块**不做**自动脱敏——它无从判断什么算敏感，这条只能由调用点守住。

## 环境变量

- `PYKNORA_LOG_LEVEL`：默认 `INFO`，可用 `logging` 的级别名或数字。
- `PYKNORA_LOG_FORMAT`：`text`（默认）或 `json`。

## 边界

- 只配置 `pyknora` 这一棵 logger，**不碰 root、不碰 `uvicorn.access`**：access log 的
  形状是 uvicorn 的对外契约，动它会连带打断别人的采集规则。
- `propagate` 保持默认的 `True`：不吞掉宿主（pytest 的 `caplog`、进程已有的 root
  handler）已经装好的链路；本模块只保证"本树有自己的 handler，且只有一个"。
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
import traceback
from datetime import datetime

# 本模块只认这一棵 logger 树；`main.py` 里的 `logging.getLogger(__name__)` 是它的子节点。
LOGGER_NAME = "pyknora"

# 给本模块自己的 handler 打的标记：重复 configure 时靠它找到"我们已经装过的那个"，
# 而不是无脑 addHandler（那会让同一条日志打印 N 遍）。测试也靠它定位 handler。
CONSOLE_HANDLER_MARKER = "pyknora_console"

_ENV_LEVEL = "PYKNORA_LOG_LEVEL"
_ENV_FORMAT = "PYKNORA_LOG_FORMAT"
_DEFAULT_LEVEL = "INFO"
_FORMAT_TEXT = "text"
_FORMAT_JSON = "json"

_EVENT_ATTR = "pyknora_event"
_FIELDS_ATTR = "pyknora_fields"
_REQUEST_ID_ATTR = "pyknora_request_id"

# 结构键属于日志骨架，不许被 fields 覆盖：字段名叫 `level` 会把"这条是 ERROR"改成
# "这条是 INFO"，日志就不可信了。撞名直接报错，好过静默改写。
_RESERVED_FIELD_KEYS = frozenset({"event", "level", "logger", "msg", "ts"})

# `log_event` 里区分"没传 request_id"与"显式传了 None"的哨兵。
_UNSET = object()

# 不需要加引号的字符串：logfmt 里裸着写更好读，也更方便 `awk`/`grep`。
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_./:@%+#,-]+$")

# 请求上下文：asyncio 下每个 Task 各自持有一份副本，天然不会串号。
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pyknora_request_id", default=None
)


def _format_value(value: object) -> str:
    """把字段值渲染成 logfmt 风格的一段文本；`None` → `null`（不是 0、也不是空串）。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        if value and _SAFE_TOKEN.match(value):
            return value
        # 带空格 / 引号 / 换行的值必须转义，否则一行日志会被拆成两行、解析直接错位。
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


class _RequestIdFilter(logging.Filter):
    """给每条记录补上 request_id（没有就是 None）。

    挂在 **handler** 而不是 logger 上：logger 自身的 filter 只对"直接经它发出"的记录
    生效，子 logger（`pyknora.api.main` 等）的记录不会经过父 logger 的 filter，
    挂错地方就会让所有子模块的 request_id 恒为 None。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, _REQUEST_ID_ATTR):
            setattr(record, _REQUEST_ID_ATTR, _request_id.get())
        return True


class _TextFormatter(logging.Formatter):
    """text 形态：`<asctime> <LEVEL> <logger> event=... request_id=... k=v ...`。"""

    def format(self, record: logging.LogRecord) -> str:
        parts = [self.formatTime(record, self.datefmt), record.levelname, record.name]
        event = getattr(record, _EVENT_ATTR, None)
        if event is None:
            # 普通 `logger.info("...")`：保留人能直接读的那句话。
            parts.append(record.getMessage())
            bound = getattr(record, _REQUEST_ID_ATTR, None)
            if bound is not None:
                parts.append(f"request_id={_format_value(bound)}")
        else:
            parts.append(f"event={_format_value(event)}")
            parts.append(f"request_id={_format_value(getattr(record, _REQUEST_ID_ATTR, None))}")
            fields: dict[str, object] = getattr(record, _FIELDS_ATTR, None) or {}
            parts.extend(f"{key}={_format_value(value)}" for key, value in fields.items())
        line = " ".join(parts)
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            line = f"{line}\n{self.formatStack(record.stack_info)}"
        return line


class _JsonFormatter(logging.Formatter):
    """json 形态：单行、可 `json.loads`，结构键恒定存在（缺失即 null）。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created)
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, _EVENT_ATTR, None),
            "request_id": getattr(record, _REQUEST_ID_ATTR, None),
        }
        fields: dict[str, object] = getattr(record, _FIELDS_ATTR, None) or {}
        payload.update(fields)
        if payload["event"] is None:
            payload["msg"] = record.getMessage()
        if record.exc_info:
            payload["exc_info"] = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
        # 单行是硬要求：换行由 json.dumps 转义，采集器按行切就不会切坏。
        return json.dumps(payload, ensure_ascii=False, default=str)


_FORMATTERS: dict[str, type[logging.Formatter]] = {
    _FORMAT_TEXT: _TextFormatter,
    _FORMAT_JSON: _JsonFormatter,
}


def _resolve_level(value: str) -> int:
    """`INFO` / `info` / `20` 都收；认不出的直接报错，不静默退化成 INFO。"""
    name = value.strip().upper()
    mapping = logging.getLevelNamesMapping()
    if name in mapping:
        return mapping[name]
    try:
        return int(name)
    except ValueError:
        raise ValueError(
            f"日志级别非法：{value!r}；可用 {', '.join(sorted(mapping))} 或 0-50 的数字"
        ) from None


def _resolve_format(value: str) -> str:
    name = value.strip().lower()
    if name not in _FORMATTERS:
        raise ValueError(f"日志形态非法：{value!r}；只支持 {_FORMAT_TEXT} / {_FORMAT_JSON}")
    return name


def _console_handler(logger: logging.Logger) -> logging.Handler:
    """返回本模块装在 `logger` 上的 handler；没有就装一个（且只装一个）。"""
    for handler in logger.handlers:
        if getattr(handler, CONSOLE_HANDLER_MARKER, False):
            return handler
    handler = logging.StreamHandler()  # 默认写 stderr，和 uvicorn 的日志出口一致
    setattr(handler, CONSOLE_HANDLER_MARKER, True)
    handler.addFilter(_RequestIdFilter())
    logger.addHandler(handler)
    return handler


def configure_logging(level: str | None = None, fmt: str | None = None) -> None:
    """装上（或复用）`pyknora` 的 handler，并设定级别与形态；可重复调用。

    优先级：显式参数 > 环境变量 > 默认（`INFO` / `text`）。
    两者都先校验后落盘：参数非法直接 `ValueError`，此时 logger 保持原样——不会出现
    "配错了但已经改成半截"的状态。

    幂等性：本模块只在 logger 上放**一个**带 `CONSOLE_HANDLER_MARKER` 的 handler，
    重复调用只替换它的 formatter；别人装的 handler 一律不动（我们无法判断它的用途）。
    """
    level_name = level if level is not None else os.environ.get(_ENV_LEVEL, _DEFAULT_LEVEL)
    format_name = fmt if fmt is not None else os.environ.get(_ENV_FORMAT, _FORMAT_TEXT)
    resolved_level = _resolve_level(level_name)
    resolved_format = _resolve_format(format_name)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(resolved_level)
    _console_handler(logger).setFormatter(_FORMATTERS[resolved_format]())


def _caller_logger_name() -> str:
    """取 `log_event` 调用点的模块名，让 text 行能直接看出是哪个子系统。

    栈帧：0 = 本函数，1 = `log_event`，2 = 真正的调用点。调用点不在 `pyknora` 树内
    （脚本、测试）时回落到 `LOGGER_NAME`——否则会凭空造出
    `test_core_logging` 这种只存在于测试进程里的 logger 名。
    """
    try:
        frame = sys._getframe(2)
    except ValueError:  # pragma: no cover - 栈过浅（解释器顶层直接调用）时的兜底
        return LOGGER_NAME
    name = frame.f_globals.get("__name__")
    if isinstance(name, str) and (name == LOGGER_NAME or name.startswith(f"{LOGGER_NAME}.")):
        return name
    return LOGGER_NAME


def bind_request_context(request_id: str) -> contextvars.Token:
    """把 `request_id` 绑到当前上下文（每个 asyncio Task 各自一份），返回复位 token。

    用法见 `api` 层：进入请求时 bind、`finally` 里 reset。只在 `try` 里 bind 而忘了
    reset，同一上下文后续的记录就会带上**别人**的 request_id——比没有更糟。
    """
    return _request_id.set(request_id)


def reset_request_context(token: contextvars.Token) -> None:
    """按 bind 返回的 token 复位。放在 `finally` 里，保证异常路径也能复位。"""
    _request_id.reset(token)


def log_event(event: str, **fields: object) -> None:
    """记一条结构化事件（text 的 `event=... k=v`、json 的同名键）。

    - logger 名取**调用方模块名**：从 `pyknora.services.retrieval.search` 调用，text 行
      里就是 `... INFO pyknora.services.retrieval event=...`，定位子系统不用再猜。
    - `request_id` 自动取 `bind_request_context` 绑定的值；批处理、评测脚本这类没有
      请求上下文的场景可以显式传 `request_id=...` 覆盖（显式值优先）。
    - `fields` 不允许占用 `ts` / `level` / `logger` / `event` / `msg` 这些结构键，
      撞名抛 `TypeError`：静默改写会让日志的级别与时间戳不可信。
    - 隐私：`fields` 只放短值与标识符，原文/用户输入必须先脱敏（见模块 docstring）。
    """
    clashes = sorted(key for key in fields if key in _RESERVED_FIELD_KEYS)
    if clashes:
        raise TypeError(f"log_event 的 fields 不许占用结构键：{', '.join(clashes)}")
    explicit = fields.pop("request_id", _UNSET)
    request_id = _request_id.get() if explicit is _UNSET else explicit
    logging.getLogger(_caller_logger_name()).info(
        event,
        extra={
            _EVENT_ATTR: event,
            _FIELDS_ATTR: fields,
            _REQUEST_ID_ATTR: request_id,
        },
    )
