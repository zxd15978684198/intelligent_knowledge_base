#!/usr/bin/env python
"""Day3 可信 RAG 的 HTTP-only 评测 runner。

为什么"只走 HTTP"、不 import pyknora 直接调服务层：

- 评测要回答的是「外部调用方实际看到了什么」。`Scope` 只从 JWT 解、越权一律 404
  这两条红线，只有穿过 HTTP 才能被验证；直接调 repo 会绕过 `require_kb_access`，
  deny 类样本就会永远通过——这正是 AGENTS.md §8 第 4 条记的那个坑。
- runner 因此不碰数据库、不依赖进程内状态，换机器只改 `--base-url`。

为什么指标口径写得这么死板（sha1 对齐 gold、分母取实际返回条数、gold 为空返回
None 而不是 0）：

Recall@5 / Precision@5 的分母一旦被顺手改得好看一点，两轮实验的数就不可比了。
报告里的数最终要用来判断「候选策略是不是真的更好」，口径漂移比数值难看严重得多，
所以宁可把「算不出来」显式记成 None 并写明原因，也不补一个 0 进去。

产物（`--out` 目录，默认 `eval/results/<experiment_id>/`）：

- `raw.jsonl`：每样本每轮一行，保留原始响应形状，供失败归因复盘；
- `summary.json`：指标 + per-category + repeats 波动，机器读；
- `report.md`：中文人读，含「样本数小，P95 仅供参考」这类诚实说明。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

# 退出码：2 = 配置/守卫不合格（还没发一个请求），3 = deny 泄漏（安全项一票否决）。
# 把 2 与 3 分开，是因为处置人不同：2 是自己的 yaml 写错了，3 是被测系统有越权漏洞。
EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_CONFIG = 2
EXIT_DENY_LEAK = 3

#: 计划 §3.5 的 15 字段里，runner 真正依赖的那些必须存在。
#: `reranker` / `chat_timeout_s` 不在必需列表：前者本轮恒为 null，后者有默认值，
#: 把它们设成必需只会让「最小可跑配置」更难写，不增加任何判别力。
REQUIRED_FIELDS: tuple[str, ...] = (
    "experiment_id",
    "app_commit",
    "prompt_version",
    "eval_dataset",
    "snapshot",
    "chat_model",
    "chat_base_url",
    "embedding_model_key",
    "embedding_dim",
    "embedding_backend",
    "retrieval_strategy",
    "query_top_k",
    "chat_top_k",
)

REQUIRED_SNAPSHOT_FIELDS: tuple[str, ...] = ("workspace_id", "kb_id", "documents")

#: 允许在两次实验之间变化的主因素字段。`query_top_k` / `chat_top_k` 故意**不在**里面：
#: 它们一变，Recall@5 / Precision@5 的分母就不是同一件事，对照实验立刻失去意义。
PRIMARY_FACTOR_FIELDS: frozenset[str] = frozenset(
    {
        "retrieval_strategy",
        "reranker",
        "candidate_k",
        "retrieval_candidate_k",
        "rrf_k",
        "retrieval_rrf_k",
    }
)

#: 占位符标记。计划 §3.5 的示例 yaml 里写的是 `<git rev-parse HEAD>` / `<用户填>`，
#: 这些字符串非空，只判空会让一次没填完的配置一路跑到发请求才炸。
PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "<",
    ">",
    "rev-parse",
    "todo",
    "tbd",
    "changeme",
    "change-me",
    "fill_me",
    "your-",
    "用户填",
    "实测",
)

#: 退化的拒答判定：`DoneEvent.status` 现在已是必填（见 `schemas/chat.py` 的 `RunStatus`），
#: 但旧 raw 的回放、以及流在 `done` 之前断掉时记录里没有 status，只能靠拒答文案识别。
#: 文案取自 `services/rag/answer.py` 的 `_insufficient_payload`（"知识库中没有检索到…"）。
ABSTAIN_MARKER = "没有检索到"

#: raw 里 results 的 content 只留前 120 字；citations 的 snippet 不截断。
MAX_CONTENT_CHARS = 120

DEFAULT_BASE_URL = "http://127.0.0.1:8010"

#: 引用核验失败原因 → 计划 §3.7 的二级 code。计划那张表列的是示例（原文「如 ...」），
#: 所以这里把三条规则拆成更细的 code，方便下一轮按失败类型统计。
CITATION_CODE_MAP: dict[str, str] = {
    "citation_document_not_in_gold": "CITATION_WRONG_DOC",
    "expect_fact_missing_in_snippet": "CITATION_FACT_MISSING",
    "page_range_inverted": "CITATION_PAGE_INVERTED",
}


class ConfigError(Exception):
    """配置/守卫不合格。调用方统一转成 exit 2，且**未发出任何请求**。"""


class RuntimeFailure(Exception):
    """环境/服务端故障（健康检查不过、登录失败）。转成 exit 1。"""


# ────────────────────────────────────────────────────────────────────────────
# 配置：读取、校验、比对
# ────────────────────────────────────────────────────────────────────────────


def load_yaml(path: Path) -> dict[str, Any]:
    """读一份 yaml；顶层必须是映射，否则后续所有 `cfg["x"]` 都会变成隐式 KeyError。"""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在：{path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件不是合法 yaml：{path}\n{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(
            f"配置文件顶层必须是映射（key: value），实际是 {type(raw).__name__}：{path}"
        )
    return raw


def is_placeholder(value: object) -> bool:
    """空串/纯占位符都算「没填」。

    `app_commit` 是最容易被手填成 `<git rev-parse HEAD>` 的字段，只判 `is None`
    等于放它过去，最后报告里出现一个查不到的 commit。
    """
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return True
        lowered = text.lower()
        return any(marker in lowered for marker in PLACEHOLDER_MARKERS)
    return False


def int_in_range(value: object, low: int, high: int) -> bool:
    """整数区间判定。排除 bool：`True` 在 Python 里也是 int，会被当成 top_k=1。"""
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def validate_config(cfg: dict[str, Any]) -> list[str]:
    """返回所有问题（不是第一个问题）。

    一次列全，是因为改 yaml 的人要拿着一份清单去补，而不是改一处跑一次。
    """
    problems: list[str] = []
    for name in REQUIRED_FIELDS:
        if name not in cfg:
            problems.append(f"缺少必需字段：{name}")
        elif name != "snapshot" and is_placeholder(cfg[name]):
            problems.append(f"必需字段为空或仍是占位符：{name} = {cfg[name]!r}")

    snapshot = cfg.get("snapshot")
    if snapshot is not None and not isinstance(snapshot, dict):
        problems.append(f"snapshot 必须是映射，实际是 {type(snapshot).__name__}")
        snapshot = None

    if isinstance(snapshot, dict):
        for name in REQUIRED_SNAPSHOT_FIELDS:
            if name not in snapshot:
                problems.append(f"缺少必需字段：snapshot.{name}")
            elif name != "documents" and is_placeholder(snapshot[name]):
                problems.append(f"必需字段为空或仍是占位符：snapshot.{name} = {snapshot[name]!r}")
        documents = snapshot.get("documents")
        if not isinstance(documents, list):
            problems.append("snapshot.documents 必须是列表（没有文档也要写 []）")
        else:
            for index, document in enumerate(documents):
                if not isinstance(document, dict):
                    problems.append(f"snapshot.documents[{index}] 必须是映射")
                elif is_placeholder(document.get("document_id")):
                    problems.append(f"snapshot.documents[{index}].document_id 缺失或是占位符")

    # top_k 的两个上界来自真实契约，不是评测自定义：/query 是 MAX_TOP_K=50、
    # /chat 的 ChatRequest 是 le=20。这里先拦成 exit 2，否则跑到一半才 422，
    # 前面那半轮的 latency 全部作废。
    query_top_k = cfg.get("query_top_k")
    if not is_placeholder(query_top_k) and not int_in_range(query_top_k, 1, 50):
        problems.append(f"query_top_k 必须是 1..50 的整数（契约 MAX_TOP_K），实际 {query_top_k!r}")
    chat_top_k = cfg.get("chat_top_k")
    if not is_placeholder(chat_top_k) and not int_in_range(chat_top_k, 1, 20):
        problems.append(f"chat_top_k 必须是 1..20 的整数（ChatRequest.top_k），实际 {chat_top_k!r}")

    embedding_dim = cfg.get("embedding_dim")
    if not is_placeholder(embedding_dim) and not int_in_range(embedding_dim, 1, 1 << 20):
        problems.append(f"embedding_dim 必须是正整数，实际 {embedding_dim!r}")

    eval_dataset = cfg.get("eval_dataset")
    if not is_placeholder(eval_dataset) and not isinstance(eval_dataset, str):
        problems.append(f"eval_dataset 必须是路径字符串，实际 {eval_dataset!r}")

    return problems


def flatten_config(value: object, prefix: str = "") -> dict[str, Any]:
    """把嵌套配置摊平成 `a.b.c` 的键，供逐字段比对。

    `documents` 是列表，摊到元素级别只会让差异报告变成天书（下标一一对应），
    所以列表整体序列化成一个值：整块相同才算相同，这正好是公平性守卫要的语义。
    """
    if isinstance(value, dict):
        flat: dict[str, Any] = {}
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            flat.update(flatten_config(item, child))
        return flat
    if isinstance(value, list):
        return {prefix: json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)}
    return {prefix: value}


def build_diff(left: dict[str, Any], right: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """逐字段差异，按字段名排序（排序是为了让输出稳定可比，不是为了美观）。"""
    flat_left = flatten_config(left)
    flat_right = flatten_config(right)
    diff: list[tuple[str, Any, Any]] = []
    for key in sorted(set(flat_left) | set(flat_right)):
        left_value = flat_left.get(key, "<缺失>")
        right_value = flat_right.get(key, "<缺失>")
        if left_value != right_value:
            diff.append((key, left_value, right_value))
    return diff


def compare_configs(left_path: Path, right_path: Path) -> tuple[bool, list[str]]:
    """公平性守卫：两份配置必须**恰好一个主因素**不同。

    完全相同没有实验意义（跑一遍等于复读）；差两个主因素则无法归因。这是计划
    §5.3/§5.4 的核心约束，写成代码就是为了不让「顺手多改一个参数」混进对照轮。
    """
    left = load_yaml(left_path)
    right = load_yaml(right_path)
    for label, cfg, path in (("A", left, left_path), ("B", right, right_path)):
        problems = validate_config(cfg)
        if problems:
            return False, [f"{label}（{path}）配置不合格：", *problems]

    diff = build_diff(left, right)
    if not diff:
        return False, ["两份配置完全相同：对照实验至少要有 1 个主因素不同，否则跑两遍是复读。"]

    primary = [item for item in diff if item[0].split(".")[-1] in PRIMARY_FACTOR_FIELDS]
    others = [item for item in diff if item[0].split(".")[-1] not in PRIMARY_FACTOR_FIELDS]

    if others:
        lines = ["以下字段不是允许变化的主因素，对照实验里必须逐字相同："]
        lines += [f"  - {key}: A={lv!r} / B={rv!r}" for key, lv, rv in others]
        return False, lines

    if len(primary) > 1:
        lines = [f"差异涉及 {len(primary)} 个主因素，无法归因（只允许恰好 1 个）："]
        lines += [f"  - {key}: A={lv!r} / B={rv!r}" for key, lv, rv in primary]
        return False, lines

    key, lv, rv = primary[0]
    return True, [f"唯一不同的主因素：{key}（A={lv!r} → B={rv!r}）"]


# ────────────────────────────────────────────────────────────────────────────
# 评测集
# ────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class EvalSample:
    """一条评测样本。字段名照计划 §3.3 的契约，不照讲义。"""

    sample_id: str
    category: str
    expect: str
    query: str
    workspace_id: str | None
    kb_id: str | None
    deny_kb_id: str | None
    expect_facts: list[str] = field(default_factory=list)
    gold_chunks: list[dict[str, Any]] = field(default_factory=list)
    notes: str | None = None

    @property
    def gold_sha1_8(self) -> list[str]:
        return [item["content_sha1_8"] for item in self.gold_chunks if item.get("content_sha1_8")]

    @property
    def gold_document_ids(self) -> list[str]:
        return [item["document_id"] for item in self.gold_chunks if item.get("document_id")]


def load_eval_set(path: Path) -> list[EvalSample]:
    """逐行读 jsonl。空行跳过，坏行直接报错——坏行静默跳过会让「20 条」变成「18 条」。"""
    if not path.exists():
        raise ConfigError(
            f"评测集不存在：{path}（不带 --config 的 --dry-run 不需要它；"
            "带 --config 的 --dry-run 与真跑都必须给）"
        )
    samples: list[EvalSample] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"评测集第 {line_no} 行不是合法 JSON：{exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"评测集第 {line_no} 行必须是对象")
        sample_id = raw.get("id") or raw.get("sample_id")
        if not sample_id:
            raise ConfigError(f"评测集第 {line_no} 行缺少 id")
        expect = raw.get("expect")
        if expect not in {"answer", "abstain", "deny"}:
            raise ConfigError(
                f"评测集 {sample_id} 的 expect 必须是 answer/abstain/deny，实际 {expect!r}"
            )
        if not raw.get("query"):
            raise ConfigError(f"评测集 {sample_id} 缺少 query")
        # deny 样本的 kb_id 本来就该是 null（资料在 B 区），所以它走 deny_kb_id。
        # 在读入阶段就拦下「deny 却没有 deny_kb_id」：跑到请求时才发现，只会拼出
        # 一个没意义的 404，而不是「越权被拦」。
        if expect == "deny" and not raw.get("deny_kb_id"):
            raise ConfigError(f"评测集 {sample_id} 是 deny 样本但没有 deny_kb_id")
        samples.append(
            EvalSample(
                sample_id=str(sample_id),
                category=str(raw.get("category") or "uncategorized"),
                expect=str(expect),
                query=str(raw["query"]),
                workspace_id=raw.get("workspace_id"),
                kb_id=raw.get("kb_id"),
                deny_kb_id=raw.get("deny_kb_id"),
                expect_facts=[str(item) for item in (raw.get("expect_facts") or [])],
                gold_chunks=list(raw.get("gold_chunks") or []),
                notes=raw.get("notes"),
            )
        )
    if not samples:
        raise ConfigError(f"评测集是空的：{path}")
    return samples


def resolve_dataset_path(raw: str, config_path: Path) -> Path:
    """配置里的相对路径按「仓库根」写，但 runner 可能从别的目录启动。

    依次试：当前目录 → 配置文件所在目录 → 其上一级。找到就用；都找不到就返回第一个
    候选，让报错指向大家约定的那个位置，而不是一个没人认得的绝对路径。
    """
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    tries = [Path.cwd() / candidate, config_path.parent / candidate]
    tries.append(config_path.parent.parent / candidate)
    for item in tries:
        if item.exists():
            return item
    return tries[0]


# ────────────────────────────────────────────────────────────────────────────
# HTTP 调用
# ────────────────────────────────────────────────────────────────────────────


def sha1_8(text: str) -> str:
    """与计划 §3.3 约定的 `content_sha1_8` 同一算法：sha1(正文).hexdigest()[:8]。

    只做对齐，不做安全用途；`usedforsecurity` 不传是因为这里对可读性没有收益。
    """
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def truncate(text: str, limit: int = MAX_CONTENT_CHARS) -> str:
    """raw 里 results 的正文只留前 120 字：raw.jsonl 是要人肉翻的，整段正文会把
    失败样本淹掉。但 citations 的 snippet **不截断**——引用核验要在里面找
    `expect_facts`，截断会让一个正确引用被误判成「事实不在片段里」。
    """
    return text if len(text) <= limit else text[:limit]


def error_code_of(payload: Any) -> str | None:
    """从统一错误体 `{"error":{"code":...}}` 里取 code；取不到返回 None。"""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            return str(code) if code else None
    return None


def body_leaks(body: str) -> bool:
    """越权响应的「泄漏」判据：出现任何片段正文/引用，或干脆不是 JSON。

    只认 `results` / `citations` / `"content"` 键：标准 404 错误体是
    `{"error":{...},"request_id":...}`，不含这三样，正常 404 不会被误判。
    """
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return bool(body.strip())
    if isinstance(payload, dict) and (payload.get("results") or payload.get("citations")):
        return True
    return '"content"' in body


def safe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, Any]]:
    """把 `event:` / `data:` 组成事件。

    服务端一次事件只发一行 data，但按 SSE 规范允许多行 data，所以这里拼接而不是
    只取第一行——解析器比服务端宽松一档，服务端改格式时才不会静默丢事件。
    """
    event = ""
    data_lines: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data_lines:
                yield event or "message", _decode_sse_data(data_lines)
            event, data_lines = "", []
            continue
        if line.startswith(":"):
            continue
        field_name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field_name == "event":
            event = value
        elif field_name == "data":
            data_lines.append(value)
    if data_lines:
        yield event or "message", _decode_sse_data(data_lines)


def _decode_sse_data(data_lines: Sequence[str]) -> Any:
    text = "\n".join(data_lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


@dataclass(slots=True)
class QueryOutcome:
    """一次 /query 的观测值。"""

    http_status: int
    results: list[dict[str, Any]]
    latency_ms: float
    error_code: str | None
    body_leaked: bool
    retrieval_strategy: str | None = None


@dataclass(slots=True)
class SampleRun:
    """一次样本执行的全部观测值。真实路径与 dry-run 共用它，指标代码只有一份。"""

    results: list[dict[str, Any]] = field(default_factory=list)
    answer_text: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    status: str = "UNKNOWN"
    invalid_citations: int = 0
    query_latency_ms: float = 0.0
    chat_ttft_ms: float | None = None
    chat_total_ms: float | None = None
    query_http_status: int | None = None
    chat_http_status: int | None = None
    query_body_leaked: bool = False
    chat_body_leaked: bool = False
    error_code: str | None = None
    degraded: dict[str, str] = field(default_factory=dict)
    deny: dict[str, Any] | None = None
    response_strategy: str | None = None
    response_prompt_version: str | None = None


class EvalClient:
    """一个 httpx client 跑完全部样本。

    为什么不每条样本新建：连接复用让 latency 更接近生产（真实调用方也是长连接），
    否则 20 条 × 3 轮 × 2 端点的握手开销会把 P95 抬成「TLS 握手 P95」。
    """

    def __init__(self, base_url: str, *, chat_timeout_s: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=chat_timeout_s, write=30.0, pool=10.0)
        )
        self._token: str | None = None

    async def __aenter__(self) -> EvalClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, Any]:
        try:
            response = await self._client.get(f"{self.base_url}/health")
        except httpx.HTTPError as exc:
            raise RuntimeFailure(
                f"连不上 {self.base_url}/health（{type(exc).__name__}: {exc}）。"
                "真跑前先起服务；只想自验用 --dry-run。"
            ) from exc
        if response.status_code != 200:
            raise RuntimeFailure(
                f"/health 返回 {response.status_code}：{truncate(response.text, 300)}"
            )
        payload = safe_json(response.text)
        if isinstance(payload, dict) and payload.get("status") not in (None, "ok"):
            raise RuntimeFailure(f"/health 状态不是 ok：{payload}")
        return payload if isinstance(payload, dict) else {}

    async def login(self, username: str, password: str) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self.base_url}/api/v1/auth/login",
                json={"username": username, "password": password},
            )
        except httpx.HTTPError as exc:
            raise RuntimeFailure(f"登录请求失败：{type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            payload = safe_json(response.text)
            raise RuntimeFailure(
                f"/api/v1/auth/login 返回 {response.status_code}："
                f"{payload or truncate(response.text, 300)}"
            )
        payload = safe_json(response.text)
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not token:
            raise RuntimeFailure("登录响应里没有 access_token")
        # token 只进内存（计划 §3.8 第 2 条）：落盘的那份迟早进 git。
        self._token = str(token)
        return payload if isinstance(payload, dict) else {}

    def _headers(self, workspace_id: str | None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        # 头名以 `api/deps.py` 的 alias 为准（`X-Workspace-ID`）。token 里的默认空间
        # 未必是样本要的那个空间，所以显式覆盖，而不是依赖 JWT。
        if workspace_id:
            headers["X-Workspace-ID"] = workspace_id
        return headers

    async def query(
        self, *, kb_id: str, workspace_id: str | None, query: str, top_k: int
    ) -> QueryOutcome:
        url = f"{self.base_url}/api/v1/knowledge-bases/{kb_id}/query"
        started = time.perf_counter()
        try:
            response = await self._client.post(
                url, json={"query": query, "top_k": top_k}, headers=self._headers(workspace_id)
            )
        except httpx.HTTPError as exc:
            return QueryOutcome(
                http_status=0,
                results=[],
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error_code=type(exc).__name__,
                body_leaked=False,
            )
        latency_ms = (time.perf_counter() - started) * 1000.0
        payload = safe_json(response.text)
        if response.status_code != 200:
            return QueryOutcome(
                http_status=response.status_code,
                results=[],
                latency_ms=latency_ms,
                error_code=error_code_of(payload),
                body_leaked=body_leaks(response.text),
            )
        raw_results = payload.get("results") if isinstance(payload, dict) else None
        rows = [_result_row(item) for item in (raw_results or []) if isinstance(item, dict)]
        # `retrieval_strategy` 由 `QueryResponse` 回显（schemas/query.py 的默认
        # `baseline`）。抓到它就按响应归因；没抓到（服务端版本较旧）才由
        # build_raw_record 回退到配置，并用 `retrieval_strategy_source` 标出来源。
        strategy = payload.get("retrieval_strategy") if isinstance(payload, dict) else None
        return QueryOutcome(
            http_status=response.status_code,
            results=rows,
            latency_ms=latency_ms,
            error_code=None,
            body_leaked=body_leaks(response.text),
            retrieval_strategy=str(strategy) if strategy else None,
        )

    async def chat(
        self, *, kb_id: str, workspace_id: str | None, query: str, top_k: int
    ) -> SampleRun:
        """打流式 /chat，解析 retrieval / token / done / error 四类事件。

        TTFT 记「第一个 token 事件到达」，总时长记到流结束（收到 done）。两者必须
        分开：首字快但收尾拖长的模型，用户体感与总耗时排名可以完全相反。
        """
        run = SampleRun()
        url = f"{self.base_url}/api/v1/knowledge-bases/{kb_id}/chat"
        started = time.perf_counter()
        pieces: list[str] = []
        async with self._client.stream(
            "POST",
            url,
            json={"query": query, "top_k": top_k},
            headers=self._headers(workspace_id),
        ) as response:
            run.chat_http_status = response.status_code
            if response.status_code != 200:
                body = (await response.aread()).decode("utf-8", errors="replace")
                run.error_code = error_code_of(safe_json(body))
                run.chat_body_leaked = body_leaks(body)
                run.chat_total_ms = (time.perf_counter() - started) * 1000.0
                return run
            # 非 deny 场景下 200 是正常的；body_leaked 只对 deny 有意义，这里按
            # 「200 且流里出现过内容」保守记 True，deny 判定那边才用它。
            run.chat_body_leaked = True
            async for event, data in iter_sse(response.aiter_lines()):
                if event == "retrieval":
                    if isinstance(data, dict):
                        run.citations = [
                            item for item in (data.get("citations") or []) if isinstance(item, dict)
                        ]
                        run.degraded = dict(data.get("degraded") or {})
                        strategy = data.get("retrieval_strategy")
                        if strategy:
                            run.response_strategy = str(strategy)
                elif event == "token":
                    if run.chat_ttft_ms is None:
                        run.chat_ttft_ms = (time.perf_counter() - started) * 1000.0
                    if isinstance(data, dict):
                        pieces.append(str(data.get("text") or ""))
                elif event == "done":
                    if isinstance(data, dict):
                        raw_status = data.get("status")
                        if raw_status:
                            run.status = str(raw_status)
                        run.invalid_citations = int(data.get("invalid_citations") or 0)
                        prompt_version = data.get("prompt_version")
                        if prompt_version:
                            run.response_prompt_version = str(prompt_version)
                elif event == "error" and isinstance(data, dict):
                    run.error_code = str(data.get("code") or "STREAM_ERROR")
        run.chat_total_ms = (time.perf_counter() - started) * 1000.0
        run.answer_text = "".join(pieces)
        return run


def _result_row(item: dict[str, Any]) -> dict[str, Any]:
    """把一条 `RetrievedChunk` 压成 raw 记录的形状。

    `active_document_version_id` 从读契约原样带出：`schemas/query.py` 的
    `RetrievedChunk` 有这个字段，它是「命中片段是否来自现行版本」的唯一凭据。
    旧版 runner 曾把它恒置 None，理由是"读契约没有这个字段"——那个前提已经不成立，
    继续置 None 会把一条旧版本引用洗成"无版本信息"。字段缺失时仍落 None，绝不凭
    `index_version` 编一个 UUID 出来。
    """
    content = str(item.get("content") or "")
    return {
        "chunk_id": item.get("chunk_id"),
        "document_id": item.get("document_id"),
        "chunk_ordinal": item.get("chunk_ordinal"),
        "active_document_version_id": item.get("active_document_version_id"),
        "index_version": item.get("index_version"),
        "rrf_score": item.get("rrf_score"),
        "vector_rank": item.get("vector_rank"),
        "keyword_rank": item.get("keyword_rank"),
        "content_sha1_8": sha1_8(content),
        "content": truncate(content),
    }


async def run_sample(client: EvalClient, sample: EvalSample, cfg: dict[str, Any]) -> SampleRun:
    """跑一条样本：先 /query 取检索，再 /chat 取回答（deny 类也打两端点）。"""
    snapshot = cfg["snapshot"]
    workspace_id = sample.workspace_id or str(snapshot["workspace_id"])
    kb_id = (
        sample.deny_kb_id if sample.expect == "deny" else (sample.kb_id or str(snapshot["kb_id"]))
    )
    if not kb_id:
        # load_eval_set 已经挡住 deny 的情形；answer 类没写 kb_id 时才走到这里。
        raise ConfigError(f"样本 {sample.sample_id} 既没有 kb_id 也没有 deny_kb_id")

    outcome = await client.query(
        kb_id=str(kb_id),
        workspace_id=workspace_id,
        query=sample.query,
        top_k=int(cfg["query_top_k"]),
    )
    chat_run = await client.chat(
        kb_id=str(kb_id),
        workspace_id=workspace_id,
        query=sample.query,
        top_k=int(cfg["chat_top_k"]),
    )

    run = SampleRun(
        results=outcome.results,
        answer_text=chat_run.answer_text,
        citations=chat_run.citations,
        status=chat_run.status,
        invalid_citations=chat_run.invalid_citations,
        query_latency_ms=outcome.latency_ms,
        chat_ttft_ms=chat_run.chat_ttft_ms,
        chat_total_ms=chat_run.chat_total_ms,
        query_http_status=outcome.http_status,
        chat_http_status=chat_run.chat_http_status,
        query_body_leaked=outcome.body_leaked,
        chat_body_leaked=chat_run.chat_body_leaked,
        error_code=outcome.error_code or chat_run.error_code,
        degraded=chat_run.degraded,
        response_strategy=outcome.retrieval_strategy or chat_run.response_strategy,
        response_prompt_version=chat_run.response_prompt_version,
    )

    if sample.expect == "deny":
        # 安全项：两个端点都必须 403/404 且响应体不含片段。任一端点 200 由 main
        # 汇总成 exit 3——「计入平均」会让 2 条漏 1 条仍显示 50%，那是安全口径的
        # 自我欺骗。
        query_denied = outcome.http_status in (403, 404)
        chat_denied = chat_run.chat_http_status in (403, 404)
        run.deny = {
            "query_http_status": outcome.http_status,
            "chat_http_status": chat_run.chat_http_status,
            "query_body_leaked": outcome.body_leaked,
            "chat_body_leaked": chat_run.chat_body_leaked,
            "intercepted": query_denied
            and chat_denied
            and not outcome.body_leaked
            and not chat_run.chat_body_leaked,
            "returned_200": 200 in (outcome.http_status, chat_run.chat_http_status),
        }
        if run.deny["intercepted"]:
            run.status = "DENIED"
        elif run.deny["returned_200"]:
            # 200 与「404 但状态码对不上」要分开写：前者是越权漏洞，后者更像路由
            # 写错，处置方式完全不同，raw 里混成一个标签就查不出来了。
            run.status = "DENY_LEAKED"
        else:
            run.status = "DENY_NOT_404"
    return run


# ────────────────────────────────────────────────────────────────────────────
# raw 记录
# ────────────────────────────────────────────────────────────────────────────


def build_raw_record(
    sample: EvalSample,
    run: SampleRun,
    *,
    repeat: int,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """一行 raw。字段名对齐任务书；`gold_*` 与 `*_source` 是给复盘用的旁证。

    `retrieval_strategy` / `prompt_version` 优先取**响应里回显的值**（`QueryResponse`
    与 `DoneEvent` 现在都带这两个字段），没有才退回配置：退回时必须显式——用
    `*_source` 标出这个数是「服务端说的」还是「配置声明的」，否则报告里写「候选
    策略那一轮」可能其实是配置写错了。
    """
    strategy = run.response_strategy or cfg.get("retrieval_strategy")
    prompt_version = run.response_prompt_version or cfg.get("prompt_version")
    return {
        "sample_id": sample.sample_id,
        "category": sample.category,
        "expect": sample.expect,
        "repeat": repeat,
        "latency_ms": round(run.query_latency_ms, 2),
        "results": run.results,
        "answer_text": run.answer_text,
        "citations": run.citations,
        "status": run.status,
        "invalid_citations": run.invalid_citations,
        "retrieval_strategy": strategy,
        "retrieval_strategy_source": "response" if run.response_strategy else "config",
        "prompt_version": prompt_version,
        "prompt_version_source": "response" if run.response_prompt_version else "config",
        "query_http_status": run.query_http_status,
        "chat_http_status": run.chat_http_status,
        "chat_ttft_ms": None if run.chat_ttft_ms is None else round(run.chat_ttft_ms, 2),
        "chat_total_ms": None if run.chat_total_ms is None else round(run.chat_total_ms, 2),
        "error_code": run.error_code,
        "degraded": run.degraded,
        "deny": run.deny,
        "gold_sha1_8": sample.gold_sha1_8,
        "gold_document_ids": sample.gold_document_ids,
        "expect_facts": sample.expect_facts,
        "notes": sample.notes,
    }


# ────────────────────────────────────────────────────────────────────────────
# 指标
# ────────────────────────────────────────────────────────────────────────────


def percentile_p95(values: Iterable[float]) -> float | None:
    """nearest-rank 的 P95：排序后取 `ceil(0.95n)-1`。

    用整数运算 `(95*n+99)//100` 求 ceil，避免浮点误差把 n=20 算成 20（那样 P95
    会静默变成 max）。不做线性插值：20 条样本插出来的「95 分位」其实是两个点的
    加权平均，看着精确反而误导；nearest-rank 至少取到的是真实观测值。
    """
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return None
    rank = (95 * len(ordered) + 99) // 100
    return ordered[min(max(rank - 1, 0), len(ordered) - 1)]


#: 明确「不是拒答」的 status。前四个是 `schemas/chat.py::RunStatus` 的全部非
#: `ABSTAINED` 取值（这也是计划 §3.5 写死的五状态）；`ANSWERED` 是早期计划稿里的
#: 旧写法（服务端从未发出过），保留只为回放旧 raw；`ERROR`/`DENY_*` 是 runner 自己
#: 合成的标签（请求失败、越权拦截），不是 SSE 的 status。
NON_ABSTAIN_STATUSES: frozenset[str] = frozenset(
    {
        "COMPLETED",
        "TIMEOUT",
        "BUDGET_EXCEEDED",
        "FAILED",
        "ANSWERED",
        "ERROR",
        "DENIED",
        "DENY_NOT_404",
        "DENY_LEAKED",
    }
)


def is_abstained(record: dict[str, Any]) -> bool:
    """无答案拒答的判定：status 优先，status 缺失时才退回拒答文案。

    契约以 `DoneEvent.status` 为准（`schemas/chat.py::RunStatus`，五个取值）：
    `ABSTAINED` 记拒答成功；`COMPLETED`/`TIMEOUT`/`BUDGET_EXCEEDED`/`FAILED` 一律
    记否——不能因为文案里恰好有「没有检索到」，就把一条超时或失败的样本洗成拒答
    成功。status 缺失（旧 raw、流在 `done` 之前断开）才退化为文案匹配。
    """
    status = str(record.get("status") or "UNKNOWN").upper()
    if status == "ABSTAINED":
        return True
    if status in NON_ABSTAIN_STATUSES:
        return False
    return ABSTAIN_MARKER in str(record.get("answer_text") or "")


def recall_at_5(record: dict[str, Any]) -> float | None:
    gold = set(record.get("gold_sha1_8") or [])
    if not gold:
        return None
    hits = {row.get("content_sha1_8") for row in record.get("results") or []} & gold
    return len(hits) / len(gold)


def precision_at_5(record: dict[str, Any]) -> float | None:
    gold = set(record.get("gold_sha1_8") or [])
    if not gold:
        return None
    results = record.get("results") or []
    if not results:
        # 契约口径：返回 0 条记 0.0，不是 None。分母写 min(5, len) 时 0 条会变成
        # 0/0，显式落 0.0 才不会让「一条都没召回」从平均里消失。
        return 0.0
    hits = sum(1 for row in results if row.get("content_sha1_8") in gold)
    return hits / min(5, len(results))


def page_range_ok(page_no: Any, page_end_no: Any) -> bool:
    """页号关系：`page_no <= page_end_no`，两者都为 null 允许；缺一个即不通过。

    缺一个时无法判断引用是否指回同一段，记成通过等于默认放行。
    """
    if page_no is None and page_end_no is None:
        return True
    if page_no is None or page_end_no is None:
        return False
    try:
        return int(page_no) <= int(page_end_no)
    except (TypeError, ValueError):
        return False


@dataclass(slots=True)
class CitationAudit:
    """引用核验的分子/分母/跳过数。分开记是为了在报告里说清「为什么是 None」。"""

    verified: int = 0
    checked: int = 0
    skipped: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float | None:
        return None if self.checked == 0 else self.verified / self.checked

    def add(self, reason: str | None) -> None:
        self.checked += 1
        if reason is None:
            self.verified += 1
        else:
            self.reasons.append(reason)


def audit_citations(record: dict[str, Any]) -> CitationAudit:
    """按计划 §3.6 的三条规则核验引用。

    `expect=abstain` / `deny` 的样本不进分母：它们的引用本身就是「不该存在的东西」，
    由拒答准确率与权限拦截率各自负责，混进引用准确率只会互相掩盖。
    没有 gold 文档集或没有 `expect_facts` 的 answer 样本无从核验，记 skipped 而不是
    猜——猜出来的分母最后会被当成「引用质量」写进报告。
    """
    audit = CitationAudit()
    if record.get("expect") != "answer":
        return audit
    gold_docs = set(record.get("gold_document_ids") or [])
    facts = [fact for fact in (record.get("expect_facts") or []) if fact]
    citations = record.get("citations") or []
    if not gold_docs or not facts:
        audit.skipped += len(citations)
        return audit
    for citation in citations:
        if not isinstance(citation, dict):
            audit.skipped += 1
            continue
        document_id = str(citation.get("document_id") or "")
        snippet = str(citation.get("snippet") or "")
        if document_id not in gold_docs:
            audit.add("citation_document_not_in_gold")
        elif not any(fact in snippet for fact in facts):
            audit.add("expect_fact_missing_in_snippet")
        elif not page_range_ok(citation.get("page_no"), citation.get("page_end_no")):
            audit.add("page_range_inverted")
        else:
            audit.add(None)
    return audit


def deny_intercepted(record: dict[str, Any]) -> bool:
    deny = record.get("deny")
    return bool(isinstance(deny, dict) and deny.get("intercepted"))


def mean_or_none(values: Sequence[float]) -> float | None:
    return None if not values else sum(values) / len(values)


def compute_metrics(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """一轮内的指标。返回数值 + 各自的分母，供跨轮聚合与报告共用。"""
    recalls = [value for value in (recall_at_5(item) for item in records) if value is not None]
    precisions = [
        value for value in (precision_at_5(item) for item in records) if value is not None
    ]

    audits = [audit_citations(item) for item in records]
    verified = sum(audit.verified for audit in audits)
    checked = sum(audit.checked for audit in audits)
    skipped = sum(audit.skipped for audit in audits)

    abstain_samples = [item for item in records if item.get("expect") == "abstain"]
    deny_samples = [item for item in records if item.get("expect") == "deny"]

    return {
        "recall_at_5": mean_or_none(recalls),
        "precision_at_5": mean_or_none(precisions),
        "citation_accuracy": None if checked == 0 else verified / checked,
        "abstain_accuracy": mean_or_none(
            [1.0 if is_abstained(item) else 0.0 for item in abstain_samples]
        ),
        "deny_interception": mean_or_none(
            [1.0 if deny_intercepted(item) else 0.0 for item in deny_samples]
        ),
        "retrieval_p95_ms": percentile_p95(
            item.get("latency_ms") for item in records if item.get("latency_ms") is not None
        ),
        "chat_ttft_p95_ms": percentile_p95(
            item.get("chat_ttft_ms") for item in records if item.get("chat_ttft_ms") is not None
        ),
        "chat_total_p95_ms": percentile_p95(
            item.get("chat_total_ms") for item in records if item.get("chat_total_ms") is not None
        ),
        "counts": {
            # `samples` 是 raw 行数（含 repeats），`unique_samples` 才是评测集条数。
            # 报告开头写「样本数」必须用后者：repeats=3 时把 60 行写成「60 条样本」
            # 会让样本量的诚实说明直接失效。
            "samples": len(records),
            "unique_samples": len({item.get("sample_id") for item in records}),
            "recall_samples": len(recalls),
            "precision_samples": len(precisions),
            "citations_checked": checked,
            "citations_verified": verified,
            "citations_skipped": skipped,
            "abstain_samples": len(abstain_samples),
            "deny_samples": len(deny_samples),
            "abstain_used_text_fallback": sum(
                1
                for item in abstain_samples
                if str(item.get("status") or "UNKNOWN").upper() not in {"ABSTAINED", "ANSWERED"}
            ),
        },
    }


METRIC_DEFINITIONS: dict[str, str] = {
    "recall_at_5": "命中 gold 的条数 / gold 条数；gold 为空记 None 且不计入平均",
    "precision_at_5": "命中条数 / min(5, 返回条数)；返回 0 条记 0.0",
    "citation_accuracy": "通过规则核验的引用数 / 被核验引用总数（仅 answer 类样本）",
    "abstain_accuracy": "no_evidence 样本中判为 ABSTAINED 的比例（status 缺失时退回拒答文案）",
    "deny_interception": "deny 样本中两端点均 403/404 且响应体无片段的比例（安全项，不参与平均）",
    "retrieval_p95_ms": "单次 /query 端到端延迟的 P95（nearest-rank；样本少时仅供参照）",
    "chat_ttft_p95_ms": "/chat 首个 token 到达耗时的 P95",
    "chat_total_p95_ms": "/chat 到 done 事件的总耗时 P95",
}

METRIC_ORDER: tuple[str, ...] = tuple(METRIC_DEFINITIONS)


def aggregate(records: Sequence[dict[str, Any]], *, repeats: int) -> dict[str, Any]:
    """跨轮聚合：每个指标报均值 + min/max。

    `repeats=1` 时 min/max 与 value 相等，报告会注明「波动无意义」——把 min==max
    读成「非常稳定」是最常见的误读。
    """
    per_repeat_metrics: list[dict[str, Any]] = []
    for repeat in range(1, repeats + 1):
        rows = [item for item in records if int(item.get("repeat", 1)) == repeat]
        per_repeat_metrics.append(compute_metrics(rows))

    metrics: dict[str, Any] = {}
    for name in METRIC_ORDER:
        values = [float(block[name]) for block in per_repeat_metrics if block.get(name) is not None]
        metrics[name] = {
            "value": mean_or_none(values),
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "per_repeat": [
                None if block.get(name) is None else round(float(block[name]), 4)
                for block in per_repeat_metrics
            ],
            "n": len(values),
            "definition": METRIC_DEFINITIONS[name],
        }

    categories = sorted({str(item.get("category")) for item in records})
    per_category: dict[str, Any] = {}
    for category in categories:
        rows = [item for item in records if str(item.get("category")) == category]
        block = compute_metrics(rows)
        per_category[category] = {
            key: None if block[key] is None else round(float(block[key]), 4) for key in METRIC_ORDER
        }
        per_category[category]["samples"] = len({item.get("sample_id") for item in rows})

    return {
        "metrics": metrics,
        "per_category": per_category,
        "per_repeat": per_repeat_metrics,
        "counts": compute_metrics(records)["counts"],
        "repeats": repeats,
    }


def collect_failures(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """失败样本清单，二级 code 尽量对齐计划 §3.7。

    自动归类只做信号明确的那几类；真正的归因仍要人看 raw.jsonl。这里的价值是让
    下一轮的 05-失败样本记录不必从 20 条里手工找——先给候选，再人工确认。
    """
    failures: list[dict[str, Any]] = []
    for record in records:
        expect = record.get("expect")
        codes: list[str] = []
        detail: list[str] = []

        if record.get("error_code") and expect != "deny":
            # deny 样本的 404 本来就带 error_code=NOT_FOUND，把它算成「请求失败」
            # 等于把安全项的正确答案记成缺陷；deny 的异常状态在下面单独判。
            codes.append("REQUEST_FAILED")
            detail.append(f"error_code={record['error_code']}")

        if expect == "deny":
            deny = record.get("deny") or {}
            statuses = (deny.get("query_http_status"), deny.get("chat_http_status"))
            if deny.get("returned_200"):
                codes.append("FORBIDDEN_LEAK")
                detail.append("deny 样本返回 200")
            elif not deny.get("intercepted"):
                if not all(status in (403, 404) for status in statuses):
                    codes.append("REQUEST_FAILED")
                codes.append("DENY_NOT_404")
                detail.append(f"query={statuses[0]} chat={statuses[1]}")
        elif expect == "abstain":
            if not is_abstained(record):
                codes.append("OVER_RETRIEVAL" if record.get("citations") else "EVIDENCE_GATE_MISS")
                detail.append("无证据题却给了实质回答")
        else:
            recall = recall_at_5(record)
            if recall is not None and recall < 1.0:
                codes.append("NO_HIT" if not record.get("results") else "GOLD_MISS")
                detail.append(f"recall@5={recall:.2f}")
            if record.get("invalid_citations"):
                codes.append("INVALID_CITATION")
                detail.append(f"invalid_citations={record['invalid_citations']}")
            audit = audit_citations(record)
            if audit.checked and audit.verified < audit.checked:
                codes.extend(sorted({CITATION_CODE_MAP[reason] for reason in audit.reasons}))
                detail.append(f"引用核验 {audit.verified}/{audit.checked}")
            facts = record.get("expect_facts") or []
            answer = str(record.get("answer_text") or "")
            if facts and answer and not any(fact in answer for fact in facts):
                codes.append("FACT_MISSING")
                detail.append("expect_facts 未出现在回答正文")

        if codes:
            failures.append(
                {
                    "sample_id": record.get("sample_id"),
                    "category": record.get("category"),
                    "expect": expect,
                    "repeat": record.get("repeat"),
                    "codes": sorted(set(codes)),
                    "detail": "；".join(detail),
                    "retrieval_strategy": record.get("retrieval_strategy"),
                }
            )
    return failures


# ────────────────────────────────────────────────────────────────────────────
# 产物
# ────────────────────────────────────────────────────────────────────────────


def write_raw_jsonl(path: Path, records: Sequence[dict[str, Any]]) -> None:
    """一行一条，UTF-8 不转义——raw 是给人翻的，\\u4e2d 那种转义没法 grep。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def fmt_metric(value: Any) -> str:
    return "—" if value is None else f"{float(value):.3f}"


def config_table(cfg: dict[str, Any]) -> str:
    """全字段表（计划 §3.9 第 1 条：版本记录要能逐字段对回去）。"""
    lines = ["| 字段 | 值 |", "| --- | --- |"]
    for key, value in flatten_config(cfg).items():
        lines.append(f"| `{key}` | {value} |")
    return "\n".join(lines)


def render_report(summary: dict[str, Any]) -> str:
    """中文人读报告。只描述「看到了什么」，不写「该怎么改」（改法属计划 §4）。"""
    metrics = summary["metrics"]
    counts = summary["counts"]
    lines: list[str] = [
        "# Day3 评测报告（自动生成，勿手改）",
        "",
        f"- experiment_id：`{summary['experiment_id']}`",
        f"- app_commit：`{summary['app_commit']}`",
        f"- prompt_version：`{summary['prompt_version']}`",
        f"- retrieval_strategy：`{summary['retrieval_strategy']}`",
        f"- base_url：`{summary['base_url']}`",
        f"- 生成时间：{summary['generated_at']}",
        f"- 样本数：{counts['unique_samples']}，repeats={summary['repeats']}，"
        f"raw 行数 {summary['raw_lines']}",
        f"- embedding_backend：`{summary['config'].get('embedding_backend')}`"
        f" / model `{summary['config'].get('embedding_model_key')}`"
        f" / dim {summary['config'].get('embedding_dim')}",
        "",
        "## 1 指标",
        "",
        "| 指标 | 值 | min | max | 分母/样本数 | 定义 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    denominators = {
        "recall_at_5": counts["recall_samples"],
        "precision_at_5": counts["precision_samples"],
        "citation_accuracy": counts["citations_checked"],
        "abstain_accuracy": counts["abstain_samples"],
        "deny_interception": counts["deny_samples"],
        "retrieval_p95_ms": counts["samples"],
        "chat_ttft_p95_ms": counts["samples"],
        "chat_total_p95_ms": counts["samples"],
    }
    for name in METRIC_ORDER:
        block = metrics[name]
        lines.append(
            f"| {name} | {fmt_metric(block['value'])} | {fmt_metric(block['min'])} | "
            f"{fmt_metric(block['max'])} | {denominators[name]} | {block['definition']} |"
        )

    lines += [
        "",
        "## 2 per-category 明细",
        "",
        "| 类别 | 样本 | Recall@5 | Precision@5 | 引用准确率 | 拒答准确率 | 拦截率 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for category, block in summary["per_category"].items():
        lines.append(
            f"| {category} | {block['samples']} | {fmt_metric(block['recall_at_5'])} | "
            f"{fmt_metric(block['precision_at_5'])} | {fmt_metric(block['citation_accuracy'])} | "
            f"{fmt_metric(block['abstain_accuracy'])} | {fmt_metric(block['deny_interception'])} |"
        )

    lines += ["", "## 3 失败样本（自动归类，需人工复核）", ""]
    if summary["failures"]:
        lines += ["| 样本 | 类别 | repeat | 二级 code | 依据 |", "| --- | --- | --- | --- | --- |"]
        for item in summary["failures"]:
            lines.append(
                f"| {item['sample_id']} | {item['category']} | {item['repeat']} | "
                f"{' / '.join(item['codes'])} | {item['detail']} |"
            )
    else:
        lines.append("本轮没有自动识别出失败样本。**这不等于没有失败**：自动归类只看几条硬信号。")

    lines += ["", "## 4 版本记录（配置全字段）", "", config_table(summary["config"]), ""]

    lines += ["## 5 诚实说明（读结论前先看这节）", ""]
    lines.extend(f"- {warning}" for warning in summary["warnings"])
    lines += [
        "",
        "## 6 产物",
        "",
        "| 文件 | 内容 |",
        "| --- | --- |",
        "| `raw.jsonl` | 每样本每轮一行，含原始 results/citations/answer_text |",
        "| `summary.json` | 上述指标 + per-category + repeats 波动（机器读） |",
        "| `report.md` | 本文件 |",
        "",
    ]
    return "\n".join(lines)


def build_warnings(
    cfg: dict[str, Any],
    *,
    repeats: int,
    limit: int | None,
    counts: dict[str, Any],
    app_commit_mismatch: bool,
) -> list[str]:
    """诚实说明。宁可报告里多几行丑话，也别让人把 4 条样本的 P95 当性能结论。"""
    warnings: list[str] = [
        f"样本数小（本轮 {counts['unique_samples']} 条，raw {counts['samples']} 行），"
        "P95 仅供参考："
        "20 条样本的 95 分位基本等于「第二慢的那条」，不要当性能基线。",
    ]
    if repeats <= 1:
        warnings.append("repeats=1：min/max 与值相同，**波动无意义**，不能据此说「结果稳定」。")
    else:
        warnings.append(f"repeats={repeats}：min/max 是同一配置多次运行的极值，不是置信区间。")
    if limit is not None:
        warnings.append(f"使用了 --limit={limit}：本轮指标只覆盖前 {limit} 条，不可用于对外报告。")
    if cfg.get("embedding_backend") == "hashing":
        warnings.append(
            "embedding_backend=hashing：与真向量分布不同，**结果不得写入对外报告**"
            "（计划 §3.5 的 G6 约束），只能用于跑通流水线。"
        )
    if app_commit_mismatch:
        warnings.append(
            "app_commit 与当前 `git rev-parse HEAD` 不一致：配置可能是手填的旧值，"
            "版本记录不可信（计划 §3.5 要求跑前自动写入）。"
        )
    if counts["abstain_used_text_fallback"]:
        warnings.append(
            f"{counts['abstain_used_text_fallback']} 条无证据样本的 status 缺失，"
            f"已退化为「回答含「{ABSTAIN_MARKER}」」判定——权宜口径，done.status 落地后要改回。"
        )
    if counts["citations_skipped"]:
        warnings.append(
            f"{counts['citations_skipped']} 条引用因 gold 文档集或 expect_facts 为空而无法核验，"
            "已记为 skipped，未计入引用准确率。"
        )
    if counts["deny_samples"] == 0:
        warnings.append("本轮没有 deny 样本：权限拦截率是 None，安全项未被验证。")
    return warnings


# ────────────────────────────────────────────────────────────────────────────
# dry-run：不需要服务器、不需要评测集
# ────────────────────────────────────────────────────────────────────────────


def dry_run_config() -> dict[str, Any]:
    """内置最小配置。字段与真配置同名，只为让 dry-run 走同一条校验路径。"""
    return {
        "experiment_id": "dry-run-example",
        "app_commit": "dry-run-not-a-real-commit",
        "prompt_version": "dry-run-prompt-v0",
        "eval_dataset": "eval/eval_set_v0.1.jsonl",
        "snapshot": {
            "workspace_id": "11111111-1111-1111-1111-111111111111",
            "kb_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "documents": [
                {
                    "document_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
                    "active_document_version_id": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
                    "active_build_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
                    "active_index_version": 1,
                    "chunks": 12,
                }
            ],
        },
        "chat_model": "dry-run-model",
        "chat_base_url": "http://127.0.0.1:9999/v1",
        "chat_timeout_s": 60.0,
        "embedding_model_key": "BAAI/bge-small-zh-v1.5",
        "embedding_dim": 512,
        "embedding_backend": "local",
        "retrieval_strategy": "baseline",
        "query_top_k": 5,
        "chat_top_k": 5,
        "reranker": None,
    }


#: dry-run 的假语料。gold 的 `content_sha1_8` 由 `sha1_8()` 现算，这样「sha1 对齐」
#: 这条逻辑真的被跑到了，而不是抄一个恒等的假数。
DRY_GOLD_TEXT = "一线城市出差住宿标准上限 500 元/晚，超支部分自理。"
DRY_DISTRACTOR_TEXT = "机房巡检每周一次，记录写入运维台账，与差旅报销无关。"


def dry_run_samples() -> list[EvalSample]:
    """四条样本覆盖全部指标分支：命中/未命中/拒答/越权，外加一条坏引用。"""
    return [
        EvalSample(
            sample_id="dry-fact-01",
            category="fact",
            expect="answer",
            query="一线城市出差住宿每晚最多报销多少？",
            workspace_id="11111111-1111-1111-1111-111111111111",
            kb_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            deny_kb_id=None,
            expect_facts=["500 元/晚"],
            gold_chunks=[
                {
                    "corpus": "基本财务管理制度.pdf",
                    "document_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
                    "chunk_ordinal": 12,
                    "content_sha1_8": sha1_8(DRY_GOLD_TEXT),
                }
            ],
            notes="命中金标准；引用含 1 条通过 + 1 条文档不属于 gold",
        ),
        EvalSample(
            sample_id="dry-fact-02",
            category="fact",
            expect="answer",
            query="年度审计报告的提交期限是几天？",
            workspace_id=None,
            kb_id=None,
            deny_kb_id=None,
            expect_facts=["30 天"],
            gold_chunks=[
                {
                    "corpus": "基本财务管理制度.pdf",
                    "document_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
                    "chunk_ordinal": 40,
                    "content_sha1_8": sha1_8("年度审计报告应于次年 3 月底前提交。"),
                }
            ],
            notes="gold 未命中，用来验证 GOLD_MISS / NO_HIT 归类",
        ),
        EvalSample(
            sample_id="dry-nov-01",
            category="no_evidence",
            expect="abstain",
            query="公司年会在哪办？",
            workspace_id=None,
            kb_id=None,
            deny_kb_id=None,
            expect_facts=[],
            gold_chunks=[],
            notes="status 故意留空，验证拒答文案退化判定",
        ),
        EvalSample(
            sample_id="dry-deny-01",
            category="deny",
            expect="deny",
            query="财务内部预算里今年市场投入多少？",
            workspace_id=None,
            kb_id=None,
            deny_kb_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            expect_facts=[],
            gold_chunks=[],
            notes="A 区令牌问 B 区库，两端点都应 404",
        ),
    ]


def dry_run_runs() -> list[SampleRun]:
    """内置假响应，形状严格照 `schemas/query.py` / `schemas/chat.py`。"""
    gold_row = {
        "chunk_id": "c0000000-0000-0000-0000-000000000001",
        "document_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "chunk_ordinal": 12,
        "index_version": 2,
        "rrf_score": 0.0325,
        "vector_rank": 1,
        "keyword_rank": 2,
        "content": DRY_GOLD_TEXT,
    }
    distractor_row = {
        "chunk_id": "c0000000-0000-0000-0000-000000000002",
        "document_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "chunk_ordinal": 31,
        "index_version": 2,
        "rrf_score": 0.0161,
        "vector_rank": 2,
        "keyword_rank": None,
        "content": DRY_DISTRACTOR_TEXT,
    }
    good_citation = {
        "chunk_id": "c0000000-0000-0000-0000-000000000001",
        "document_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "page_no": 3,
        "page_end_no": 3,
        "snippet": gold_row["content"],
    }
    bad_citation = {
        "chunk_id": "c0000000-0000-0000-0000-000000000009",
        "document_id": "99999999-9999-9999-9999-999999999999",
        "page_no": 4,
        "page_end_no": 2,
        "snippet": "这段来自别的文档，且页号倒置。",
    }
    return [
        SampleRun(
            results=[_result_row(gold_row), _result_row(distractor_row)],
            answer_text="一线城市住宿标准上限为 500 元/晚 [资料 1]。",
            citations=[good_citation, bad_citation],
            status="ANSWERED",
            invalid_citations=0,
            query_latency_ms=42.0,
            chat_ttft_ms=180.0,
            chat_total_ms=900.0,
            query_http_status=200,
            chat_http_status=200,
            query_body_leaked=False,
            chat_body_leaked=False,
        ),
        SampleRun(
            results=[_result_row(distractor_row)],
            answer_text="根据资料，审计报告在次年 3 月底前提交 [资料 1]。",
            citations=[],
            status="ANSWERED",
            invalid_citations=1,
            query_latency_ms=38.5,
            chat_ttft_ms=210.0,
            chat_total_ms=1100.0,
            query_http_status=200,
            chat_http_status=200,
            query_body_leaked=False,
            chat_body_leaked=False,
        ),
        SampleRun(
            results=[],
            answer_text="知识库中没有检索到相关资料，暂时无法回答。",
            citations=[],
            # 故意不给 status：验证「退化判定」这条路真的被执行到。
            status="UNKNOWN",
            invalid_citations=0,
            query_latency_ms=12.0,
            chat_ttft_ms=95.0,
            chat_total_ms=140.0,
            query_http_status=200,
            chat_http_status=200,
        ),
        SampleRun(
            results=[],
            answer_text="",
            citations=[],
            status="DENIED",
            query_latency_ms=8.0,
            query_http_status=404,
            chat_http_status=404,
            deny={
                "query_http_status": 404,
                "chat_http_status": 404,
                "query_body_leaked": False,
                "chat_body_leaked": False,
                "intercepted": True,
                "returned_200": False,
            },
        ),
    ]


def build_summary(
    records: Sequence[dict[str, Any]],
    *,
    cfg: dict[str, Any],
    repeats: int,
    base_url: str,
    limit: int | None,
    app_commit_mismatch: bool,
    deny_leak: bool,
) -> dict[str, Any]:
    aggregated = aggregate(records, repeats=repeats)
    warnings = build_warnings(
        cfg,
        repeats=repeats,
        limit=limit,
        counts=aggregated["counts"],
        app_commit_mismatch=app_commit_mismatch,
    )
    if deny_leak:
        warnings.insert(
            0,
            "**存在 deny 样本返回 200 或泄漏片段：权限拦截率不参与平均，"
            "本轮整体判失败（exit 3）。**",
        )
    return {
        "experiment_id": cfg.get("experiment_id"),
        "app_commit": cfg.get("app_commit"),
        "prompt_version": cfg.get("prompt_version"),
        "retrieval_strategy": cfg.get("retrieval_strategy"),
        "base_url": base_url,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "repeats": repeats,
        "raw_lines": len(records),
        "metrics": aggregated["metrics"],
        "per_category": aggregated["per_category"],
        "per_repeat": aggregated["per_repeat"],
        "counts": aggregated["counts"],
        "deny_leak": deny_leak,
        "failures": collect_failures(records),
        "warnings": warnings,
        "config": cfg,
    }


def print_eval_plan(
    cfg: dict[str, Any],
    samples: Sequence[EvalSample],
    *,
    config_path: Path,
    dataset_path: Path,
    repeats: int,
    base_url: str,
    limit: int | None,
) -> None:
    """打印「真跑会发哪些请求」的计划，一个字节都不发。

    这是 `--dry-run --config` 的核心：配置与评测集任何一处对不上，都要在起服务
    之前看见——尤其是 `expect` 与 kb 的配对（deny 样本必须走 `deny_kb_id`，否则
    跑出来的是 404 还是"越权被拦"根本分不清）。样本里那个历史遗留的 `top_k`
    不打印：计划 §3.5 已把口径改成配置级的 `query_top_k` / `chat_top_k`，打印一个
    实际不生效的数只会让人以为本次请求按它走。
    """
    snapshot = cfg.get("snapshot") if isinstance(cfg.get("snapshot"), dict) else {}
    default_kb = snapshot.get("kb_id")
    default_workspace = snapshot.get("workspace_id")
    query_top_k = cfg.get("query_top_k")
    chat_top_k = cfg.get("chat_top_k")
    limit_note = f"，--limit={limit}" if limit is not None else ""

    print("=" * 78)
    print("[dry-run] 不连服务器；只读配置与评测集，下面每条都是「真跑会发的请求」计划。")
    print(f"[dry-run] 配置：{config_path}")
    print(f"[dry-run] 评测集：{dataset_path}（显示 {len(samples)} 条{limit_note}）")
    print(
        f"[dry-run] experiment_id={cfg.get('experiment_id')} "
        f"strategy={cfg.get('retrieval_strategy')} "
        f"top_k: query={query_top_k} chat={chat_top_k} "
        f"repeats={repeats} base_url={base_url}"
    )
    if not snapshot:
        # validate_config 目前放行 `snapshot: null`（现状缺口，单测已钉住）；真跑会
        # 在 run_sample 里以 TypeError 炸掉，且不是 exit 2 的配置错误。这里提前说，
        # 但不改变校验结论——改校验会让那条「钉住现状」的单测变红。
        print("[dry-run] 警告：配置没有 snapshot；样本未写 kb/workspace 时将没有缺省值。")
    print("=" * 78)
    for index, sample in enumerate(samples, start=1):
        if sample.expect == "deny":
            target = f"deny_kb_id={sample.deny_kb_id}（越权探针：/query 与 /chat 都必须 403/404）"
        else:
            target = f"kb_id={sample.kb_id or default_kb}"
        workspace = sample.workspace_id or default_workspace
        print(
            f"\n[{index}/{len(samples)}] {sample.sample_id}  "
            f"category={sample.category} expect={sample.expect}"
        )
        print(f"  query: {sample.query}")
        print(f"  target: {target}")
        print(f"  workspace={workspace}  top_k: query={query_top_k} chat={chat_top_k}")
        print(
            f"  gold_chunks={len(sample.gold_chunks)} gold_sha1_8={sample.gold_sha1_8} "
            f"expect_facts={sample.expect_facts}"
        )
        if sample.notes:
            print(f"  notes: {sample.notes}")
    print("\n" + "=" * 78)


def run_dry_run(
    *,
    config_path: Path | None = None,
    eval_set_path: Path | None = None,
    repeats: int = 1,
    base_url: str = DEFAULT_BASE_URL,
    limit: int | None = None,
) -> int:
    """不碰网络、不发请求：给了 --config 先打印真实评测计划，再走内置样本自验。

    两种模式：

    - 不带 `--config`：旧语义，只用内置样本与内置假响应验证「记录 → 指标 → 产物」
      三条路径没坏；
    - 带 `--config`：额外校验该配置、读它指向的评测集（或 `--eval-set`），逐条打印
      真跑会发的请求。仍然不需要登录凭据，也不需要被测服务在跑——没有模型、没有
      API 时也能把配置与评测集的问题在起服务之前暴露出来。

    自验段**始终**用内置配置与内置样本：把真配置塞进假数据算出来的指标没有意义，
    报告里一旦出现这种数，就会被当成这一轮的真实结果。
    """
    self_test_cfg = dry_run_config()
    self_problems = validate_config(self_test_cfg)
    if self_problems:
        print("[dry-run] 内置配置居然没通过校验，这是 runner 的 bug：")
        for problem in self_problems:
            print(f"  - {problem}")
        return EXIT_RUNTIME

    if config_path is not None:
        cfg = load_yaml(config_path)
        problems = validate_config(cfg)
        if problems:
            print(f"[dry-run] 配置 {config_path} 没通过校验（共 {len(problems)} 处）：")
            for problem in problems:
                print(f"  - {problem}")
            return EXIT_CONFIG
        raw_dataset = str(cfg.get("eval_dataset") or "")
        dataset_path = eval_set_path or resolve_dataset_path(raw_dataset, config_path)
        samples = load_eval_set(dataset_path)
        if limit is not None:
            samples = samples[:limit]
        print_eval_plan(
            cfg,
            samples,
            config_path=config_path,
            dataset_path=dataset_path,
            repeats=repeats,
            base_url=base_url,
            limit=limit,
        )
        print("\n[dry-run] 评测计划打印完毕：未发任何 HTTP 请求，也未要求登录凭据。")
        print("[dry-run] 下面是 runner 自验：内置 4 条假样本，与上面的评测集无关。\n")

    samples = dry_run_samples()
    runs = dry_run_runs()
    records = [
        build_raw_record(sample, run, repeat=1, cfg=self_test_cfg)
        for sample, run in zip(samples, runs, strict=True)
    ]
    summary = build_summary(
        records,
        cfg=self_test_cfg,
        repeats=1,
        base_url=DEFAULT_BASE_URL,
        limit=None,
        app_commit_mismatch=False,
        deny_leak=False,
    )

    print("=" * 78)
    print("[dry-run] 自验：用内置样本与内置假响应走完整路径；不连服务器、不读评测集。")
    print(f"[dry-run] 样本 {len(samples)} 条 → raw {len(records)} 行")
    print("=" * 78)
    print("\n--- 第 1 条完整 raw 记录 ---")
    print(json.dumps(records[0], ensure_ascii=False, indent=2))
    print("\n--- summary（完整结构；真跑时落 summary.json）---")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # 顺带把三个写盘函数跑一遍：只写临时目录，绝不落在仓库里。
    with tempfile.TemporaryDirectory(prefix="pyknora-eval-dry-") as tmp:
        out = Path(tmp)
        write_raw_jsonl(out / "raw.jsonl", records)
        write_json(out / "summary.json", summary)
        (out / "report.md").write_text(render_report(summary), encoding="utf-8")
        line_count = len((out / "raw.jsonl").read_text(encoding="utf-8").splitlines())
        report_head = (out / "report.md").read_text(encoding="utf-8").splitlines()[:12]

    print("\n--- 产物自检（写临时目录，不落仓库）---")
    print(f"raw.jsonl 行数 = {line_count}（期望 {len(records)}）")
    print("report.md 前 12 行：")
    for line in report_head:
        print(f"  | {line}")

    print("\n--- 指标一览 ---")
    for name in METRIC_ORDER:
        print(f"  {name:22s} = {fmt_metric(summary['metrics'][name]['value'])}")
    print(f"  自动归类失败样本 {len(summary['failures'])} 条")
    print("\n[dry-run] OK：记录、指标、产物三条路径都跑通。")
    return EXIT_OK


# ────────────────────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────────────────────


def git_head() -> str | None:
    """读当前 HEAD，只用于「配置里的 app_commit 是不是漂了」的告警。

    这条链路失败(没装 git、不是仓库)也不该影响评测，所以一律返回 None 而不是抛。
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, ValueError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def credentials_from(args: argparse.Namespace) -> tuple[str, str]:
    """登录凭据只从命令行或环境变量来，**不写进 yaml**。

    写进 yaml 的结果是它必然进 git（配置是入库文件），而评测用的账号往往就是本机
    管理员账号；token 不落盘，密码也不能。
    """
    username = args.username or os.environ.get("PYKNORA_EVAL_USERNAME")
    password = args.password or os.environ.get("PYKNORA_EVAL_PASSWORD")
    if not username or not password:
        raise ConfigError(
            "缺少登录凭据：用 --username/--password，或设 PYKNORA_EVAL_USERNAME / "
            "PYKNORA_EVAL_PASSWORD。凭据刻意不放进 yaml（配置要入库）。"
        )
    return username, password


async def run_real(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    samples = load_eval_set(args.eval_set_path)
    if args.limit is not None:
        samples = samples[: args.limit]
    username, password = credentials_from(args)

    records: list[dict[str, Any]] = []
    chat_timeout = float(cfg.get("chat_timeout_s") or 120.0)
    async with EvalClient(args.base_url, chat_timeout_s=chat_timeout) as client:
        health = await client.health()
        print(f"[eval] /health OK：{json.dumps(health, ensure_ascii=False)}")
        login = await client.login(username, password)
        print(
            f"[eval] 登录成功 principal={login.get('principal_id')} "
            f"workspace={login.get('workspace_id')}"
        )
        print(f"[eval] 样本 {len(samples)} 条 × repeats={args.repeats}")

        for repeat in range(1, args.repeats + 1):
            for index, sample in enumerate(samples, start=1):
                try:
                    run = await run_sample(client, sample, cfg)
                except ConfigError:
                    raise
                except Exception as exc:  # 单条样本失败不该中断整轮
                    # 一条样本炸掉就丢掉整轮，等于把「某条超时」升级成「这轮白跑」；
                    # 记成带 error_code 的 raw 行，指标里它自然表现为未命中。
                    print(f"[eval] 样本 {sample.sample_id} 异常：{type(exc).__name__}: {exc}")
                    run = SampleRun(status="UNKNOWN", error_code=type(exc).__name__)
                records.append(build_raw_record(sample, run, repeat=repeat, cfg=cfg))
                print(
                    f"[eval] r{repeat} {index}/{len(samples)} {sample.sample_id:12s} "
                    f"query={run.query_http_status} results={len(run.results)} "
                    f"chat={run.chat_http_status} status={run.status} "
                    f"latency={run.query_latency_ms:.1f}ms"
                )

    deny_leak = any(
        isinstance(record.get("deny"), dict) and record["deny"].get("returned_200")
        for record in records
    )
    head = git_head()
    app_commit_mismatch = bool(head and str(cfg.get("app_commit")) != head)

    summary = build_summary(
        records,
        cfg=cfg,
        repeats=args.repeats,
        base_url=args.base_url,
        limit=args.limit,
        app_commit_mismatch=app_commit_mismatch,
        deny_leak=deny_leak,
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_raw_jsonl(out_dir / "raw.jsonl", records)
    write_json(out_dir / "summary.json", summary)
    (out_dir / "report.md").write_text(render_report(summary), encoding="utf-8")

    print("\n[eval] 指标：")
    for name in METRIC_ORDER:
        print(f"  {name:22s} = {fmt_metric(summary['metrics'][name]['value'])}")
    print(f"[eval] 产物目录：{out_dir.resolve()}")
    for warning in summary["warnings"]:
        print(f"[eval] 注意：{warning}")

    if deny_leak:
        print("\n[eval] 失败：存在 deny 样本返回 200 / 泄漏片段（exit 3）。")
        return EXIT_DENY_LEAK
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Day3 可信 RAG 的 HTTP-only 评测 runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  uv run --frozen python eval/run_eval.py --dry-run\n"
            "  uv run --frozen python eval/run_eval.py"
            " --config eval/baseline_config.yaml --repeats 3\n"
            "  uv run --frozen python eval/run_eval.py"
            " --config eval/baseline_config.yaml --check-config\n"
            "  uv run --frozen python eval/run_eval.py"
            " --compare-config eval/baseline_config.yaml eval/candidate_config.yaml\n"
        ),
    )
    parser.add_argument(
        "--config", type=Path, help="基线配置 yaml（--dry-run/--compare-config 时可省）"
    )
    parser.add_argument("--eval-set", type=Path, help="评测集 jsonl；缺省取配置里的 eval_dataset")
    parser.add_argument(
        "--repeats", type=int, default=1, help="重复轮数（默认 1；>1 时报告 min/max）"
    )
    parser.add_argument("--out", type=Path, help="产物目录（默认 eval/results/<experiment_id>）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="被测服务地址")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="不连服务器；带 --config 时打印真实评测计划，并用内置样本自验整条路径",
    )
    parser.add_argument("--limit", type=int, help="只跑前 N 条（调试用，结果不可对外）")
    parser.add_argument("--check-config", action="store_true", help="只校验配置，不发请求")
    parser.add_argument(
        "--compare-config",
        nargs=2,
        metavar=("A", "B"),
        type=Path,
        help="公平性守卫：两份 yaml 必须恰好一个主因素不同",
    )
    parser.add_argument("--username", help="登录用户名（或 PYKNORA_EVAL_USERNAME）")
    parser.add_argument("--password", help="登录密码（或 PYKNORA_EVAL_PASSWORD）")
    return parser


def print_config_report(cfg: dict[str, Any], path: Path, *, head: str | None) -> None:
    print(f"[check-config] {path} 通过校验（必需字段齐全、无占位符）。")
    print(f"{'字段':28s} 值")
    for key, value in flatten_config(cfg).items():
        print(f"{key:28s} {value}")
    if head and str(cfg.get("app_commit")) != head:
        print(
            f"[check-config] 注意：app_commit={cfg.get('app_commit')} 与当前 HEAD={head} 不一致；"
            "计划 §3.5 要求跑前自动写入，手填会漂。"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.repeats < 1:
        print("--repeats 必须 >= 1", file=sys.stderr)
        return EXIT_CONFIG
    if args.limit is not None and args.limit < 1:
        print("--limit 必须 >= 1", file=sys.stderr)
        return EXIT_CONFIG

    try:
        if args.dry_run:
            return run_dry_run(
                config_path=args.config,
                eval_set_path=args.eval_set,
                repeats=args.repeats,
                base_url=args.base_url,
                limit=args.limit,
            )

        if args.compare_config:
            ok, lines = compare_configs(*args.compare_config)
            print("[compare-config] 通过。" if ok else "[compare-config] 未通过。")
            for line in lines:
                print(f"  {line}")
            return EXIT_OK if ok else EXIT_CONFIG

        if args.config is None:
            print("--config 必填（--dry-run / --compare-config 除外）", file=sys.stderr)
            return EXIT_CONFIG

        cfg = load_yaml(args.config)
        problems = validate_config(cfg)
        if problems:
            print(f"[config] {args.config} 不合格，共 {len(problems)} 处：", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return EXIT_CONFIG

        head = git_head()
        if args.check_config:
            print_config_report(cfg, args.config, head=head)
            return EXIT_OK

        if args.eval_set is not None:
            args.eval_set_path = args.eval_set
        else:
            args.eval_set_path = resolve_dataset_path(str(cfg["eval_dataset"]), args.config)
        if args.out is None:
            args.out = Path("eval/results") / str(cfg["experiment_id"])

        return asyncio.run(run_real(args, cfg))
    except ConfigError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except RuntimeFailure as exc:
        print(f"[run] {exc}", file=sys.stderr)
        return EXIT_RUNTIME


if __name__ == "__main__":
    raise SystemExit(main())
