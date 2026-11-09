"""证据门控的单元测试：EV-01..EV-04 逐条对位，外加边界与反例。

为什么反例比正例多：门控的失败方向不对称。判成 ``INSUFFICIENT`` 只多拒一次，
判成 ``SUPPORTED``/``CONFLICTING`` 错一次就是给用户一个没有依据的数字。所以计划 §11 R4
要求"每加一条规则补一条反例用例"——下面每条形如 ``test_no_*`` / ``test_*_are_not_*``
的用例都是某个判据的反例，删掉它们等于删掉这条判据的护栏。

序号约定：``reason`` 里的"第 N 段"是**证据序列的 1 起位置**（与 `answer.py` 喂给模型的
``[资料 N]`` 同一口径），不是 `chunk_ordinal`。用例里两者故意取不同值，防止实现混用。
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from pyknora.services.rag.evidence import (
    Evidence,
    EvidenceAssessment,
    Verdict,
    assess,
    split_unsupported_claims,
)

# 固定的 UUID：用例里要按 id 断言"哪几条被列进 supporting_chunk_ids"，
# 随机 uuid4 会让失败信息变成一屏噪声。
_CHUNK_A = UUID("11111111-1111-4111-8111-111111111111")
_CHUNK_B = UUID("22222222-2222-4222-8222-222222222222")
_DOC = UUID("33333333-3333-4333-8333-333333333333")
_VERSION = UUID("44444444-4444-4444-8444-444444444444")


def _evidence(
    *,
    chunk_id: UUID = _CHUNK_A,
    content: str,
    score: float = 0.5,
    chunk_ordinal: int = 0,
    index_version: int = 1,
    page_no: int | None = 1,
    page_end_no: int | None = None,
) -> Evidence:
    """构造一条片段；默认值只负责"字段齐全"，判定相关的值一律由用例显式给。"""
    return Evidence(
        chunk_id=chunk_id,
        document_id=_DOC,
        active_document_version_id=_VERSION,
        index_version=index_version,
        chunk_ordinal=chunk_ordinal,
        content=content,
        score=score,
        page_no=page_no,
        page_end_no=page_end_no,
    )


# ---------------------------------------------------------------- EV-01 有直接支持


def test_ev01_supported_when_chunk_covers_every_numeric_anchor() -> None:
    """EV-01：片段里就有那个金额，问句的数字锚点被全量覆盖 → SUPPORTED。"""
    result = assess(
        "一线城市住宿报销上限是 500 元吗？",
        [_evidence(content="一线城市住宿报销上限为 500 元/晚，超出部分自理。")],
    )

    assert result.verdict is Verdict.SUPPORTED
    assert result.supporting_chunk_ids == (_CHUNK_A,)
    assert "500 元" in result.reason
    assert result.unsupported_claims == ()
    assert result.signals["supported_positions"] == [1]


def test_supported_does_not_require_topic_fragment_when_anchor_is_covered() -> None:
    """数字锚点已覆盖时，不再额外要求"主题片段命中"。

    "500 元能报几晚"与"住宿标准：500 元/晚"在词面上几乎不重合（一个是问句语序），
    但"500 元"这个金额本身就把两句话钉在同一件事上。多叠一条词面要求会把这类
    真实可答的问题判成不足（计划 §11 R4 的误杀）。
    """
    result = assess("500 元能报几晚？", [_evidence(content="住宿标准：500 元/晚，含早餐。")])

    assert result.verdict is Verdict.SUPPORTED
    assert result.signals["topic_chunk_positions"] == []


# ---------------------------------------------------------------- EV-02 只主题相关


def test_ev02_topic_only_is_insufficient_when_question_asks_for_a_number() -> None:
    """EV-02：主题对得上、但片段里没有任何数字 → INSUFFICIENT。

    问句自身不带数字（"上限是多少"），所以数字锚点集合为空；此时必须靠
    ``quantity_asked`` 这条判据要求"片段里确实有数"，否则 3 个主题片段命中会让这段
    "制度说明"被放行——正是计划 §6.1 要拦的场景。
    """
    result = assess(
        "一线城市住宿报销上限是多少？",
        [_evidence(content="住宿报销规定：员工出差应选择合标酒店，超标部分由个人承担。")],
    )

    assert result.verdict is Verdict.INSUFFICIENT
    assert result.supporting_chunk_ids == ()
    assert result.signals["quantity_asked"] is True
    assert "没有任何数字" in result.reason
    assert result.unsupported_claims != ()


def test_numbers_of_the_wrong_kind_do_not_satisfy_a_quantity_question() -> None:
    """片段里有数字，但不是**被问到的那类**数 → 仍然 INSUFFICIENT。

    中文数字锚点上线后（"三天""一年""一次"在制度文本里到处都是），"片段里有没有数字"
    这条判据几乎恒真。真实样本 `noev-02`（问健身补贴额度，语料里没有这件事）就是被
    一段只写了"累计三天以内不扣工资""满一年后享受年假"的片段放行的——它看着有数，
    其实答不了金额问题。
    """
    result = assess(
        "公司每年给员工的健身补贴额度是多少？",
        [
            _evidence(
                content=(
                    "累计三天以内的，员工康复后通过努力能够弥补工作的，原则上不扣发工资；"
                    "在公司工作满一年后，员工可以按规定享受探亲假、年休假。"
                )
            )
        ],
    )

    assert result.verdict is Verdict.INSUFFICIENT
    assert result.signals["quantity_asked"] is True
    assert "不是问题要的那类" in result.reason


def test_no_anchor_and_no_quantity_cue_needs_two_fragments() -> None:
    """问句既无数字也不问数值时，退化成"至少 2 个主题片段"。"""
    supported = assess(
        "住宿报销如何办理？", [_evidence(content="住宿报销：按标准执行，超标自理。")]
    )
    single = assess("住宿报销如何办理？", [_evidence(content="住宿差旅。")])

    assert supported.verdict is Verdict.SUPPORTED
    assert single.verdict is Verdict.INSUFFICIENT
    assert "未达 2 个的下限" in single.reason


# ---------------------------------------------------------------- EV-03 适用对象不匹配


def test_ev03_manager_only_clause_is_insufficient_for_ordinary_employees() -> None:
    """EV-03：片段"仅适用于经理及以上"，问的是新入职员工 → 适用对象不匹配 → INSUFFICIENT。

    这段正文换掉适用对象就足以支撑（见下一个用例），所以判定翻转完全由适用对象规则
    造成——这正是本条要证明的：规则不是摆设。
    """
    result = assess(
        "新入职员工的年假有多少天？",
        [_evidence(content="本条款仅适用于经理及以上人员：年假为 10 天，入职当年按比例折算。")],
    )

    assert result.verdict is Verdict.INSUFFICIENT
    assert result.signals["scope_mismatch_positions"] == [1]
    assert "适用对象不匹配" in result.reason
    # 被排除之后没有候选，主题口径上也不该再说"命中了主题"。
    assert result.signals["topic_chunk_positions"] == []


def test_same_clause_for_all_staff_is_supported() -> None:
    """对照组：同样的条款只要适用于全体员工，就该判 SUPPORTED。"""
    result = assess(
        "新入职员工的年假有多少天？",
        [_evidence(content="本制度适用于全体员工：年假为 10 天，入职当年按比例折算。")],
    )

    assert result.verdict is Verdict.SUPPORTED
    assert result.signals["scope_mismatch_positions"] == []


def test_question_mentioning_both_roles_does_not_exclude_manager_clause() -> None:
    """问句同时问了两类对象时，经理条款是"部分相关"，不能当作不适用丢掉。

    这是适用对象规则唯一会误杀的情形：把整条判成不匹配，就答不出"经理 10 天"这一半。
    """
    result = assess(
        "普通员工和经理的年假分别有多少天？",
        [_evidence(content="经理及以上人员：年假为 10 天。")],
    )

    assert result.signals["scope_mismatch_positions"] == []


# ---------------------------------------------------------------- EV-04 两版本冲突


def test_ev04_conflicting_values_across_chunks() -> None:
    """EV-04：两个版本对同一事实给了不同数值 → CONFLICTING，reason 列值与片段序号。"""
    result = assess(
        "一线城市住宿报销上限是多少？",
        [
            _evidence(
                chunk_id=_CHUNK_A, content="一线城市住宿报销上限为 500 元/晚。", chunk_ordinal=7
            ),
            _evidence(
                chunk_id=_CHUNK_B, content="一线城市住宿报销上限为 800 元/晚。", chunk_ordinal=19
            ),
        ],
    )

    assert result.verdict is Verdict.CONFLICTING
    # 冲突的两处都要进 citations（计划 §7.1），且顺序跟证据顺序走。
    assert result.supporting_chunk_ids == (_CHUNK_A, _CHUNK_B)
    # 序号必须是"第 1/2 段"（证据位置），不是 chunk_ordinal 的 7/19。
    assert "第 1 段" in result.reason
    assert "第 2 段" in result.reason
    assert "500 元" in result.reason
    assert "800 元" in result.reason
    assert result.unsupported_claims != ()
    conflict = result.signals["conflicts"][0]
    assert conflict["category"] == "money"
    assert conflict["values"] == [500.0, 800.0]
    assert conflict["chunk_positions"] == [1, 2]


def test_upper_bound_and_lower_bound_are_not_a_conflict() -> None:
    """``住宿上限 500`` 与 ``住宿下限 300`` 只共享上位词，不是同一条事实的两个取值。

    这条规则是收窄冲突判定的那一半：中心词（``上限``/``下限``）不同且只共享"住宿"时
    判成冲突，会让"住宿标准"这类问句永远拿到 CONFLICTING，用户反而看不到任何规定。
    """
    result = assess(
        "住宿报销标准是多少？",
        [
            _evidence(chunk_id=_CHUNK_A, content="一线城市住宿报销上限 500 元/晚。"),
            _evidence(chunk_id=_CHUNK_B, content="一线城市住宿报销下限 300 元/晚。"),
        ],
    )

    assert result.verdict is Verdict.SUPPORTED
    assert result.supporting_chunk_ids == (_CHUNK_A, _CHUNK_B)
    assert result.signals["conflicts"] == []


def test_per_night_qualifier_does_not_hide_a_conflict() -> None:
    """``上限为每晚 500`` 与 ``上限为 800`` 是同一事实的两种写法，必须判冲突。

    计价口径（每晚/每人）夹在中心词与数字之间，若不去掉，"为每晚"会被当成中心词，
    两条对不上就被静默放过——用户只会看到 500，而另一段写的是 800。
    """
    result = assess(
        "住宿上限是多少？",
        [
            _evidence(chunk_id=_CHUNK_A, content="一线城市住宿上限为每晚 500 元。"),
            _evidence(chunk_id=_CHUNK_B, content="一线城市住宿上限为 800 元/晚。"),
        ],
    )

    assert result.verdict is Verdict.CONFLICTING
    assert "500 元" in result.reason
    assert "800 元" in result.reason


def test_different_connectors_still_conflict() -> None:
    """``上限为 500`` 与 ``上限是 800`` 只差连接字，仍是同一条事实——必须判冲突。

    守住 `_TRAILING_CONNECTORS` 的剥离范围：把实字（上/下/限）也放进去，主语中心词会被
    `str.rstrip` 剃掉，两条冲突就会因为"主语不同"而被放过。
    """
    result = assess(
        "住宿上限是多少？",
        [
            _evidence(chunk_id=_CHUNK_A, content="一线城市住宿上限为 500 元/晚。"),
            _evidence(chunk_id=_CHUNK_B, content="一线城市住宿上限是 800 元/晚。"),
        ],
    )

    assert result.verdict is Verdict.CONFLICTING
    assert "500 元" in result.reason
    assert "800 元" in result.reason


def test_different_subjects_do_not_conflict() -> None:
    """年假 5 天 vs 病假 3 天：同类但不同主语，不是冲突。"""
    result = assess(
        "年假有几天？",
        [
            _evidence(chunk_id=_CHUNK_A, content="年假为 5 天。"),
            _evidence(chunk_id=_CHUNK_B, content="病假为 3 天。"),
        ],
    )

    assert result.verdict is not Verdict.CONFLICTING
    assert result.signals["conflicts"] == []
    assert "冲突" not in result.reason


def test_workday_and_natural_day_are_not_a_conflict() -> None:
    """``10 个工作日`` 与 ``14 天`` 同属"期限"但单位不同类，不能判冲突。"""
    result = assess(
        "出差住宿最多几天？",
        [
            _evidence(chunk_id=_CHUNK_A, content="出差住宿最多 10 个工作日。"),
            _evidence(chunk_id=_CHUNK_B, content="出差住宿最多 14 天。"),
        ],
    )

    assert result.verdict is not Verdict.CONFLICTING
    assert result.signals["conflicts"] == []


def test_currency_units_are_normalised_before_comparing() -> None:
    """``1 万元`` 与 ``10000 元`` 是同一金额的两种写法，不是冲突。"""
    result = assess(
        "住宿上限是多少元？",
        [
            _evidence(chunk_id=_CHUNK_A, content="一线城市住宿上限为 1 万元。"),
            _evidence(chunk_id=_CHUNK_B, content="一线城市住宿上限为 10000 元。"),
        ],
    )

    assert result.verdict is Verdict.SUPPORTED
    assert result.supporting_chunk_ids == (_CHUNK_A, _CHUNK_B)
    assert result.signals["conflicts"] == []


def test_calendar_year_is_not_a_duration_anchor() -> None:
    """``2023 年``/``2024 年`` 是日历年份，不能当成"期限"参与冲突判定。"""
    result = assess(
        "制度执行时间是多少？",
        [
            _evidence(chunk_id=_CHUNK_A, content="本制度自 2023 年起执行。"),
            _evidence(chunk_id=_CHUNK_B, content="本制度自 2024 年起执行。"),
        ],
    )

    assert result.verdict is not Verdict.CONFLICTING
    assert result.signals["conflicts"] == []


# ---------------------------------------------------------------- 限定语分档
#
# 来历：真实语料里「应届毕业生（本科）…试用期 1 个月」与「凡进入我公司员工…试用期
# 3 个月」共享中心词「用期」与主题「试用」，被判成"同一事实的两个取值"→ 拒答，
# 实际是**两条适用范围不同的规定**。同理探亲假按"配偶分居/未婚父母/已婚父母"分档。
# 实测（`eval/baselines/baseline-20261108-r3`）：`fact-02`、`fact-06` 都是这么被拒的。
#
# 判据用「锚点所在分句去掉锚点后的文本相似度」：同一事实换个数字，残余文本几乎一致；
# 不同分档的两条规定，残余文本差得远。阈值见 `_SAME_FACT_SIMILARITY`。


def test_qualifier_scoped_values_are_not_a_conflict() -> None:
    """「本科岗 1 个月」与「其他员工 3 个月」是两条规定，不是同一条事实的两个取值。"""
    result = assess(
        "应届本科毕业生试用期是多久？",
        [
            _evidence(
                chunk_id=_CHUNK_A,
                content="应届毕业生（本科）：合同期限最低 3 年，试用期 1 个月。",
            ),
            _evidence(
                chunk_id=_CHUNK_B,
                content="凡进入我公司的员工，必须经过考试与面试，试用期 3 个月。",
            ),
        ],
    )

    assert result.verdict is not Verdict.CONFLICTING
    assert result.signals["conflicts"] == []


def test_parallel_clauses_for_different_situations_are_not_a_conflict() -> None:
    """探亲假按"配偶分居/未婚父母/已婚父母"分档给天数，三种情形不互相矛盾。"""
    result = assess(
        "员工与配偶分居两地，每年可以享受多少天探亲假？",
        [
            _evidence(
                chunk_id=_CHUNK_A,
                content="（1）与配偶分居两地，每年员工可享受一次为期 30 天的探亲假。",
            ),
            _evidence(
                chunk_id=_CHUNK_B,
                content="（3）已婚而父母均在外地居住，每四年员工可享受为期 20 天的探亲假。",
            ),
        ],
    )

    assert result.verdict is not Verdict.CONFLICTING
    assert result.signals["conflicts"] == []


def test_conflict_in_unasked_category_does_not_block_the_answer() -> None:
    """问**工资**时，片段间"试用期 1 个月 vs 6 个月"的差异不是这条事实的冲突。

    真实样本 `fact-02`：问句要的是"应届本科试用期工资"，冲突却落在"试用期月数"上，
    结果一条本可回答的工资问题被拒。冲突必须落在**被问到的那类事实**上。
    """
    result = assess(
        "应届本科毕业生试用期的工资是多少？",
        [
            _evidence(
                chunk_id=_CHUNK_A,
                content="应届毕业生（本科）：试用期 1 个月，工资 1800 元。",
            ),
            _evidence(
                chunk_id=_CHUNK_B,
                content="凡进入我公司的员工须经考试与面试，试用期 6 个月。",
            ),
        ],
    )

    assert result.verdict is not Verdict.CONFLICTING
    assert result.signals["conflicts"] == []


def test_yes_no_question_about_a_topic_word_is_not_a_quantity_question() -> None:
    """ "能不能把费用拆成几张单子"是**是非题**：问的是允不允许，不是要一个数值。

    真实样本 `syn-03`：问句里的"几"是"几张"的量词、"费用"只是主题词，`_QUANTITY_CUES`
    却把两个都当数值线索 → 要求片段必须有数字 → 一段合规条款（通篇无数字）被拒。
    """
    result = assess(
        "同一笔费用能不能拆成几张单子分开报？",
        [_evidence(content="费用报销应及时并严禁分拆，属于同一项目、同一性质的费用按单笔报销。")],
    )

    assert result.verdict is Verdict.SUPPORTED
    assert result.signals["quantity_asked"] is False


# ---------------------------------------------------------------- 中文数字锚点
#
# 来历：真实语料里「财务部门每月至少对库存票据抽点一次」是这句话唯一的数量信息，
# 而 `_NUMERIC_ANCHOR` 原先只认阿拉伯数字 → 该片段 anchors=0 → 问句又在问数值
# （"多久"）时被 `covered = len(hits) >= 2 and bool(view.anchors)` 判成不足。
# 实测（`eval/baselines/baseline-20261108-r3`）：`fact-04` 就是被这一条拒掉的。


def test_chinese_numeral_is_a_numeric_anchor() -> None:
    """``抽点一次`` 里的"一次"是数量锚点：中文数字与阿拉伯数字同权。"""
    result = assess(
        "库存票据多久抽点一次？",
        [
            _evidence(
                content="财务部门每月至少对库存票据抽点一次，并编制盘点表。",
            )
        ],
    )

    assert result.verdict is Verdict.SUPPORTED
    assert result.signals["supported_positions"] == [1]


def test_chinese_and_arabic_forms_of_the_same_quantity_are_equal() -> None:
    """``3 个月`` 与 ``三个月`` 是同一个值，判冲突时不能当成两个不同取值。"""
    result = assess(
        "试用期最长多久？",
        [
            _evidence(chunk_id=_CHUNK_A, content="合同期限满三年的，试用期不得超过 3 个月。"),
            _evidence(chunk_id=_CHUNK_B, content="合同期限满三年的，试用期不得超过三个月。"),
        ],
    )

    assert result.verdict is not Verdict.CONFLICTING
    assert result.signals["conflicts"] == []


def test_compound_chinese_numeral_is_parsed_as_one_value() -> None:
    """``三十天`` 是 30，不是 3 与 10 两个锚点。"""
    result = assess(
        "旷工多少天作除名处理？",
        [_evidence(content="连续旷工超过三十天，作除名处理。")],
    )

    assert result.verdict is Verdict.SUPPORTED


# ---------------------------------------------------------------- 空证据 / 边界


def test_empty_evidence_is_insufficient_with_fixed_reason() -> None:
    """空序列：``reason`` 固定 ``no evidence``（计划 §7.1 第一种结果）。"""
    result = assess("年假有几天？", [])

    assert result.verdict is Verdict.INSUFFICIENT
    assert result.reason == "no evidence"
    assert result.supporting_chunk_ids == ()
    assert result.signals["evidence_count"] == 0
    assert result.signals["topic_chunk_positions"] == []
    assert result.signals["conflicts"] == []


def test_empty_question_is_insufficient() -> None:
    """空问题没有任何锚点，任何放行都是瞎猜。"""
    result = assess("   ", [_evidence(content="年假为 5 天。")])

    assert result.verdict is Verdict.INSUFFICIENT
    assert result.reason == "empty question"


def test_missing_required_fact_blocks_support() -> None:
    """数值锚点覆盖了，但 required_facts 缺失 → 仍判不足，并说清缺哪个。"""
    result = assess(
        "年假是 5 天吗？",
        [_evidence(content="年假为 5 天。")],
        required_facts=["仅限转正后"],
    )

    assert result.verdict is Verdict.INSUFFICIENT
    assert result.signals["missing_required_facts"] == ["仅限转正后"]
    assert "仅限转正后" in result.reason
    assert "仅限转正后" in result.unsupported_claims[0]


def test_required_facts_must_all_land_in_the_same_chunk() -> None:
    """两条片段各有一半事实不算数：模型是逐段读的，拼起来的是猜测。"""
    result = assess(
        "年假是 5 天吗？",
        [
            _evidence(chunk_id=_CHUNK_A, content="年假为 5 天。"),
            _evidence(chunk_id=_CHUNK_B, content="年假自次年起生效。"),
        ],
        required_facts=["5 天", "次年起"],
    )

    assert result.verdict is Verdict.INSUFFICIENT
    # 最接近的是第一段（锚点齐、只缺"次年起"），reason 应该指名道姓。
    assert "次年起" in result.reason


def test_blank_required_fact_is_ignored() -> None:
    """空字符串 fact 在任何文本里都"命中"，留着会把整条判据静默废掉。"""
    result = assess(
        "年假是 5 天吗？", [_evidence(content="年假为 5 天。")], required_facts=["", "  "]
    )

    assert result.verdict is Verdict.SUPPORTED


def test_page_numbers_may_be_none() -> None:
    """txt/md 类文档没有页码；页码缺失不影响能否回答。"""
    result = assess(
        "年假是 5 天吗？",
        [_evidence(content="年假为 5 天。", page_no=None, page_end_no=None)],
    )

    assert result.verdict is Verdict.SUPPORTED
    assert result.supporting_chunk_ids == (_CHUNK_A,)


def test_low_score_still_supported() -> None:
    """召回分不参与判定：低分但覆盖了锚点，就该放行（阈值不是真理性标准）。"""
    result = assess(
        "年假是 5 天吗？",
        [_evidence(content="年假为 5 天。", score=0.01)],
    )

    assert result.verdict is Verdict.SUPPORTED
    assert result.signals["evidence_scores"] == [pytest.approx(0.01)]


# ---------------------------------------------------------------- signals 契约


def test_signals_shape_is_stable_and_json_friendly() -> None:
    """``signals`` 的键集合是稳定契约，且只装 JSON 原生类型（能原样进日志/事件体）。"""
    signals = assess(
        "一线城市住宿报销上限是多少？",
        [
            _evidence(chunk_id=_CHUNK_A, content="一线城市住宿报销上限为 500 元/晚。"),
            _evidence(chunk_id=_CHUNK_B, content="一线城市住宿报销上限为 800 元/晚。"),
        ],
    ).signals

    assert set(signals) == {
        "evidence_count",
        "evidence_scores",
        "question_anchor_texts",
        "question_fragment_count",
        "quantity_asked",
        "topic_chunk_positions",
        "scope_mismatch_positions",
        "missing_anchor_texts",
        "missing_required_facts",
        "conflicts",
        "supported_positions",
        "supported_values",
    }
    # 序列化成功即证明里面没有 UUID / 枚举 / dataclass 实例。
    json.dumps(signals, ensure_ascii=False)


# ---------------------------------------------------------------- 正文二次比对


def test_split_unsupported_claims_returns_every_sentence_when_insufficient() -> None:
    """判不足时整段正文都是猜测，逐句豁免等于承认其中某句有据。"""
    assessment = assess(
        "一线城市住宿报销上限是多少？",
        [_evidence(content="住宿报销规定：员工出差应选择合标酒店，超标部分由个人承担。")],
    )
    claims = split_unsupported_claims("年假为 5 天。另有 3 天带薪病假。", assessment)

    assert claims == ("年假为 5 天", "另有 3 天带薪病假")


def test_split_unsupported_claims_only_flags_disputed_values_when_conflicting() -> None:
    """冲突时只标出带冲突取值的句子，纯叙述句不冒充结论。"""
    assessment = assess(
        "一线城市住宿报销上限是多少？",
        [
            _evidence(chunk_id=_CHUNK_A, content="一线城市住宿报销上限为 500 元/晚。"),
            _evidence(chunk_id=_CHUNK_B, content="一线城市住宿报销上限为 800 元/晚。"),
        ],
    )
    claims = split_unsupported_claims(
        "一线城市住宿报销上限为 500 元。具体以现行制度为准。", assessment
    )

    assert claims == ("一线城市住宿报销上限为 500 元",)


def test_split_unsupported_claims_flags_invented_number_when_supported() -> None:
    """判支持时，正文里多编出来的数字仍要被挑出来。"""
    assessment = assess(
        "一线城市住宿报销上限是 500 元吗？",
        [_evidence(content="一线城市住宿报销上限为 500 元/晚，超出部分自理。")],
    )
    claims = split_unsupported_claims("住宿报销上限为 500 元。另有 800 元的餐补。", assessment)

    assert assessment.verdict is Verdict.SUPPORTED
    assert claims == ("另有 800 元的餐补",)


def test_split_unsupported_claims_handles_empty_answer_and_foreign_signals() -> None:
    """空正文返回空元组；signals 若是反序列化来的畸形数据也不许抛。"""
    supported = EvidenceAssessment(
        verdict=Verdict.SUPPORTED,
        supporting_chunk_ids=(),
        reason="手工构造",
        signals={"conflicts": [{"values": "not-a-list"}], "supported_values": ["bad"]},
    )

    assert split_unsupported_claims("", supported) == ()
    assert split_unsupported_claims("年假为 5 天。", supported) == ("年假为 5 天",)
