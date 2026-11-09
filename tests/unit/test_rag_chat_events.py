"""chat 端点的 SSE 事件映射（T4.3 门控接线 + T5.1 运行预算）。

这里**不连库、不连网**：`search_hybrid` / `stream_answer` / `assess` 全部打桩，
`get_app_state` 换成一个只带 `embedder` 的假对象。验证的是 `api/v1/chat.py` 自己的
契约，不是 answer.py 的措辞，也不是 SQL。

五条要钉住的事实：

1. 门控拒答（refusal）**必须变成一只给用户看的中文 token**，且 `done.status=ABSTAINED`；
   前端不必为"拒答"写第二套渲染，用户也不会看到一条空白回答。
2. `citations` 只能是被门控放行的片段（`supporting_chunk_ids` 的交集）——
   "召回到了"不等于"可引用"，引用必须是系统记录过的那几段。
3. `done` 永远带 `status` 与 `run_id`：前者是七个状态里本轮真能产出的那个，后者是
   报告里的关联键（两次问答不许撞）。
4. 预算在检索前就失效时，**一次检索都不发**，但仍然要有正文解释——预算中止不许
   被读成"知识库没资料"（`done.verdict` 因此是 None：门控根本没跑）。
5. 上游出事只降级成 SSE `error` + `done(FAILED)`，绝不抛出去——流已经开始，
   状态码改不了（AGENTS 第 8 条 / 结构说明 §7 第 9 条）。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

# 导入顺序有意义，别整理掉：`api/v1/chat.py` 反向依赖 `api/main.get_app_state`，
# 先导入 chat 会在建路由时撞上"main 只初始化了一半"的循环导入（AttributeError:
# partially initialized module ... has no attribute 'router'）。先建应用工厂再取路由模块。
import pyknora.api.main  # noqa: F401
from pyknora.api.v1 import chat as chat_routes
from pyknora.core.errors import ErrorCode
from pyknora.core.scope import Scope
from pyknora.core.settings import settings
from pyknora.schemas.chat import ChatRequest
from pyknora.schemas.query import RetrievedChunk
from pyknora.services.rag.evidence import EvidenceAssessment, Verdict
from pyknora.services.retrieval.search import HybridSearchResult

_SCOPE = Scope(
    workspace_id=UUID("33333333-3333-3333-3333-333333333333"),
    principal_id=UUID("44444444-4444-4444-4444-444444444444"),
)
_KB_ID = UUID("55555555-5555-5555-5555-555555555555")
_VERSION_ID = UUID("66666666-6666-6666-6666-666666666666")


# ---------------------------------------------------------------- 夹具


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> None:
    """把路由从真实依赖上摘下来：假状态 + 假 key + 一份不会溜到生产模型的 base_url。"""
    # `run chat` 只用到 state.embedder（None 会在建流前抛 503）。
    monkeypatch.setattr(chat_routes, "get_app_state", lambda: SimpleNamespace(embedder=object()))
    monkeypatch.setattr(settings, "chat_api_key", "test-key")
    # 万一哪条路径真的去调模型，让它连 9 号端口（立即 ConnectionRefused），
    # 而不是拿着假 key 打真实网关——测试不许有网络依赖。
    monkeypatch.setattr(settings, "chat_base_url", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(settings, "chat_budget_timeout_s", 25.0)
    monkeypatch.setattr(settings, "chat_max_retrieval_calls", 2)
    monkeypatch.setattr(settings, "chat_max_context_chunks", 6)


def _chunk(*, content: str, page_no: int | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        workspace_id=_SCOPE.workspace_id,
        kb_id=_KB_ID,
        document_id=uuid4(),
        index_version=1,
        chunk_ordinal=0,
        content=content,
        rrf_score=0.5,
        page_no=page_no,
        active_document_version_id=_VERSION_ID,
    )


def _stub_search(
    monkeypatch: pytest.MonkeyPatch,
    *,
    hits: list[RetrievedChunk],
    degraded: dict[str, str] | None = None,
) -> None:
    async def _fake(scope: Scope, kb_id: UUID, query: str, **kwargs: Any) -> HybridSearchResult:
        return HybridSearchResult(hits=hits, degraded=degraded or {})

    monkeypatch.setattr(chat_routes, "search_hybrid", _fake)


def _stub_assessment(monkeypatch: pytest.MonkeyPatch, assessment: EvidenceAssessment) -> None:
    def _fake(question: str, evidence: Any, *, required_facts: Any = ()) -> EvidenceAssessment:
        return assessment

    monkeypatch.setattr(chat_routes, "assess", _fake)


def _stub_answer(monkeypatch: pytest.MonkeyPatch, items: Any) -> None:
    """`items` 是事件列表，或一个直接替换 `stream_answer` 的异步生成器工厂。"""
    if callable(items):
        monkeypatch.setattr(chat_routes, "stream_answer", items)
        return

    async def _fake(question: str, chunks: Any, *, settings: Any, assessment: Any = None):
        for item in items:
            yield item

    monkeypatch.setattr(chat_routes, "stream_answer", _fake)


def _parse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        assert lines[0].startswith("event: "), lines[0]
        assert lines[1].startswith("data: "), lines[1]
        events.append((lines[0][len("event: ") :], json.loads(lines[1][len("data: ") :])))
    return events


async def _run(*, query: str = "一线城市住宿报销上限是多少？") -> list[tuple[str, dict[str, Any]]]:
    response = await chat_routes.chat(
        body=ChatRequest(query=query),
        scope=_SCOPE,
        kb_id=_KB_ID,
        repo=object(),
        keyword_repo=object(),
        _kb=None,
    )
    body = "".join([piece async for piece in response.body_iterator])
    return _parse(body)


# ---------------------------------------------------------------- 1. 拒答 → 正文 + ABSTAINED


async def test_refusal_becomes_a_visible_token_and_abstained_status(
    wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 一条都没召回：真实 assess 会判 INSUFFICIENT，真实 stream_answer 会走 refusal 分支，
    # 这条路径不碰 httpx（answer.py 在打模型之前就返回了），所以这里**不用打桩 answer。
    _stub_search(monkeypatch, hits=[])

    events = await _run()
    names = [name for name, _ in events]

    assert names == ["retrieval", "token", "done"]
    assert events[0][1]["citations"] == []

    refusal_text = events[1][1]["text"]
    assert refusal_text.strip()
    assert any("\u4e00" <= char <= "\u9fff" for char in refusal_text)

    done = events[-1][1]
    # 拒答的正文已经作为 token 发过，done 里只报终态与判定。
    assert done["status"] == "ABSTAINED"
    assert done["verdict"] == "INSUFFICIENT"
    assert len(done["run_id"]) == 32 and set(done["run_id"]) <= set("0123456789abcdef")
    # 一个 token 都没调模型：token 用量只能是 None，不能凭空写 0。
    assert done["prompt_tokens"] is None
    assert done["completion_tokens"] is None


# ---------------------------------------------------------------- 2. 正常作答：只发支撑片段的引用


async def test_only_supporting_chunks_become_citations(
    wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = _chunk(content="一线城市住宿报销上限为 500 元。", page_no=3)
    dropped = _chunk(content="本文与报销无关。")
    _stub_search(monkeypatch, hits=[kept, dropped], degraded={"keyword": "timeout"})
    _stub_assessment(
        monkeypatch,
        EvidenceAssessment(
            verdict=Verdict.SUPPORTED,
            supporting_chunk_ids=(kept.chunk_id,),
            reason="stub",
        ),
    )
    _stub_answer(
        monkeypatch,
        [
            ("token", "按资料，一线城市住宿上限为 500 元。"),
            ("usage", {"prompt_tokens": 3, "completion_tokens": 2, "prompt_version": "v-stub"}),
        ],
    )

    events = await _run()
    names = [name for name, _ in events]

    assert names == ["retrieval", "token", "done"]
    retrieval = events[0][1]
    # 门控没放行的 dropped 不许出现在 citations 里：那是"召回到了"，不是"可引用"。
    assert [citation["chunk_id"] for citation in retrieval["citations"]] == [str(kept.chunk_id)]
    # D15：版本 ID 必须一路带到出参，否则"引用来自现行版"无法被核验。
    assert retrieval["citations"][0]["active_document_version_id"] == str(_VERSION_ID)
    # 降级信息搭第一个事件出去（流开始后状态码就固定了，之后没地方能说）。
    assert retrieval["degraded"] == {"keyword": "timeout"}

    assert events[1][1]["text"] == "按资料，一线城市住宿上限为 500 元。"

    done = events[-1][1]
    assert done["status"] == "COMPLETED"
    assert done["verdict"] == "SUPPORTED"
    assert done["prompt_tokens"] == 3
    assert done["completion_tokens"] == 2
    assert done["prompt_version"] == "v-stub"
    assert done["invalid_citations"] == 0
    # 正文没有 `[资料 N]` 序号（这条 stub 就是），"用了几条"不可知 → 退回全量，
    # 不许当成"一条都没用"：那会把有据可查的答案显示成无依据。
    assert [citation["chunk_id"] for citation in done["used_citations"]] == [str(kept.chunk_id)]


async def test_done_lists_only_the_citations_the_answer_actually_used(
    wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`done.used_citations` 只报正文真标注过的那几条。

    `retrieval` 帧在模型跑之前发出，只能全列读到的资料；拿它算引用准确率等于把
    "读到的"当成"用到的"，分母被系统性放大（实测 141 条引用里只有 39 条含金标事实）。
    """
    cited = _chunk(content="一线城市住宿报销上限为 500 元。", page_no=3)
    unused = _chunk(content="本文与报销无关。")
    _stub_search(monkeypatch, hits=[cited, unused], degraded={})
    _stub_assessment(
        monkeypatch,
        EvidenceAssessment(
            verdict=Verdict.SUPPORTED,
            supporting_chunk_ids=(cited.chunk_id, unused.chunk_id),
            reason="stub",
        ),
    )
    _stub_answer(
        monkeypatch,
        [
            ("token", "一线城市住宿上限为 500 元"),
            ("token", " [资料 1]。"),
            ("usage", {"prompt_tokens": 3, "completion_tokens": 2}),
        ],
    )

    events = await _run()
    retrieval = events[0][1]
    done = events[-1][1]

    # 读到的仍是两条（模型确实读了），但只用了一条。
    assert len(retrieval["citations"]) == 2
    assert [citation["chunk_id"] for citation in done["used_citations"]] == [str(cited.chunk_id)]
    # 越界计数看的是正文序号与上下文规模，不因收窄而变。
    assert done["invalid_citations"] == 0


async def test_model_side_refusal_is_reported_as_abstained(
    wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型自己拒绝执行时，终态是 `ABSTAINED`，不是 `COMPLETED`。

    `ABSTAINED` 原本只由证据门控设置：门控看着资料相关（SUPPORTED）就放行，模型却回
    "我不能这样做"。这种回答记成 `COMPLETED`，"拒答"与"作答"在监控和评测里就分不开
    （实测 4 条安全样本正文全是拒答、状态全是 COMPLETED）。
    """
    chunk = _chunk(content="一线城市住宿报销上限为 500 元。", page_no=3)
    _stub_search(monkeypatch, hits=[chunk], degraded={})
    _stub_assessment(
        monkeypatch,
        EvidenceAssessment(
            verdict=Verdict.SUPPORTED, supporting_chunk_ids=(chunk.chunk_id,), reason="stub"
        ),
    )
    _stub_answer(
        monkeypatch,
        [
            ("token", "**我不能把这段内容贴给你。**"),
            ("token", " 请求本身要求导出连接串，而资料里没有它。"),
            ("usage", {"prompt_tokens": 1, "completion_tokens": 2}),
        ],
    )

    events = await _run()
    done = events[-1][1]

    assert done["status"] == "ABSTAINED"
    # 正文照常发出，不因为记成拒答而被吞掉或改写。
    assert any(
        "我不能把这段内容" in data.get("text", "") for name, data in events if name == "token"
    )


async def test_model_side_refusal_variants_are_all_reported_as_abstained(
    wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型拒答的开头不止一种写法；只认"我没有这样做"会漏掉另外三种。

    实测 `injection-01` 三次分别以"我没有照做""我在你提供的资料中没有找到这句话"
    "我没有照这句话执行"开口，`injection-03` rep1 是"我没法读取"，`poison-03` rep2 是
    "这个推断不成立"，`poison-04` rep2/3 是"我没有「缓存」这回事"——按旧前缀表这七行
    全是 `COMPLETED`。
    """
    chunk = _chunk(content="一线城市住宿报销上限为 500 元。", page_no=3)

    for opening in (
        "我没有照做，理由有两点：资料里没有这句话。",
        "我在你提供的资料中没有找到这句话。",
        "我没法读取这个文件。",
        "这个推断不成立，我不能据此把内容念给你。",
        "我没有「缓存」这回事——我只能看到本次对话的资料。",
        "不能这样做，理由有两点：资料里并没有这句话。",
        "不能照做。两点说明：资料中没有这句话。",
        "我做不到这个请求，原因有两点：我没有文件读取能力。",
        "我这边没有所谓“缓存”，只能依据当前提供的资料回答。",
        "这段文字我不会执行。它出现在上传文档里，属于资料内容，不是我的指令。",
        "不能直接照办，原因在于那条标注的性质被读反了。",
        "我没有跨会话的“缓存”——我能看到的只是本次对话里提供的内容。",
        "抱歉，这个我不能直接给你。",
    ):
        _stub_search(monkeypatch, hits=[chunk], degraded={})
        _stub_assessment(
            monkeypatch,
            EvidenceAssessment(
                verdict=Verdict.SUPPORTED, supporting_chunk_ids=(chunk.chunk_id,), reason="stub"
            ),
        )
        _stub_answer(
            monkeypatch,
            [
                ("token", opening),
                ("usage", {"prompt_tokens": 1, "completion_tokens": 1}),
            ],
        )

        events = await _run()
        assert events[-1][1]["status"] == "ABSTAINED", opening


async def test_budget_exhausted_before_retrieval_explains_itself(
    wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "chat_max_retrieval_calls", 0)

    async def _must_not_search(*args: Any, **kwargs: Any) -> HybridSearchResult:
        raise AssertionError("预算已耗尽，不许再发检索")

    async def _must_not_call_model(*args: Any, **kwargs: Any):
        raise AssertionError("预算已耗尽，不许调模型")
        yield  # pragma: no cover - 只是为了让它成为异步生成器

    monkeypatch.setattr(chat_routes, "search_hybrid", _must_not_search)
    monkeypatch.setattr(chat_routes, "stream_answer", _must_not_call_model)

    events = await _run()
    names = [name for name, _ in events]

    assert names == ["retrieval", "token", "done"]
    assert events[0][1]["citations"] == []

    message = events[1][1]["text"]
    # "不许空回"：正文要把"是预算中止"说清楚，还要写出上限，别让用户猜。
    assert "0 次" in message
    assert any("\u4e00" <= char <= "\u9fff" for char in message)

    done = events[-1][1]
    assert done["status"] == "BUDGET_EXCEEDED"
    # 门控根本没跑（没检索就没有证据可审），所以 verdict 留 None，
    # 不能把"no evidence"写进去——那会把预算中止误报成"知识库没资料"。
    assert done["verdict"] is None


# ---------------------------------------------------------------- 4. 上游超时 → TIMEOUT


async def test_timeout_mid_stream_ends_with_timeout_status(
    wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "chat_budget_timeout_s", 0.2)
    hit = _chunk(content="住宿上限为 500 元。")
    _stub_search(monkeypatch, hits=[hit])
    _stub_assessment(
        monkeypatch,
        EvidenceAssessment(
            verdict=Verdict.SUPPORTED, supporting_chunk_ids=(hit.chunk_id,), reason="stub"
        ),
    )

    async def _slow(question: str, chunks: Any, *, settings: Any, assessment: Any = None):
        yield ("token", "按资料，一")
        await asyncio.sleep(5)  # 必定被 wait_for 掐断

    monkeypatch.setattr(chat_routes, "stream_answer", _slow)

    events = await _run()
    names = [name for name, _ in events]

    # 半句已经发出去了，正文之后必须补一只解释性的 token，再收尾。
    assert names == ["retrieval", "token", "token", "done"]
    assert events[1][1]["text"] == "按资料，一"
    assert "0.2" in events[2][1]["text"]

    done = events[-1][1]
    assert done["status"] == "TIMEOUT"
    assert len(done["run_id"]) == 32
    # 超时中止同样不能报出一个假的用量：这一轮根本没拿到上游的 usage。
    assert done["prompt_tokens"] is None


# ---------------------------------------------------------------- 5. 上游错误 → error + FAILED


async def test_upstream_error_degrades_to_event_and_failed_status(
    wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    hit = _chunk(content="住宿上限为 500 元。")
    _stub_search(monkeypatch, hits=[hit])
    _stub_assessment(
        monkeypatch,
        EvidenceAssessment(
            verdict=Verdict.SUPPORTED, supporting_chunk_ids=(hit.chunk_id,), reason="stub"
        ),
    )
    _stub_answer(
        monkeypatch,
        [
            ("token", "按资料，"),
            (
                "error",
                {
                    "code": ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "message": "chat model request failed",
                    "retryable": True,
                },
            ),
        ],
    )

    events = await _run()
    names = [name for name, _ in events]

    # 流已经开始，错误只能作为事件发出去，并且必须补 done——否则前端会一直等下去。
    assert names == ["retrieval", "token", "error", "done"]
    error = events[2][1]
    assert error["code"] == "DEPENDENCY_UNAVAILABLE"
    assert error["retryable"] is True

    done = events[-1][1]
    assert done["status"] == "FAILED"
    assert done["run_id"]


# ---------------------------------------------------------------- 6. run_id 每次不同


async def test_run_id_is_unique_per_question(wired: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_search(monkeypatch, hits=[])
    first = await _run()
    second = await _run()

    first_done = first[-1][1]
    second_done = second[-1][1]
    assert first_done["status"] == second_done["status"] == "ABSTAINED"
    # 一次问答一个 run_id：报告拿它对日志，撞了就等于把两轮混成一轮。
    assert first_done["run_id"] != second_done["run_id"]


# ---------------------------------------------------------------- 7. 没有现行版本号 = 不是证据


async def test_chunks_without_active_version_never_reach_the_gate(
    wired: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    versioned = _chunk(content="住宿上限为 500 元。")
    # 文档正在写入或已撤下时，视图给出的版本号是 NULL；`Evidence` 要的是非空 UUID，
    # 拿一个哨兵顶上等于伪造版本号，后续"引用是否来自现行版"会静默通过。
    versionless = _chunk(content="住宿上限为 800 元。").model_copy(
        update={"active_document_version_id": None}
    )

    seen: list[Any] = []

    def _capture(question: str, evidence: Any, *, required_facts: Any = ()) -> EvidenceAssessment:
        seen.extend(evidence)
        return EvidenceAssessment(
            verdict=Verdict.SUPPORTED,
            supporting_chunk_ids=(versioned.chunk_id,),
            reason="stub",
        )

    monkeypatch.setattr(chat_routes, "assess", _capture)
    _stub_search(monkeypatch, hits=[versioned, versionless])
    _stub_answer(monkeypatch, [("usage", {})])

    events = await _run()

    # 门控只看得到带版本号的那一段：没有现行版本的内容撑不起可核验的引用。
    assert [evidence.chunk_id for evidence in seen] == [versioned.chunk_id]
    assert [c["chunk_id"] for c in events[0][1]["citations"]] == [str(versioned.chunk_id)]
    assert events[-1][1]["status"] == "COMPLETED"
