"""引用校验的三条判据（计划 §6.4 `:480-482` 的 CIT-01/02/03、§6.6 `:499`）。

三条要求里，有的今天**能断言**，有的只有一个**缺口**。分开写，是为了让"绿"不冒充
"已实现"——`xfail(strict=True)` 的用例一旦有人补上函数就会 XPASS 并让套件失败，
逼着把标记删掉；绿着的 `xfail` 永远只能读成"仍未实现"。

1. **CIT-01（引用不存在的 ID）**：本协议里模型只能写 `[资料 N]`（N 是投喂顺序，
   从 1 起），`citations` 是服务端按同一个列表构造的（`api/v1/chat.py:160-167`）。
   所以"编一个不存在的 chunk_id"当前物理上产生不了，能校验的只有**序号越界**；
   这一层用 `invalid_cited_indices` 与 `done.invalid_citations` 锁住。**ID 级**
   校验（`UNKNOWN_CITATION`）不存在——`core/errors.py` 的 `ErrorCode` 里没有这个
   取值，见 `test_unknown_chunk_id_is_rejected`。
2. **CIT-02（引用已撤权/越权文档）**：可见性只在检索 SQL 谓词与
   `GET /documents/{id}` 的 404 兜底两处做（计划 §6.5 `:486-488`），组装 citations
   时不再拿 `Scope` 复核，也没有 deny 库样本。这里只锁"上下文只能从检索命中里删、
   不能凭空加"这条结构不变量，"拒"本身见下面的 CIT-02 xfail。
3. **CIT-03（旧版冒充现行版）**：D15 已经把等式**两端**都摆出来了
   （`Citation.active_document_version_id` 与 `GET /documents/{id}` 的同名字段），
   判定因此是一次等式比较；但**没有生产代码消费它**，`chat.py` 只是把值原样透出。
   数据面与入口面（片段没有版本号就不进门控）可测，"拒"那一半见
   `test_gate_rejects_citation_from_retired_version`。

本文件不重跑门控规则（那是 `test_evidence.py` 的职责），不起 HTTP、不连数据库。
"""

from __future__ import annotations

from uuid import UUID

import pytest

# 必须先导入 main：chat 路由与 `pyknora.api.main` 之间有循环导入，直接从
# `pyknora.api.v1` 取路由会在别处先被导入时炸掉（与 test_rag_chat_events.py 同因）。
import pyknora.api.main  # noqa: F401
from pyknora.api.v1 import chat as chat_routes
from pyknora.core.scope import Scope
from pyknora.core.settings import Settings, settings
from pyknora.schemas.chat import Citation, DoneEvent, RunStatus
from pyknora.schemas.query import RetrievedChunk
from pyknora.services.rag.answer import (
    cited_indices,
    invalid_cited_indices,
    select_context_chunks,
)
from pyknora.services.rag.evidence import EvidenceAssessment, Verdict

_WORKSPACE = UUID("11111111-1111-4111-8111-111111111111")
_KB = UUID("22222222-2222-4222-8222-222222222222")
_DOC = UUID("33333333-3333-4333-8333-333333333333")
_DOC_DENIED = UUID("44444444-4444-4444-8444-444444444444")
_VERSION_V1 = UUID("55555555-5555-4555-8555-555555555555")
_VERSION_V2 = UUID("66666666-6666-4666-8666-666666666666")
_CHUNK_V1 = UUID("77777777-7777-4777-8777-777777777777")
_CHUNK_V2 = UUID("88888888-8888-4888-8888-888888888888")
_CHUNK_DENIED = UUID("99999999-9999-4999-8999-999999999999")


# ---------------------------------------------------------------- 夹具构造


def _settings(**overrides: object) -> Settings:
    return settings.model_copy(update={"chat_max_context_chunks": 6, **overrides})


def _chunk(
    chunk_id: UUID,
    *,
    version: UUID | None = _VERSION_V2,
    document_id: UUID = _DOC,
    content: str = "一线城市住宿报销上限为 500 元。",
    page_no: int = 3,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        workspace_id=_WORKSPACE,
        kb_id=_KB,
        document_id=document_id,
        index_version=1,
        chunk_ordinal=0,
        content=content,
        page_no=page_no,
        page_end_no=page_no,
        rrf_score=0.5,
        vector_rank=1,
        keyword_rank=1,
        active_document_version_id=version,
    )


def _citation(chunk: RetrievedChunk) -> Citation:
    """与 `api/v1/chat.py:160-167` 同形的组装：版本号只能来自命中片段本身。"""
    return Citation(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        page_no=chunk.page_no,
        page_end_no=chunk.page_end_no,
        snippet=chunk.content[:500],
        active_document_version_id=chunk.active_document_version_id,
    )


def _is_current(citation: Citation, *, active_version_id: UUID | None) -> bool:
    """§6.4 `:482` 的 CIT-03 判据：引用自带的版本必须**等于**文档的现行版本。

    缺字段（`None`）不是"无法判断所以放行"，而是不通过：只有两端给出同一个 UUID，
    "这条引用来自现行版"才有证据。判据写在这里是因为生产侧还没有消费它的地方
    （见 `test_gate_rejects_citation_from_retired_version`），而不是因为规则只存在于
    测试里——§6.4 的原话就是"必须等于 …，不等即旧版 → 拒"。
    """
    return (
        active_version_id is not None and citation.active_document_version_id == active_version_id
    )


# ---------------------------------------------------------------- CIT-01


def test_out_of_range_citation_marker_counts_as_invalid() -> None:
    """CIT-01（序号层）：`[资料 N]` 越界就是"引用了不存在的资料"。

    模型看到的是 1..N 编号的资料（`answer._format_chunk`），越界只有两种成因：
    它编了一份没投喂过的资料，或引用了被 `chat_max_context_chunks` 截掉的那几条。
    两个边界都要锁：恰好 `N == context_size` 是合法的（差一错误的高发点），
    `0` 不合法（序号从 1 起，0 不是"第一条"）。
    """
    answer = "住宿上限 500 元 [资料 1]，另有补充 [资料 9]。"
    assert cited_indices(answer) == {1, 9}
    invalid = invalid_cited_indices(answer, context_size=3)
    assert invalid == [9]

    assert invalid_cited_indices("见 [资料 3]", context_size=3) == []
    assert invalid_cited_indices("见 [资料 0]", context_size=3) == [0]
    # 空上下文（拒答 / 预算中止）时任何序号都越界：那时一条资料都没投喂。
    assert invalid_cited_indices("见 [资料 1]", context_size=0) == [1]

    # 越界条数必须进 `done` 事件：eval 的 `eval/run_eval.py:666` 就是从这一格读回来的。
    # 只写日志的话，调用方会把带假引用的答案当成有据可查（`schemas/chat.py:93-100`）。
    done = DoneEvent(
        invalid_citations=len(invalid),
        status=RunStatus.COMPLETED,
        run_id="run-test",
    )
    assert done.model_dump(mode="json")["invalid_citations"] == 1


@pytest.mark.xfail(
    strict=True,
    reason="缺口：没有 chunk_id 级引用校验（计划 §6.4:480 要求 UNKNOWN_CITATION）",
)
def test_unknown_chunk_id_is_rejected() -> None:
    """CIT-01（ID 层）：引用了本轮已知集合之外的 chunk_id 必须被判不通过。

    现状：**没有任何函数能做这件事**。`citations` 由服务端按 `context_chunks` 顺序
    构造，模型只输出 `[资料 N]`，所以伪造 chunk_id 当前无从产生。这不等于"已经
    校验过"：一旦 citations 改成按 ID 续查、或与投喂列表分叉，就没有第二道闸去拦
    一个不存在的 ID。

    缺什么：`validate_citation_ids(citations, known_chunk_ids)`（返回被拒的引用）。
    计划 §6.4 `:480` 要求错误码 `UNKNOWN_CITATION`，而 `src/pyknora/core/errors.py`
    的 `ErrorCode`（18 个取值）里没有它——连"拒"的载体都还不存在。
    最该实现：新建 `src/pyknora/services/rag/citations.py`，与
    `answer.invalid_cited_indices` 同层（序号校验管越界，ID 校验管身份），
    由 `api/v1/chat.py` 在组装 citations 之后、发 `done` 之前调用。
    """
    from pyknora.services.rag.citations import validate_citation_ids

    citations = [_citation(_chunk(_CHUNK_V1))]
    assert validate_citation_ids(citations, known_chunk_ids={_CHUNK_V2})


# ---------------------------------------------------------------- CIT-02


def test_context_only_ever_removes_retrieved_chunks() -> None:
    """CIT-02 的"不塞入"一半：上下文只能从检索命中里删，不能凭空加。

    可见性在检索 SQL 的谓词里就定了（`db/repos/reads.py`）；到了 RAG 这一层，
    "哪些片段允许被看见"已经是既成事实。`select_context_chunks` 若会补片段，
    越权内容就能绕过检索层出现在 citations 里。所以这里锁：返回值永远是入参的
    子序列，两类过滤（门控的支持列表、`chat_max_context_chunks`）都只做减法。
    """
    chunks = [_chunk(_CHUNK_V1), _chunk(_CHUNK_V2), _chunk(_CHUNK_DENIED)]

    kept = select_context_chunks(chunks, settings=_settings(chat_max_context_chunks=2))
    assert [item.chunk_id for item in kept] == [_CHUNK_V1, _CHUNK_V2]
    assert {item.chunk_id for item in kept} <= {item.chunk_id for item in chunks}

    supported = EvidenceAssessment(
        verdict=Verdict.SUPPORTED,
        supporting_chunk_ids=(_CHUNK_V2,),
        reason="test",
    )
    kept = select_context_chunks(
        chunks, settings=_settings(chat_max_context_chunks=6), assessment=supported
    )
    # 门控没点名的片段（哪怕检索命中）不得进上下文，自然也不会变成 citation。
    assert [item.chunk_id for item in kept] == [_CHUNK_V2]


@pytest.mark.xfail(
    strict=True,
    reason="缺口：citations 组装前没有二次授权（计划 §6.4:481 要 CITATION_ACCESS_DENIED）",
)
def test_citation_of_a_document_outside_the_scope_is_rejected() -> None:
    """CIT-02 的"拒绝"一半：不在授权集合里的文档不能留在 citations 里。

    现状（计划 §6.5 `:486-488`）：可见性只在两处做——检索 SQL 的谓词，与
    `GET /documents/{id}` 对"已软删/越权"统一回 404。`api/v1/chat.py` 组装 citations
    时**不再拿 `Scope` 复核**，`context_chunks` 里有什么就发什么。当前不会越权，
    只因为"检索拿不到就进不来"这一条隐式链；缓存、跨请求复用、或将来按 ID 续查，
    任何一处改动都会把它变成真漏洞。

    缺什么：`reauthorize_citations(citations, *, scope)`（逐条按 document_id 重判
    可见性）。计划 §6.4 `:481` 要求错误码 `CITATION_ACCESS_DENIED`，`ErrorCode`
    里同样没有；§6.6 把这条划给 api 层，因为需要 deny 库样本（`docs/Day03/06 §6`
    已登记"当前无该用例"）。
    最该实现：`src/pyknora/api/v1/chat.py` 的 `_evidence()` 之后、`context_chunks`
    组装之前（那里同时握着 `scope`、`chunks` 与 session），或抽进
    `services/rag/citations.py` 供 `/query` 复用。
    """
    from pyknora.services.rag.citations import reauthorize_citations

    denied = _citation(_chunk(_CHUNK_DENIED, document_id=_DOC_DENIED))
    scope = Scope(
        workspace_id=_WORKSPACE,
        principal_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    )
    assert reauthorize_citations([denied], scope=scope)


# ---------------------------------------------------------------- CIT-03


def test_citation_carries_the_version_it_was_retrieved_under() -> None:
    """CIT-03 的数据面：新旧两版引用必须能靠 `active_document_version_id` 分开。

    同一篇文档换版后，`document_id`、页码、甚至片段文本都可能一模一样（同名重传、
    只改其中一段）。唯一能机械区分"这条引用来自现行版"的，就是 D15 这个字段。
    本用例锁三件事：字段确实从片段透出、序列化后还在、缺字段按不通过处理。
    """
    old = _citation(_chunk(_CHUNK_V1, version=_VERSION_V1, content="春节休假 7 天。"))
    new = _citation(_chunk(_CHUNK_V2, version=_VERSION_V2, content="春节休假 9 天。"))

    # 同一篇文档：只靠 document_id 分不开新旧。
    assert old.document_id == new.document_id
    assert old.chunk_id != new.chunk_id  # 新版是另一次索引产出的新片段
    assert not _is_current(old, active_version_id=_VERSION_V2)
    assert _is_current(new, active_version_id=_VERSION_V2)
    # 文档没有现行版时"来自现行版"证不了，按不通过走。
    assert not _is_current(new, active_version_id=None)

    # 走一遍 SSE 的 JSON 序列化：字段在 payload 里丢掉就等于没带，
    # 前端拿不到等式的一端，判定只能退回人眼。
    assert old.model_dump(mode="json")["active_document_version_id"] == str(_VERSION_V1)

    unversioned = _citation(_chunk(_CHUNK_V1, version=None))
    assert unversioned.active_document_version_id is None
    assert not _is_current(unversioned, active_version_id=_VERSION_V2)


def test_gate_never_receives_a_chunk_without_an_active_version() -> None:
    """CIT-03 的入口面：没有现行版本号的片段不得成为门控输入。

    `Evidence.active_document_version_id` 是非空 UUID；把 `None` 塞成一个哨兵 UUID
    等于伪造版本号，后续的等式判定会静默通过——那正是这个字段存在的唯一理由
    （`api/v1/chat.py:57-88` 的注释）。`_evidence()` 是唯一构造门控输入的地方，
    所以直接测它；走路由反而会把这句断言埋进 SSE 解析里。
    """
    chunks = [
        _chunk(_CHUNK_V1, version=_VERSION_V1),
        _chunk(_CHUNK_V2, version=None),
    ]
    # 私有函数是刻意的：它是"片段 → 门控输入"的唯一边界，测它比测整条路由更窄。
    evidence = chat_routes._evidence(chunks)
    assert [item.chunk_id for item in evidence] == [_CHUNK_V1]
    assert evidence[0].active_document_version_id == _VERSION_V1


@pytest.mark.xfail(
    strict=True,
    reason="缺口：没有代码把引用版本与文档现行版本做等式判定（计划 §6.4:482）",
)
def test_gate_rejects_citation_from_retired_version() -> None:
    """CIT-03 的判定面：引用版本 ≠ 文档现行版本时，这次问答必须标成不通过。

    现状：`chat.py` 把 `active_document_version_id` 原样透出（D15 只完成"带出来"），
    **没有任何消费方**。`done` 事件里只有 `invalid_citations`（序号越界）与
    `verdict`（证据门控），没有"引用是否现行"的位置；
    `tests/api/test_api_chat.py:235` 也只断言字段被带出。所以"旧版冒充现行版"
    今天不会被任何一层拦住。

    缺什么：`check_citation_versions(citations, *, active_versions)` → 返回不匹配的
    引用，以及 `done` 上一个能表达它的字段（或直接置 `status` 非 `COMPLETED`）。
    计划 §6.4 `:482` 只规定"不得冒充现行版"，**没有指定错误码**；现成的
    `VERSION_SUPERSEDED` 是发布语义（`services/ingest/publish.py:126,148`），
    借用它会让调用方把"作业被新版本顶掉"和"引用过期"混成一件事。
    最该实现：`services/rag/citations.py`（与 CIT-01/02 同一层），由
    `api/v1/chat.py` 在发 `done` 之前调用并把结果并进 `done`。
    """
    from pyknora.services.rag.citations import check_citation_versions

    retired = _citation(_chunk(_CHUNK_V1, version=_VERSION_V1))
    mismatches = check_citation_versions([retired], active_versions={_DOC: _VERSION_V2})
    assert [item.chunk_id for item in mismatches] == [_CHUNK_V1]
