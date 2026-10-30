"""用**真实语料**钉住证据门控的三态口径（Day03 现场问题的事后校准）。

来历：2026-10-30 用 KB ``1556633e-fff3-4a27-8731-adc00d2ff5eb``（"Day03 评测库 A"，
3 份真实文档 / 98 条片段）跑通链路后，对「一线城市住宿标准是多少」观测到
citations=0、``verdict=INSUFFICIENT``。当时的怀疑是"门控对真实语料一律判不足"。
逐层排查（``.tmp_ref/gate_probe.py``，真实 search_hybrid + assess）证明：

- 检索层正常：top_k=6 返回 6 条，``degraded={}``，rrf 0.0161–0.0323，全部带现行版本；
- 门控不是"一律不足"：同一批语料里「探亲假有几天」「差旅费报销标准是多少」等
  7 条问法都判了 SUPPORTED；
- 本问题被拒答是**语料决定的**：全库 ``LIKE '%住宿%'``、``'%一线%'``、``'%城市%'``
  命中数都是 0（去空白后仍为 0），差旅相关条款只讲填单与时限，没有任何住宿金额标准。

所以下面的用例不测"改了什么"，而是测"现行判据在真实文本上给出的是哪个结论"：
有直接证据 → ``SUPPORTED``；只有主题相关（没写数字）→ ``INSUFFICIENT``；两段对同一条
事实给了不同取值 → ``CONFLICTING``。

fixture 的出处（原文照抄，仅去掉与本文件无关的段落边界）：
- ``_CHUNK_5_ATTENDANCE``  ← doc ``2a72b9e0-aab3-4550-9bd9-875b58970100``
  （员工手册v2.docx）chunk_ordinal=5，chunk_id ``34e8d648-2378-413c-9d5e-98f6a6bf3901``
- ``_CHUNK_8_LEAVE``      ← 同文档 chunk_ordinal=8，chunk_id
  ``1f9f7ef8-5673-48c5-b81f-c255cc5e44b7``
- ``_CHUNK_9_LEAVE``      ← 同文档 chunk_ordinal=9，chunk_id
  ``0df64ab3-edd5-4d5d-9565-a17764a703f2``
- ``_CHUNK_7_SALARY``     ← 同文档 chunk_ordinal=7，chunk_id
  ``cca518f9-1b1a-434b-8d30-97b6c5845156``
- ``_CHUNK_32_FINANCE``   ← 同文档 chunk_ordinal=32，chunk_id
  ``75f67889-9fce-4a45-aa7e-dd0e70dd122d``

这些是通用制度条款，不含个人信息，无需脱敏。
"""

from __future__ import annotations

from uuid import UUID

from pyknora.services.rag.evidence import Evidence, Verdict, assess

# ---------------------------------------------------------------- fixture（真实原文）

#: 员工手册 chunk#5：迟到扣款那一段（"每次扣除薪金50元"）。
_CHUNK_5_ATTENDANCE = """0元，违纪三次以上除作上述处罚外，将劝其辞职。

无故不上班或故意不记录考勤的作旷工处理，并给予不超过当月薪金20%的经济处罚。连续旷工超过十五天，或一年内累计旷工超过三十天者，作除名处理，公司不负责其一切善后事宜。

迟到或早退15分钟以上，两小时以下者，每次扣除薪金50元，迟到或早退超过两小时按旷工处理。

请假须填写请假单，员工层由所在部门的经理签署意见，管理人员由上一级领导签署意见，获得批准并安排好工作后，才可离开工作岗位，同时请假单应交行政部备案。"""

#: 员工手册 chunk#7：岗位工资 + 福利/假期开头（检索第 2 名命中的那段）。
_CHUNK_7_SALARY = """，公司在认为必要时才进行调整。现行岗位工资标准请见《薪酬制度》。 

(3)一般情况下，员工薪金收入的变动取决于岗位发生的变化。

（4）员工收入一般取决于员工所从事的岗位，不同岗位将带给员工不同的薪金收入。员工收入的增长，主要决定于员工努力担当更高层次的岗位来取得。

7.岗位薪金调整，新的岗位薪金从公司下发有关通知的下月一日起执行。

8.公司每半月对员工进行考核，考核汇总结果作为年终考核。员工的年终考核结果与员工岗位工资剩余部分紧密相关。

第四章 福利

假期

员工可享受本手册规定的重要节假日，期间照常支付员工的薪金。

员工转正后可享受婚假、丧假、产假及护理假；在公司工作满一年后，员工可以按规定享受探亲假、年休假，这些假期均为有薪假期。"""

#: 员工手册 chunk#8：病事假上限 + 重要节假日 + 探亲假天数（"为期20天"在最后一段）。
_CHUNK_8_LEAVE = """1）员工提前一个月向所在部门负责人和行政部申报拟休假的种类和时间；

（2）具体休假时间，员工层由所在部门与个人协商后统一安排；经理层及以上由所在部门与行政部协商安排。休假期间若遇公众假日或法定假日，不另增加休假时间。

凡符合以下情况之一的，不享受当年年休假或探亲假：

（1）一年内一次性病假超过25天或累计病假超过40天；

（2）一年内一次性事假超过15天或累计事假超过30天；

（3）一年内病、事假相加超过50天；

（4）一年内休产假超过30天。

重要节假日

元 旦 放假1天

春 节 放假10天

五一节 放假7天

国庆节 放假7天

探亲假

（1）与配偶分居两地，每年员工可享受一次为期30天的探亲假；也可以选择分散进行，但是一年不得超过30天；

（2）未婚、父母均在外地居住，每年员工可享受为期20天的探亲假；"""

#: 员工手册 chunk#9：探亲假/婚假/丧假/病假条款（检索第 1 名命中的那段）。
#: 注意它含 9 个数字锚点，但主题上只有"标准"两个字与住宿问题擦边。
_CHUNK_9_LEAVE = """每年员工可享受为期20天的探亲假；

（3）已婚而父母均在外地居住，每四年员工可享受为期20天的探亲假，此假应在第四年享受；

（4）享受探亲假和国定假日者，骨干员工采取交通费一律报效原则（交通工具标准：火车硬卧，汽车，飞机采用特事特批），其他员工（在公司工作一年以上的）只报销春节交通费。

7．婚假

公司对依国家婚姻法履行正式登记手续的转正员工，给婚假3天，如符合男25周岁、女23周岁及以上初婚的晚婚条件，增加为10天假期。但员工必须在结婚注册日后5个月内取假。

8．丧假

直系亲属（指配偶、子女、父母或配偶之父母）死亡，公司给假5天，离家太远者请假时间酌情处理（请假最多不能超过8天）"""

#: 员工手册 chunk#32：财务管理制度里的"票据报效办法"（通篇没有任何"数字+单位"）。
#: 主题上命中「报销」「标准」两个片段，正是计划 §6.1 说的"相关但不足"。
_CHUNK_32_FINANCE = """第三章  财务管理制度

本手册中所包含的财务管理制度，为各部门日常工作中所需，非财务核算的内部制度。目前只包含票据报效办法，其他方面有待于今后实践当中逐步完善。

票据报效办法

第一条  票据的取得

凡公司员工从事有关企业的正常经营活动，发生相关成本费用项目的，必须依法取得正规发票。

对于假发票、内容不相关的发票、过期发票等不符合报销内容的，财务有权拒绝接受。

第二条  正规票据的含义

1、由税务机关监制的正式发票，包括增值税发票、工业普通发票、商业普通发票等；

2、行政失业单位提供的行政事业收费手锯；

由财税部门监制的统一正式收据。

第三条  财务审核票据的标准与依据

1、按照公司正常业务活动所取得的，发票所反映的内容必须是公司发生的正常业务；"""

#: 真实语料里**没有**的词（全库 LIKE 计数为 0，去空白后仍为 0）。
#: 用它做 fixture 的完整性断言：如果哪天语料更新了，本文件的前提就该重审。
_ABSENT_TERMS = ("住宿", "一线", "城市", "酒店", "房费")

_CHUNK_5 = UUID("34e8d648-2378-413c-9d5e-98f6a6bf3901")
_CHUNK_7 = UUID("cca518f9-1b1a-434b-8d30-97b6c5845156")
_CHUNK_8 = UUID("1f9f7ef8-5673-48c5-b81f-c255cc5e44b7")
_CHUNK_9 = UUID("0df64ab3-edd5-4d5d-9565-a17764a703f2")
_CHUNK_32 = UUID("75f67889-9fce-4a45-aa7e-dd0e70dd122d")
#: 冲突用例的第二条是构造的（见 `test_conflicting_values...` 的说明），给它一个独立 id。
_CHUNK_5_TWIN = UUID("55555555-5555-4555-8555-555555555555")
_DOC = UUID("2a72b9e0-aab3-4550-9bd9-875b58970100")
_VERSION = UUID("44444444-4444-4444-8444-444444444444")


def _evidence(*, chunk_id: UUID, content: str, chunk_ordinal: int = 0) -> Evidence:
    """构造一条片段；score 与判定无关（门控刻意不看相似度阈值），给个占位值即可。"""
    return Evidence(
        chunk_id=chunk_id,
        document_id=_DOC,
        active_document_version_id=_VERSION,
        index_version=1,
        chunk_ordinal=chunk_ordinal,
        content=content,
        score=0.03,
        page_no=None,
        page_end_no=None,
    )


# ---------------------------------------------------------------- 三态


def test_supported_on_real_clause_that_states_the_number() -> None:
    """有直接证据就放行：问"探亲假有几天"，真实条款里就写着"为期20天"。

    这条同时是"门控并非一律判不足"的证词——同一份语料、同样的 ``assess`` 调用，
    只要片段里真有那个数字，结论就是 ``SUPPORTED``。
    """
    result = assess("探亲假有几天", [_evidence(chunk_id=_CHUNK_8, content=_CHUNK_8_LEAVE)])

    assert result.verdict is Verdict.SUPPORTED
    assert result.supporting_chunk_ids == (_CHUNK_8,)
    # asked_quantity=True 分支要求"片段里确实有数字"——这里 20 天/30 天 都在。
    assert result.signals["quantity_asked"] is True
    assert result.signals["supported_positions"] == [1]


def test_reported_lodging_question_abstains_on_real_retrieval_chunks() -> None:
    """现场问题的回归钉：真实召回的前两名**都不含住宿事实**，门控必须拒答。

    这两段是 ``search_hybrid(top_k=6)`` 的真实第 1、2 名（rrf=0.03227 / 0.02996）。
    它们各自只与问句共享 1 个主题片段（"标准"），而问句在问数值、自身没有数字锚点，
    于是落在 ``covered = len(hits) >= 2 and bool(view.anchors)`` 的 1 < 2 上。

    断言的重点不是"实现怎么写"，而是：**拒答的根因在语料，不在阈值**——
    同一个问题换个有答案的问法就能通过（见上一条用例）。
    """
    for content in (_CHUNK_9_LEAVE, _CHUNK_7_SALARY):
        assert not any(term in content for term in _ABSENT_TERMS), (
            "fixture 前提变了：语料里出现住宿词"
        )

    result = assess(
        "一线城市住宿标准是多少",
        [
            _evidence(chunk_id=_CHUNK_9, content=_CHUNK_9_LEAVE, chunk_ordinal=9),
            _evidence(chunk_id=_CHUNK_7, content=_CHUNK_7_SALARY, chunk_ordinal=7),
        ],
    )

    assert result.verdict is Verdict.INSUFFICIENT
    assert result.supporting_chunk_ids == ()
    assert result.reason == "证据不足以判定"
    # 门控**看得见**主题相关（两段都至少共享 1 个片段），但相关不等于能答。
    assert result.signals["topic_chunk_positions"] == [1, 2]
    assert result.signals["supported_positions"] == []
    # 问句自己要的是数值，而它没有数字锚点 —— 这是 EV-02 生效的前提。
    assert result.signals["quantity_asked"] is True
    assert result.signals["question_anchor_texts"] == []


def test_topic_relevant_but_numberless_chunk_is_insufficient() -> None:
    """只有主题相关 → INSUFFICIENT：真实"票据报效办法"命中 2 个片段却一个数字都没有。

    计划 §6.1 要拦的就是这种片段：它谈报销、谈审核标准，看起来切题，但没写任何金额。
    若门控只数主题片段，这段会被 ``len(hits) >= 2`` 放行，答案只能靠模型编。
    """
    result = assess(
        "住宿报销标准是多少",
        [_evidence(chunk_id=_CHUNK_32, content=_CHUNK_32_FINANCE, chunk_ordinal=32)],
    )

    assert result.verdict is Verdict.INSUFFICIENT
    assert result.supporting_chunk_ids == ()
    # 主题命中了 ≥2 个片段（"报销"、"标准"），唯独没有"数字+单位"。
    assert result.signals["topic_chunk_positions"] == [1]
    assert result.signals["quantity_asked"] is True
    assert result.signals["missing_anchor_texts"] == []


def test_conflicting_values_win_over_mutual_support() -> None:
    """冲突优先于支持：两段对同一条事实给了不同金额 → CONFLICTING，不许挑一个发出去。

    说明：真实语料里**没有**跨片段的同类冲突（``.tmp_ref/conflict_scan.py`` 扫了 98 条
    片段，同类别+同中心词+不同取值的组全部落在**同一片段内部**的不同条款里），
    所以第二条是把真实句子里的"50元"改成"80元"构造的——改动仅此一个数字，
    其余逐字照抄。这条用例钉的是判据（同类 + 同中心词 + 主语与问句相交），
    不是语料事实：语料里没有真冲突，这正是本次拒答没有被判 CONFLICTING 的原因。
    """
    twin = _CHUNK_5_ATTENDANCE.replace("每次扣除薪金50元", "每次扣除薪金80元")
    assert twin != _CHUNK_5_ATTENDANCE

    result = assess(
        "迟到扣除薪金多少",
        [
            _evidence(chunk_id=_CHUNK_5, content=_CHUNK_5_ATTENDANCE, chunk_ordinal=5),
            _evidence(chunk_id=_CHUNK_5_TWIN, content=twin, chunk_ordinal=5),
        ],
    )

    assert result.verdict is Verdict.CONFLICTING
    assert set(result.supporting_chunk_ids) == {_CHUNK_5, _CHUNK_5_TWIN}
    assert "存在冲突" in result.reason
    assert "50元" in result.reason
    assert "80元" in result.reason
    conflicts = result.signals["conflicts"]
    assert isinstance(conflicts, list) and len(conflicts) == 1
    assert conflicts[0]["category"] == "money"
    assert conflicts[0]["values"] == [50.0, 80.0]
