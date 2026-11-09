"""证据门控：把「召回到了相关片段」和「这些片段足以回答」分成两件事。

**为什么要有这个模块**（计划 §6.1）：现有链路里 `chunks` 非空就等于"能回答"——只要
召回 5 条就调模型（`services/rag/answer.py`）。但"主题相关"与"数值充分"不是一回事：
问"年假有几天"召回到一段讲年假制度的正文、通篇没写天数，模型会顺着常识补一个数字，
读起来像答案，可依据并不存在。判定必须发生在**调模型之前**：先把"能答"和"不能答"
分开，才有资格让模型说话。

三态与后续处理（计划 §6.3 / §7.1）：

- ``SUPPORTED``：有片段覆盖了问句的**全部数值锚点**（问句没有数字时，退化为
  "覆盖至少 2 个非停用词主题片段"，且在问数值时片段里必须真的有数字），
  且 ``required_facts`` 全部落在**同一个**片段里 → 正常生成。
- ``INSUFFICIENT``：只有主题相关、锚点对不上；或片段只适用于上层角色而问的
  是普通员工；或压根没有证据 → 不调模型，直接拒答（``done.status=ABSTAINED``，
  ``citations=[]``）。
- ``CONFLICTING``：不同片段对**同一个被问到的事实**给出不同取值 → 不调模型，
  把两处取值与片段序号列进 ``reason``，不擅自判"新的那个一定对"（§7.1 第二种结果：
  冲突要指出来，而不是替用户裁决）。

逐条对位计划 §6.4 的用例（本模块的验收口径）：

======  ============================================  ==================================
EV-01   片段里就有金额/天数，问句锚点被全量覆盖      ``SUPPORTED``
EV-02   只主题相关、没写具体数值                     ``INSUFFICIENT``（数值锚点缺失）
EV-03   片段仅适用于经理，问的是普通员工             ``INSUFFICIENT``（适用对象不匹配）
EV-04   两个版本对同一事实给了不同数值               ``CONFLICTING``，reason 列值 + 序号
======  ============================================  ==================================
空序列没有证据同样走 ``INSUFFICIENT``，``reason`` 固定为 ``no evidence``（计划 §7.1 第一种）。

**这里刻意不用相似度阈值当判据**。理由不是"阈值难调"，而是阈值回答的是另一个问题：
``rrf_score`` 高只说明"这段文本的词面与问句像"，而门控要判的是"这段文本里有没有问句要的
那个数"。一个 0.92 分的片段完全可以是"关于年假的一段制度说明、通篇没写天数"；反过来，
一个 0.3 分的片段可能正好是那张写满数字的表格。用阈值放行等于把"像"当"对"——这正是
讲义 §6.3 点名不许做的事，也是计划 D12 的立论。所以：

- 本模块**不接收** score 阈值参数，``Evidence.score`` 只进 ``signals["evidence_scores"]``
  供观测，**不参与判定**；
- 阈值在本项目里只有一个合法用途——丢弃明显无关的尾部片段（``evidence_min_score``，
  默认 0.0 = 不过滤）。那是召回预算问题，由基线数据决定，落地在调用方（计划 §6.3 第 1 层），
  不在这里。

**保守原则**（讲义原话，计划 §6.3 结尾）：默认不放行。只有"确认覆盖"才 ``SUPPORTED``，
任何一条判据不满足就 ``INSUFFICIENT``；冲突优先于支持——两条片段各说一个值、单看每条都
"命中"，此时若先判 ``SUPPORTED`` 就会把一个互相矛盾的事实当成确定答案发出去。

判定顺序（实现顺序，不是文档顺序）：无证据 → 空问题 → 适用对象过滤 → 冲突 → 支持 → 不足。
把"冲突"排在"支持"前面是刻意的，理由见上一段。

已知边界（先登记，不在本轮解决）：

1. 只认**带单位的**数字。中文数字（"一次""三十天"）与阿拉伯数字同权（见 `_CN_DIGITS`）：
   语料里"抽点一次"就是那句话唯一的数量信息，只认阿拉伯数字会让这类片段 anchors=0、
   问句又在问数值时必然被判不足（实测 `fact-04` 因此被拒答）。裸数字仍然不抽——
   "第 3 章""2024"不是可核验的数量事实；逐位朗读式年份（"二零二四"）也刻意不解析。
2. 主题命中是**二元组词面**判断，同义改写（"住宿上限" vs "差旅房费标准"）命不中，结果
   偏向更保守的 ``INSUFFICIENT``。判宽才是危险方向，所以这个偏差可接受；同义词表/向量
   主题命中登记为下一轮项。
3. ``"5月1日"`` 这类日期会被读成"5 个月"+"1"；``"2024 年"`` 这类年份已按年份排除
   （见 `_numeric_anchors`）。日期误判只会让判定更保守（多一个覆盖不了的锚点），
   不会把错误答案放出去。
4. ``required_facts`` 用"去掉全部空白后的子串包含"判命中：它不认同义、不认换序。
   callers 应当传**片段正文里真会出现**的短串（金额、期限、专有名词）。
5. 冲突的"同一事实"靠两道判据：**中心词**（`_NumericAnchor.head`，紧贴数字的二元组）
   加**锚点所在分句去掉锚点后的文本相似度**（`_SAME_FACT_SIMILARITY`）。
   "住宿上限 500"与"住宿下限 300"是两条规定，不判冲突；"每晚 500"与"上限 800"这类
   只差计价口径的写法靠剥掉 `_HEAD_FILLERS` 归到同一中心词，仍会判冲突。
   相似度这一道专挡**并列分档**："应届本科试用期 1 个月"与"其他员工试用期 3 个月"
   共享中心词"用期"却依适用对象不同分档，判冲突会让用户拿不到任何一条（实测
   `fact-02`/`fact-06` 因此被拒答）。残留误报在近义中心词与整句改写上，残留漏报在
   "句式雷同但分档不同"上。登记为下一轮项：条款号/实体识别，或把冲突降级成
   "并列展示"而不是拒答。

内部实现里所有 `signals` 只装 JSON 原生类型（str/int/float/list/dict），不放 UUID、不放
枚举：它要能原样进日志与 SSE 事件体，见 `EvidenceAssessment.signals` 的注释。
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final
from uuid import UUID

from pyknora.schemas.locator import Locator

# ---------------------------------------------------------------- 数据形状


class Verdict(StrEnum):
    """门控结论。

    取值用大写，与 `core/errors.py` 的 `ErrorCode` 同一书写习惯；`StrEnum` 让它可以
    直接进 ``done.status`` / 日志 / SSE 事件体而不再做一次映射（``str`` 子类会被
    JSON 序列化成字符串本身）。
    """

    SUPPORTED = "SUPPORTED"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICTING = "CONFLICTING"


@dataclass(frozen=True, slots=True)
class Evidence:
    """一条待判定的片段；字段名与 `RetrievedChunk` 对齐（计划 §6.2）。

    为什么不直接复用 `RetrievedChunk`：判定层不该反向依赖 HTTP 出参模型——出参随接口
    变（D15 就给 `RetrievedChunk`/`Citation` 加了 ``active_document_version_id``），
    而门控是纯函数，评测脚本、离线 runner、chat 三个入口都要能构造它。这里把需要的
    字段摊平，等价于给判定层一个**稳定输入契约**，schema 再变也不用改门控。
    """

    chunk_id: UUID
    document_id: UUID
    active_document_version_id: UUID
    """该片段所属的**当前生效**文档版本。

    计划 §6.5/CIT-03 要求"引用必须来自现行版"，而版本号只有这里能带：判定时它不参与
    规则计算，但会随 ``supporting_chunk_ids`` 一起被调用方拿去和
    ``GET /documents/{id}.active_document_version_id`` 做等式校验。放在输入里是为了
    让"旧版片段"有机会在门控这一层就被看见，而不是等引用回填时才暴露。
    """

    index_version: int
    chunk_ordinal: int
    content: str
    score: float
    """召回分（`rrf_score`）。**只观测不判定**，理由见模块 docstring。"""

    page_no: int | None = None
    page_end_no: int | None = None
    """页码允许为 ``None``：txt/md 类文档没有"页"这个概念，编一个 1 出来更糟。

    门控不读这两个字段——引用能不能点开是前端/契约的事，能不能**答**与页码无关。
    测试里专门有一条覆盖"页码为 None 时判定不变"。
    """

    locator: Locator | None = None
    """结构化位置（`chunks.locator`，迁移 0014 / ADR 0008 D2）。

    与 `page_no` 同一类字段：门控**不读**它，它只是随证据一路带到引用里的载荷。
    放在这里是因为 `Evidence` 是判定层与调用方之间唯一的稳定输入契约（见类注释）——
    引用要跳到页 / `Sheet1!B2:C4` / 幻灯片 5 / 坐标框，而门控这一跳不带，后面
    就没有任何地方能把它补回来：`Citation.locator` 的来源只能是这里。

    `None` 表示来源本身没有位置信息（txt/md、老数据、解析器没读到），不是"没查"；
    与 `page_no` 一样，缺它只影响引用能不能精确回指，不影响判定。
    """


@dataclass(frozen=True, slots=True)
class EvidenceAssessment:
    """一次门控的结论。

    ``supporting_chunk_ids`` 的含义随 ``verdict`` 变，三种读法必须分清，否则调用方会
    把"冲突的两处"或"一句都没有"当成"支持列表"：

    - ``SUPPORTED``：真正支撑结论的片段（可以不止一条，供 citations 全量列出）；
    - ``CONFLICTING``：**互相矛盾的那几处**（§7.1 要求 citations 列两处，所以这里
      给的是"必须一起摆出来"的集合，不是"可信依据"）；
    - ``INSUFFICIENT``：恒为空元组——没有依据就一条引用都不许发，否则客户端会把
      "召回到的相关片段"当成"答案的依据"。
    """

    verdict: Verdict
    supporting_chunk_ids: tuple[UUID, ...]
    reason: str
    """给人看的一句话判定依据（中文）。

    它会进日志、进 ``done`` 事件、演示时直接念给评审：所以写"第 2 段缺少数值锚点
    「5 天」"，不写 ``"MISSING_ANCHOR"`` 这种码值——码值无法自证，得翻代码才知道
    发生了什么。
    """

    unsupported_claims: tuple[str, ...] = ()
    """证据撑不住的具体断言/缺口，一条一句。

    ``INSUFFICIENT`` 时是"缺什么"，``CONFLICTING`` 时是"哪条事实有两个值"，
    ``SUPPORTED`` 时为空（没有缺口）。``split_unsupported_claims`` 拿它和已生成
    的正文做二次比对，见该函数。
    """

    signals: dict[str, object] = field(default_factory=dict)
    """可观测的中间量，**只装 JSON 原生类型**。

    为什么不用 UUID / 枚举：它要能原样进结构化日志与 SSE 事件体（后续 T4.3/T5.1 会
    把 ``verdict`` 写进 ``done`` 事件）。放 UUID 就得在序列化处再写一层转换，而那层
    转换一旦漏了，线上表现是"日志打不出来"而不是报错——静默丢观测数据。

    键集合见 `_new_signals`，是稳定契约，测试会锁结构。
    """


# ---------------------------------------------------------------- 词表与正则

#: 汉字（基本区/扩展A/兼容区）+ 假名 + 谚文视作同类表意字 run，与数据库侧
#: `pyknora_cjk_bigram`（`migrations/0007_cjk_bigram_fts.sql`）保持同一口径：
#: 两边切法不一致时，"检索命中的词"与"门控认出的主题"会对不上，排查时找不到原因。
_CJK_RUN: Final[re.Pattern[str]] = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+"
)

#: 切分 run 的虚词。**只切几乎不承载事实的字**：切多会把真词组拆散（"有效期"被
#: "有"切开就只剩"效期"，反而丢了含义）。这些字出现在句子里只起连接作用，把它们
#: 留在二元组里会产出"假的""年在"这类噪声片段，噪声片段的危害不是多算命中，
#: 而是让"主题命中"变成一件几乎必然成立的事——那等于把判据废掉。
_FRAGMENT_SEPARATORS: Final[re.Pattern[str]] = re.compile(
    r"[的了是在和与及或吗呢吧啊么也就都而并]+"
)

#: 两字停用片段：问句里几乎必现、且与被问事实无关。留着它们，"这个/什么/多少"
#: 会让任何一个礼貌用语的片段都算"主题命中"。
_STOP_FRAGMENTS: Final[frozenset[str]] = frozenset(
    {
        "什么",
        "怎么",
        "怎样",
        "如何",
        "为何",
        "哪些",
        "哪个",
        "请问",
        "是否",
        "多少",
        "多久",
        "多长",
        "我们",
        "你们",
        "他们",
        "这个",
        "那个",
        "这些",
        "那些",
        "可以",
        "能否",
        "以及",
        "或者",
        "如果",
        "需要",
        "应该",
        "应当",
        "一下",
        "有关",
        "关于",
        "不是",
        "有无",
        "几个",
        "几天",
    }
)

#: 单位 → (事实类别, 换算到类别基准单位的倍数)。
#:
#: 类别是"哪种事实"：**冲突只在同类之间判**，否则"年假 5 天"和"补贴 500 元"会因为
#: 都含数字被判成冲突。换算是为了"1 万元"与"10000 元"不被当成两个不同取值——同一
#: 金额的两种写法在真实文档里很常见，换算掉才能比较。
#:
#: "工作日"单独成类而不并入"天"：10 个工作日≈14 个自然日，并类会把两个都对的写法
#: 判成冲突。判宽（少报一次冲突）在这里是错的，判窄（多报冲突）是对的，但"单位不同"
#: 不属于窄，属于错。
_UNITS: Final[dict[str, tuple[str, float]]] = {
    "万元": ("money", 10_000.0),
    "万块": ("money", 10_000.0),
    "千元": ("money", 1_000.0),
    "人民币": ("money", 1.0),
    "元": ("money", 1.0),
    "块": ("money", 1.0),
    "工作日": ("workday", 1.0),
    "天": ("day", 1.0),
    "年": ("year", 1.0),
    "个月": ("month", 1.0),
    "小时": ("hour", 1.0),
    "%": ("percent", 1.0),
    "％": ("percent", 1.0),
    "人": ("people", 1.0),
    "次": ("times", 1.0),
}

# 单位按长度倒序拼进正则："万元"必须先于"元"被尝试，否则"500万元"会被读成
# "500万"+（无单位）+ 残留的"元"，金额直接差三个数量级。
_UNIT_ALTERNATION: Final[str] = "|".join(
    sorted((re.escape(unit) for unit in _UNITS), key=len, reverse=True)
)

#: 中文数字的字面量与位值。制度文本里"一次""三个月""三十天"是**唯一**的数量信息，
#: 只认阿拉伯数字会让这些片段 anchors=0，于是"多久/多少"这类问句必然被判不足
#: （实测 `fact-04`：「财务部门每月至少对库存票据抽点一次」被拒答）。
#:
#: 刻意不含"零/〇"：它们几乎只出现在"二零二四年"这类逐位朗读的年份里，按位值解析
#: 会把 2024 读成 4，反而造出一个假锚点。含零的写法本来也极少。
_CN_DIGITS: Final[dict[str, int]] = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_PLACES: Final[dict[str, int]] = {"十": 10, "百": 100, "千": 1000}

#: "数字 + 单位"。数字允许中文数字、千分位逗号与小数；`(?:个)?` 吃掉"3 **个**工作日"
#: 里的量词。不匹配裸数字：没有单位的数字（"第 3 章""2024"）不是可核验的**数量事实**，
#: 把它当锚点会让门控对"章节号"提出数值要求，一路误杀。
_NUMERIC_ANCHOR: Final[re.Pattern[str]] = re.compile(
    rf"(?P<num>[一二两三四五六七八九十百千]{{1,6}}|\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?"
    rf"|\d+(?:\.\d+)?)\s*(?:个)?"
    rf"(?P<unit>{_UNIT_ALTERNATION})"
)

#: 问句在问"多少"的线索词。它们让 EV-02 成立：问句自身不带数字（"上限是多少"），
#: 但**要的答案一定是数字**，所以片段里没有数字就必须判不足——否则"住宿报销的规定"
#: 这种主题相关但没写金额的片段会被 2 个片段命中的规则放行，正是 §6.1 要拦的场景。
#:
#: 刻意**不含**两类词（实测 `syn-03` 被它们误杀）：
#: - 纯主题词（原表里的"费用"）：它说明"问哪件事"，不说明"要一个数"；
#: - 单字"几"：它在"几张/几天"里是量词，在"能不能…"的是非句里也可能是笔误式计数，
#:   单独认它会把"同一笔费用能不能拆成几张单子"读成数值问题。计数式疑问用
#:   `_COUNTED_QUANTITY_PATTERN`（"几"必须紧跟量词）单独认。
_QUANTITY_CUES: Final[tuple[str, ...]] = (
    "多少",
    "上限",
    "下限",
    "标准",
    "比例",
    "金额",
    "额度",
    "价格",
    "补贴",
    "天数",
    "期限",
    "多久",
    "多长时间",
    "报价",
    "限额",
    "次数",
    "怎么算",
    "计算方法",
)

#: "几 + 量词"才算在要数量（"几天""几张""几年"），单独一个"几"不算。
_COUNTED_QUANTITY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"几[个只份张条天次年月人位件笔台套截页段名]"
)

#: 是非问句的标记。问句在问"允不允许/是不是"时，哪怕同时出现"多少/几"，答案主体也是
#: 一条判断而不是一个数 —— 此时不该要求片段里必须有数字（那会把通篇无数字的合规条款
#: 判成不足，实测 `syn-03`）。
_YES_NO_MARKERS: Final[tuple[str, ...]] = (
    "能不能",
    "能否",
    "可不可以",
    "可否",
    "是不是",
    "是否",
    "有没有",
    "算不算",
    "要不要",
    "会不会",
    "允不允许",
)

#: 问句在问**哪一类**数量事实的线索词 → 能回答它的锚点类别（类别名与 `_UNITS` 一致）。
#:
#: 两处用途：
#:
#: - **冲突**只算"被问到的那类事实"。真实样本 `fact-02` 问的是工资，两段片段却对
#:   "试用期几个月"给了不同值——那不是这条问题的冲突，判成冲突只会让一个本可回答的
#:   工资问题被拒。
#: - **覆盖**要求片段给出的是"那一类"数。没有这条时，中文数字锚点（"三天""一年"在
#:   制度文本里到处都是）会让"片段里有没有数字"几乎恒真：真实样本 `noev-02` 问健身
#:   补贴额度（语料里没有这件事），一段只写了"累计三天以内不扣工资""满一年后享受
#:   年假"的片段就此被判成"有数可答"，把该拒答的问成了应答。
#:
#: 词表刻意只收**无歧义**的说法：认错方向是"少过滤一次"，比"把能答的问题拒掉"轻得多。
#: 认不出类别时返回空集，等于不过滤（行为同改动前）。
_ASKED_CATEGORY_CUES: Final[dict[str, tuple[str, ...]]] = {
    "工资": ("money",),
    "薪金": ("money",),
    "薪酬": ("money",),
    "薪资": ("money",),
    "奖金": ("money",),
    "津贴": ("money",),
    "补贴": ("money",),
    "报销": ("money",),
    "金额": ("money",),
    "额度": ("money",),
    "费用": ("money",),
    "价格": ("money",),
    "报价": ("money",),
    "多少天": ("day",),
    "几天": ("day",),
    "天数": ("day",),
    "多久": ("day", "month", "year", "hour", "times"),
    "多长时间": ("day", "month", "year", "hour", "times"),
    "期限": ("day", "month", "year"),
    "次数": ("times",),
    "几次": ("times",),
    "比例": ("percent",),
}

#: 适用对象：上层角色与其"限定词"。
#:
#: 只看角色词附近的限定词（`_SCOPE_WINDOW` 个字符内），不做全文共现：一份手册往往
#: 通篇提"经理"（在职责描述里），按全文共现判会把手册本身判成"仅限经理"，于是
#: 所有普通员工的问题都被误杀。
_UPPER_ROLES: Final[tuple[str, ...]] = (
    "经理",
    "主管",
    "负责人",
    "总监",
    "高管",
    "领导",
    "管理层",
    "部长",
    "处长",
)
_UPPER_SCOPE_MARKERS: Final[tuple[str, ...]] = (
    "仅限",
    "仅限于",
    "仅适用",
    "只适用",
    "只限",
    "适用于",
    "适用本",
    "须为",
    "限于",
    "面向",
    "及以上",
    "以上人员",
)
_SCOPE_WINDOW: Final[int] = 8

#: 基础角色词（问句侧判断"问的是普通员工"）。"员工"单列是刻意的：问句写"员工年假"
#: 与写"普通员工年假"是同一个意思，不认它就会漏掉 EV-03 的一大半真实问法。
_BASE_ROLES: Final[tuple[str, ...]] = (
    "普通员工",
    "新入职",
    "新员工",
    "实习生",
    "应届",
    "试用期",
    "基层",
    "员工",
)

#: 主语窗口里要剔除的泛称/角色词。它们只说明"这是谁的条款"，不说明"这是哪一条事实"，
#: 留着会让两条不同事实因为都以"员工"开头而被判成冲突（年假 5 天 vs 病假 3 天）。
_SUBJECT_NOISE: Final[tuple[str, ...]] = (
    "普通员工",
    "新入职员工",
    "员工",
    "经理",
    "主管",
    "负责人",
    "总监",
    "高管",
    "公司",
    "部门",
    "全体",
    "所有",
)

#: 取"数字前面 8 个字"当作这条数字的主语。8 是实测值：再短取不到"住宿上限"这种
#: 四字名词（中间还可能夹一个量词），再长会把上一条事实的尾巴卷进来，
#: 于是两条无关的数字共享了主语、被判成冲突。
_SUBJECT_WINDOW: Final[int] = 8

#: 主语窗口末尾要剥掉的连接字（"住宿上限**为** 500 元"里的"为"）。
#: 这里只放**纯连接词**，不放"上/下/限/人"这类实字：str.rstrip 是逐字符剃，
#: 把"限"放进来会把"住宿报销上**限**"剃成"住宿报销"——主语里最关键的中心词没了，
#: 于是"住宿上限 500"与"住宿下限 300"共享"住宿"、被误判成同一条事实的冲突。
_TRAILING_CONNECTORS: Final[str] = "为是达约及至到和与或的可则即须需应共均每"

#: 紧贴数字、却不携带事实的限定词：`住宿上限为每晚 500 元` 里真正的主语中心词是"上限"，
#: "每晚"只是计价口径。不剥掉它，同一事实换个写法（另一段写"上限 800 元"）就会因为
#: 中心词不同而被放过，两条冲突被静默吞掉——那比多报一条冲突危险得多。
_HEAD_FILLERS: Final[frozenset[str]] = frozenset(
    {"每晚", "每人", "每天", "每次", "每月", "每间", "每份", "单次", "均价"}
)

#: 表达"上限"的几种写法：`限额`/`额度` 与 `上限` 指的是同一条事实的中心词。
_HEAD_ALIASES: Final[dict[str, str]] = {"限额": "上限", "额度": "上限"}

#: 年份不是期限。`"2024 年"`、`"1999 年"` 这类四位数按日历年份排除，否则
#: "本制度自 2024 年起执行"会给问句贡献一个覆盖不了的"2024 年"锚点，把所有正确的
#: 片段一路判成不足。区间取 1900..2100：文档里的日历年份都落在里面，
#: 而"30 年工龄""5 年"这类真期限在区间外。
_CALENDAR_YEAR_RANGE: Final[tuple[float, float]] = (1900.0, 2100.0)

#: 判"同一条事实"的第二道闸：锚点所在**分句**去掉锚点原文后，两条残余文本的相似度。
#:
#: 为什么还需要它：中心词相同也可能是**并列分档**的两条规定——「应届毕业生（本科）……
#: 试用期 1 个月」与「凡进入我公司的员工……试用期 3 个月」共享中心词"用期"，判成冲突
#: 会让用户拿不到任何一条（实测 `fact-02`/`fact-06`）。分档规定的两个分句句式相同、
#: 限定语不同，剔掉数字后残余文本反而差得远；同一条事实的两种写法残余文本几乎一致。
#:
#: 0.85 是实测值：真冲突 0.909/0.909/1.000（EV-04 三条用例），假冲突 0.196/0.145/0.118
#: （两个真实误报），中间空档很宽。
_SAME_FACT_SIMILARITY: Final[float] = 0.85

#: 分句边界：只横向切到最近的这些字符，不跨句比较。
_CLAUSE_BOUNDARIES: Final[str] = "；;。!！?？\n\r"

#: 算相似度前剔除的字符（空白与标点）：换个标点、多一个换行不该影响"是不是同一句话"。
_RESIDUAL_NOISE: Final[re.Pattern[str]] = re.compile(r"[\s，,、：:（）()《》“”‘’\"'\-—~～…/]")


# ---------------------------------------------------------------- 内部结构


@dataclass(frozen=True, slots=True)
class _NumericAnchor:
    """正文里的一个"数字 + 单位"。"""

    category: str
    """事实类别（money / day / workday / year / …），冲突只在同类之间判。"""

    value: float
    """换算到类别基准单位后的数值（万元 → 元）。"""

    display: str
    """原文片段（如 ``"5 天"``）：reason 与 signals 里给人看的就是它。"""

    subject: frozenset[str]
    """数字前置窗口里的非噪声二元组。

    用来回答"这两个数字是不是在说同一条事实"。没有它，"年假 5 天"与"病假 3 天"
    会因为同属 day 类而被判成冲突——那是最差的一类误判：把两条都对的规定说成
    "资料自相矛盾"，用户按哪条都错。
    """

    head: str
    """紧贴数字的那个二元组（``住宿上限 500 元`` 里的 ``上限``）。

    只靠"共享任意片段"会误伤只差中心词的一对事实：``住宿上限 500`` 与
    ``住宿下限 300`` 共享"住宿"，其实在讲两条不同的规定。中心词不同时，
    要再共享一个片段（合计 ≥2）才算同一条事实。
    """

    start: int
    """锚点在正文里的起始偏移（含）。

    只服务"分句相似度"这一道判据（`_clause_residual`）：同一事实换个数字，它所在分句
    的其余部分是同一句话；分档规定的两句话则差得远。不参与冲突以外的任何判定。
    """

    end: int
    """锚点在正文里的结束偏移（不含），与 ``start`` 一起把锚点原文从分句中剔掉。"""


@dataclass(frozen=True, slots=True)
class _ChunkView:
    """一条片段判定时需要的中间表示（避免对同一段正文反复跑正则）。"""

    evidence: Evidence
    position: int
    """1 起的**片段序号**，不是 `chunk_ordinal`。

    reason 里写"第 2 段"必须和模型看到的 ``[资料 2]`` 是同一个数：`answer.py` 的
    system prompt 用 `enumerate(chunks, start=1)` 编号。用文档内部序号会出现
    "第 7 段"但上下文里只有 3 段的情况，用户与日志对不上号。
    """

    anchors: tuple[_NumericAnchor, ...]
    fragments: frozenset[str]


@dataclass(slots=True)
class _ConflictValue:
    """冲突里出现过的某个取值（一个值可能出现在多条片段里）。"""

    value: float
    display: str
    positions: list[int] = field(default_factory=list)


@dataclass(slots=True)
class _Conflict:
    """同一类事实在多个片段之间取值不一致。

    故意是可变的累加器（与对外 DTO 相反）：冲突是**归并**出来的，两条片段、三种取值
    都可能汇进同一条记录；边收集边生成不可变副本只会把代码写复杂，而它不会离开本模块。
    """

    category: str
    topics: set[str]
    values: list[_ConflictValue] = field(default_factory=list)

    def add(self, anchor: _NumericAnchor, *, position: int) -> None:
        """记下"第 position 段在这个事实上给了 anchor 这个值"。"""
        for known in self.values:
            if known.value == anchor.value:
                if position not in known.positions:
                    known.positions.append(position)
                return
        self.values.append(
            _ConflictValue(value=anchor.value, display=anchor.display, positions=[position])
        )
        # 按数值排序而不是按出现顺序：reason 里"500 元、800 元"比"800 元、500 元"好读，
        # 也让同一份资料换顺序召回时 reason 保持稳定（日志 diff 才有意义）。
        self.values.sort(key=lambda item: item.value)

    @property
    def positions(self) -> tuple[int, ...]:
        return tuple(sorted({position for item in self.values for position in item.positions}))

    @property
    def reason(self) -> str:
        topic = _fragments_text(sorted(self.topics))
        detail = "、".join(f"第 {item.positions[0]} 段为 {item.display}" for item in self.values)
        return f"资料存在冲突：{topic}在不同片段取值不同（{detail}）"

    @property
    def claim(self) -> str:
        topic = _fragments_text(sorted(self.topics))
        values = " / ".join(item.display for item in self.values)
        return f"{topic}有互相矛盾的取值：{values}"

    def as_signal(self) -> dict[str, object]:
        return {
            "category": self.category,
            "values": [item.value for item in self.values],
            "displays": [item.display for item in self.values],
            "chunk_positions": list(self.positions),
            "topic_fragments": sorted(self.topics)[:4],
        }


# ---------------------------------------------------------------- 抽取


def _cjk_fragments(text: str) -> frozenset[str]:
    """把正文切成**重叠二元组**形式的主题片段（至少 2 个汉字，虚词切开，停用词剔除）。

    为什么是二元组而不是"长度 ≥ 2 的连续汉字 run"：run 会把整句粘成一块
    （"年假有几天"就是一个 run），文档里几乎不可能出现与问句一字不差的整句，
    于是"覆盖 ≥ 1 个片段"这条判据会永远为假——判据等于不存在。二元组是中文里
    最小的可复用语素单位，也与检索侧 `pyknora_cjk_bigram` 同口径。
    """
    fragments: set[str] = set()
    for run in _CJK_RUN.findall(text):
        for piece in _FRAGMENT_SEPARATORS.split(run):
            for index in range(len(piece) - 1):
                pair = piece[index : index + 2]
                if pair in _STOP_FRAGMENTS:
                    continue
                fragments.add(pair)
    return frozenset(fragments)


def _subject_parts(text: str, end: int) -> tuple[frozenset[str], str]:
    """取 ``text[:end]`` 末尾一小段，返回（去泛称后的二元组, 紧贴数字的那个二元组）。"""
    window = text[max(0, end - _SUBJECT_WINDOW) : end].rstrip()
    window = window.rstrip(_TRAILING_CONNECTORS)
    for noise in _SUBJECT_NOISE:
        window = window.replace(noise, "")
    runs = _CJK_RUN.findall(window)
    head_run = runs[-1] if runs else ""
    # 先摘掉计价口径（"每晚"），再把露出来的连接字（"为"）剃掉，中心词才是真正的"上限"：
    # 只做一步的话，"为每晚"会被读成中心词"限为"，同一事实的两种写法就对不上了。
    for filler in _HEAD_FILLERS:
        head_run = head_run.replace(filler, "")
    head_run = head_run.rstrip(_TRAILING_CONNECTORS)
    head = head_run[-2:]
    return _cjk_fragments(window), _HEAD_ALIASES.get(head, head)


def _cn_number(token: str) -> float | None:
    """把 ``"三"`` / ``"十五"`` / ``"三十"`` / ``"一百五十"`` 解析成数值。

    只覆盖制度文本里会出现的写法（位值制，最大千位）。含"零/〇"的逐位朗读式
    （``"二零二四"``）返回 ``None`` —— 那不是数量，宁可不建锚点。
    """
    total = 0
    digit = 0
    for char in token:
        if char in _CN_DIGITS:
            digit = _CN_DIGITS[char]
            continue
        place = _CN_PLACES.get(char)
        if place is None:
            return None
        total += (digit or 1) * place
        digit = 0
    return float(total + digit)


def _anchor_value(raw: str) -> float | None:
    """锚点原文的数字部分 → 数值（阿拉伯/中文两种写法）。解析不了返回 ``None``。"""
    if raw[:1].isdigit():
        return float(raw.replace(",", ""))
    return _cn_number(raw)


def _numeric_anchors(text: str) -> list[_NumericAnchor]:
    """抽出正文里所有"数字 + 单位"锚点（按出现顺序）。"""
    anchors: list[_NumericAnchor] = []
    for match in _NUMERIC_ANCHOR.finditer(text):
        category, multiplier = _UNITS[match.group("unit")]
        number = _anchor_value(match.group("num"))
        if number is None:
            continue
        value = number * multiplier
        low, high = _CALENDAR_YEAR_RANGE
        if category == "year" and low <= value <= high:
            continue
        subject, head = _subject_parts(text, match.start())
        anchors.append(
            _NumericAnchor(
                category=category,
                value=value,
                display=match.group(0),
                subject=subject,
                head=head,
                start=match.start(),
                end=match.end(),
            )
        )
    return anchors


def _asked_categories(question: str) -> frozenset[str]:
    """问句在问哪一类数量事实；认不出返回空集（调用方据此不做类别过滤）。"""
    return frozenset(
        category
        for cue, categories in _ASKED_CATEGORY_CUES.items()
        if cue in question
        for category in categories
    )


def _asks_for_quantity(question: str) -> bool:
    """问句是不是在要一个**数值**（哪怕它自己没写数字）。

    两个收紧（都有实测样本顶着，见 `_QUANTITY_CUES` 的注释）：

    - 计数式疑问必须"几 + 量词"（`几天`/`几张`），裸"几"不算；
    - 是非问句（"能不能…"）不算——答案主体是一条判断，不是数。
    """
    if any(marker in question for marker in _YES_NO_MARKERS):
        return False
    return any(cue in question for cue in _QUANTITY_CUES) or bool(
        _COUNTED_QUANTITY_PATTERN.search(question)
    )


def _asks_about_base_role(question: str) -> bool:
    """问的是不是普通员工/新入职/实习生这类基层对象。

    同时提到上层角色（"普通员工和经理的年假各几天"）时返回 ``False``：问句把两类
    对象都摆出来了，此时"只适用于经理"的片段是**部分相关**，不该当作适用对象不匹配
    直接丢掉——那是这条规则唯一会误杀的情形，必须显式排除。
    """
    mentions_base = any(role in question for role in _BASE_ROLES)
    mentions_upper = any(role in question for role in _UPPER_ROLES)
    return mentions_base and not mentions_upper


def _scoped_to_upper_roles(content: str) -> bool:
    """片段是不是"仅适用于经理及以上"这类上层条款。"""
    for role in _UPPER_ROLES:
        start = content.find(role)
        while start != -1:
            window = content[max(0, start - _SCOPE_WINDOW) : start + len(role) + _SCOPE_WINDOW]
            if any(marker in window for marker in _UPPER_SCOPE_MARKERS):
                return True
            start = content.find(role, start + 1)
    return False


def _mentions_base_role(content: str) -> bool:
    return any(role in content for role in _BASE_ROLES)


def _normalise_for_fact(text: str) -> str:
    """把文本压成"只比字"的形式：去掉全部空白并做 casefold。

    片段正文里 ``"3000 元"`` 与人手写的 ``required_facts=["3000元"]`` 差一个空格，
    不压空白就会把"事实就在片段里"判成"缺失"。这个方向是**误杀**（多拒一次），
    但既然代价是零成本可修，就没必要留着。
    """
    return re.sub(r"\s+", "", text).casefold()


def _missing_facts(content: str, facts: Sequence[str]) -> tuple[str, ...]:
    haystack = _normalise_for_fact(content)
    return tuple(fact for fact in facts if _normalise_for_fact(fact) not in haystack)


def _view(evidence: Evidence, *, position: int) -> _ChunkView:
    return _ChunkView(
        evidence=evidence,
        position=position,
        anchors=tuple(_numeric_anchors(evidence.content)),
        fragments=_cjk_fragments(evidence.content),
    )


def _positions_text(positions: Sequence[int]) -> str:
    """把片段序号写成 ``1、2``：外层拼"第 … 段"时不会出现重复的"第"。"""
    return "、".join(str(position) for position in positions)


def _unique(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _chain_fragments(fragments: Sequence[str]) -> list[str]:
    """把重叠的二元组接回连续汉字串（``住宿``+``宿报``+``报销`` → ``住宿报销``）。

    只服务 ``reason``/``unsupported_claims`` 的**可读性**：给评审念一句
    "住宿报销上限在不同片段取值不同"能对上制度，念"住宿、宿报、报销"只会让人怀疑是不是
    坏词表。判据用的始终是二元组本身，``signals`` 里也仍保留原始片段（机器口径不变）。
    """
    remaining = sorted(set(fragments))
    chains: list[str] = []
    while remaining:
        # 从"没有前驱"的那一个起头：`住宿` 前面没有片段能接上，而 `上限` 前面有 `销上`。
        # 否则按字典序从 `上限` 起头会把它单独摘出去，剩下 `住宿报销上` 半截，读者对不上。
        starts = [item for item in remaining if not _has_predecessor(item, remaining)]
        chain = (starts or remaining)[0]
        remaining.remove(chain)
        extending = True
        while extending:
            extending = False
            for candidate in remaining:
                if len(candidate) == 2 and candidate[0] == chain[-1]:
                    chain += candidate[1]
                    remaining.remove(candidate)
                    extending = True
                    break
        chains.append(chain)
    return chains


def _fragments_text(fragments: Sequence[str], *, limit: int = 2) -> str:
    """片段集合的人话形式（接成连续汉字串后取前 ``limit`` 段）。"""
    return "、".join(sorted(_chain_fragments(fragments), key=len, reverse=True)[:limit])


def _has_predecessor(fragment: str, pool: Sequence[str]) -> bool:
    """``pool`` 里有没有片段能接在 ``fragment`` 前面（前一段的尾字 == 本段首字）。"""
    return any(other != fragment and other[-1] == fragment[0] for other in pool)


# ---------------------------------------------------------------- 判定


def _clause_prefix(text: str, start: int) -> str:
    """取锚点**之前**的那半句（回到最近的分句边界），剥掉空白与标点。

    只用前缀、不看后缀，是因为区分"是不是同一件事"的限定语在中文制度文本里一律
    出现在数字**前面**："应届毕业生（本科）：…试用期 1 个月"、"（3）已婚而父母均
    在外地居住…20 天"。数字后面跟的"超出部分自理""需提前审批"是同一条规定的补充
    说明，把它算进来只会稀释相似度——实测会把真冲突（500 元 vs 800 元）压到阈值以下。
    """
    left = start
    while left > 0 and text[left - 1] not in _CLAUSE_BOUNDARIES:
        left -= 1
    return _RESIDUAL_NOISE.sub("", text[left:start])


def _same_fact_similarity(
    left: _NumericAnchor, right: _NumericAnchor, *, left_text: str, right_text: str
) -> float:
    """两条锚点的"前缀分句"有多像（0..1）；用于把并列分档和真冲突分开。"""
    dirty_left = _clause_prefix(left_text, left.start)
    dirty_right = _clause_prefix(right_text, right.start)
    if not dirty_left and not dirty_right:
        # 两段都只有"数字 + 单位"本身（如表格单元格），没有可比文本 → 按同一事实处理。
        return 1.0
    return difflib.SequenceMatcher(None, dirty_left, dirty_right).ratio()


def _detect_conflicts(
    question_fragments: frozenset[str],
    views: Sequence[_ChunkView],
    *,
    asked_categories: frozenset[str] = frozenset(),
) -> list[_Conflict]:
    """找出"不同片段对同一被问到的事实给了不同取值"。

    四道限制是为了让"冲突"只落在真冲突上：

    1. **同类**（money/day/…）：不同类的两个数（天数与金额）永远不是同一条事实的两个值；
    2. **同一主语**：两处数字前面的窗口要有共同二元组，否则"年假 5 天"与"病假 3 天"
       会被判成矛盾；
    3. **主语与问句相交**：只判**被问到**的事实。条款之间本来就可以有不同数值
       （年假 5 天、婚假 10 天），把它们全算冲突等于任何长文档都答不了；
    4. **落在被问的那一类上**（`asked_categories`）：问工资时"试用期 1 个月 vs 6 个月"
       不是冲突，它是另一条事实的取舍。

    中心词相同之后还有一道"分句相似度"（`_SAME_FACT_SIMILARITY`）：并列分档的两条
    规定共享中心词，但语境不同，不算矛盾。

    归并按"同类 + 主题片段有交集"合并：合并错了只会让 reason 宽泛一点，拆错却会让两条
    冲突各自"看起来唯一"，反而误导用户——所以宁可合并。
    """
    conflicts: list[_Conflict] = []
    for index, left in enumerate(views):
        for right in views[index + 1 :]:
            for left_anchor in left.anchors:
                for right_anchor in right.anchors:
                    if left_anchor.category != right_anchor.category:
                        continue
                    if asked_categories and left_anchor.category not in asked_categories:
                        continue
                    if left_anchor.value == right_anchor.value:
                        continue
                    shared = left_anchor.subject & right_anchor.subject
                    topics = shared & question_fragments
                    if not topics:
                        continue
                    # 中心词一致才是同一条事实；"住宿上限 500"与"住宿下限 300"只共享
                    # "住宿"这类上位词，是在讲制度里的两个不同口径，不是同一条事实的两个
                    # 取值。中心词都抽不到时才退回"共享 ≥2 个片段"的弱条件。
                    if left_anchor.head and right_anchor.head:
                        if left_anchor.head != right_anchor.head:
                            continue
                    elif len(shared) < 2:
                        continue
                    # 第四道：分句相似度。中心词相同的两处数字仍可能是**并列分档**的两条
                    # 规定（"应届本科试用期 1 个月" vs "其他员工试用期 3 个月"），判成冲突
                    # 等于把制度里的分支说成"资料自相矛盾"，用户两边都拿不到。
                    if (
                        _same_fact_similarity(
                            left_anchor,
                            right_anchor,
                            left_text=left.evidence.content,
                            right_text=right.evidence.content,
                        )
                        < _SAME_FACT_SIMILARITY
                    ):
                        continue
                    target = next(
                        (
                            conflict
                            for conflict in conflicts
                            if conflict.category == left_anchor.category
                            and conflict.topics & topics
                        ),
                        None,
                    )
                    if target is None:
                        target = _Conflict(category=left_anchor.category, topics=set())
                        conflicts.append(target)
                    target.topics |= topics
                    target.add(left_anchor, position=left.position)
                    target.add(right_anchor, position=right.position)
    return conflicts


def _missing_anchors(
    view: _ChunkView, question_anchors: Sequence[_NumericAnchor]
) -> tuple[str, ...]:
    """问句的数值锚点里，这条片段还缺哪些（按问句里的出现顺序）。"""
    present = {(anchor.category, anchor.value) for anchor in view.anchors}
    return tuple(
        anchor.display
        for anchor in question_anchors
        if (anchor.category, anchor.value) not in present
    )


def _covers(
    view: _ChunkView,
    *,
    question_anchors: Sequence[_NumericAnchor],
    question_fragments: frozenset[str],
    asked_quantity: bool,
    facts: Sequence[str],
    asked_categories: frozenset[str] = frozenset(),
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    """这条片段是否足以支撑回答；同时返回（缺失锚点，缺失事实）。

    三种判据的取舍：

    - **问句带数字**：以"数字全被覆盖"为准，**不额外要求主题命中**。数字 + 单位本身
      就是强判据（"500 元"在文档里通常只有一处含义），再叠一条词面要求只会误杀
      （计划 §11 R4 列的第一风险就是误杀）。
    - **问句不带数字但在问数值**：要求"确实有数，而且是被问到的那类数"——这是计划
      §6.3 第 2 层的原话（"片段必须含数字，否则 INSUFFICIENT"），也是 EV-02 的机制；
      类别这一层见 `_ASKED_CATEGORY_CUES` 的注释（真实样本 `noev-02` 靠它挡住）。
      只看主题片段的话，"住宿报销规定：员工出差应选择合标酒店"会以 3 个片段命中被
      放行，而那正是 §6.1 要拦的"相关但不足"。
    - **问句既不带数字也不问数值**：退化成"至少 2 个主题片段"（计划 §6.4 的 EV 口径）。
      1 个片段可能是巧合（"住宿"这个词在差旅文档里到处都是），2 个才开始像同一件事。
    """
    missing_anchors = _missing_anchors(view, question_anchors)
    missing_facts = _missing_facts(view.evidence.content, facts)
    hits = view.fragments & question_fragments
    if question_anchors:
        covered = not missing_anchors
    elif asked_quantity:
        has_number = bool(view.anchors)
        if has_number and asked_categories:
            has_number = any(anchor.category in asked_categories for anchor in view.anchors)
        covered = len(hits) >= 2 and has_number
    else:
        covered = len(hits) >= 2
    return covered and not missing_facts, missing_anchors, missing_facts


def _best_view(
    views: Sequence[_ChunkView],
    *,
    question_anchors: Sequence[_NumericAnchor],
    question_fragments: frozenset[str],
    asked_quantity: bool,
    facts: Sequence[str],
    asked_categories: frozenset[str] = frozenset(),
) -> _ChunkView | None:
    """最接近"能答"的那条片段，只用于把 reason 说具体（"第 2 段还缺什么"）。

    排序键是（主题命中数↑，缺失锚点↓，缺失事实↓）；并列时取先出现的——召回顺序本身
    就是相关性排序，这条不额外发明权重。
    """
    scored: list[tuple[int, int, int, _ChunkView]] = []
    for view in views:
        _, missing_anchors, missing_facts = _covers(
            view,
            question_anchors=question_anchors,
            question_fragments=question_fragments,
            asked_quantity=asked_quantity,
            facts=facts,
            asked_categories=asked_categories,
        )
        hits = len(view.fragments & question_fragments)
        scored.append((hits, -len(missing_anchors), -len(missing_facts), view))
    if not scored:
        return None
    return max(scored, key=lambda item: item[:3])[3]


def _new_signals(
    *,
    evidence: Sequence[Evidence],
    question_anchors: Sequence[_NumericAnchor],
    question_fragments: frozenset[str],
    asked_quantity: bool,
) -> dict[str, object]:
    """``signals`` 的固定键集合（测试会锁结构，所以集中在一处定义）。"""
    return {
        "evidence_count": len(evidence),
        "evidence_scores": [item.score for item in evidence],
        "question_anchor_texts": [anchor.display for anchor in question_anchors],
        "question_fragment_count": len(question_fragments),
        "quantity_asked": asked_quantity,
        "topic_chunk_positions": [],
        "scope_mismatch_positions": [],
        "missing_anchor_texts": [],
        "missing_required_facts": [],
        "conflicts": [],
        "supported_positions": [],
        "supported_values": [],
    }


def assess(
    question: str,
    evidence: Sequence[Evidence],
    *,
    required_facts: Sequence[str] = (),
) -> EvidenceAssessment:
    """判定这批片段能否支撑回答这个问题（三态，见模块 docstring）。

    参数：

    - ``question``：用户原问题。空串/全空白没有锚点，任何放行都是瞎猜 → ``INSUFFICIENT``。
    - ``evidence``：已按召回顺序排好的片段。顺序决定 reason 里的"第 N 段"，
      也决定 ``supporting_chunk_ids`` 的顺序（citations 跟着它发）。
    - ``required_facts``：**必须**出现在同一片段里的短串（金额、期限、专有名词）。
      空白的 fact 会被丢掉——空串在任何文本里都"命中"，留着会把整条判据静默废掉。

    返回不可变的 `EvidenceAssessment`；本函数不抛异常、不做 IO，可在循环里跑。
    """
    facts = tuple(fact for fact in required_facts if fact.strip())
    question_anchors = _numeric_anchors(question)
    question_fragments = _cjk_fragments(question)
    asked_quantity = _asks_for_quantity(question)
    asked_categories = _asked_categories(question)
    signals = _new_signals(
        evidence=evidence,
        question_anchors=question_anchors,
        question_fragments=question_fragments,
        asked_quantity=asked_quantity,
    )

    # §7.1 第一种结果：没有证据。reason 固定英文 `no evidence`，因为这是评测集
    # （计划 §3.3 的 `no_evidence` 类）与线上日志都会打的稳定词。
    if not evidence:
        return EvidenceAssessment(
            verdict=Verdict.INSUFFICIENT,
            supporting_chunk_ids=(),
            reason="no evidence",
            unsupported_claims=("没有任何可用证据片段",),
            signals=signals,
        )

    if not question.strip():
        return EvidenceAssessment(
            verdict=Verdict.INSUFFICIENT,
            supporting_chunk_ids=(),
            reason="empty question",
            unsupported_claims=("问题为空，无法建立任何锚点",),
            signals=signals,
        )

    views = tuple(_view(item, position=position) for position, item in enumerate(evidence, 1))

    # EV-03：问的是普通员工，而片段是"仅限经理及以上"的条款。
    # 这一层是**排除**而不是"判不足后立即返回"：同一批召回里可能既有经理条款、
    # 也有对普通员工有效的条款，先剔掉不适用的，再用剩下的判支持/不足。
    question_is_base = _asks_about_base_role(question)
    scope_mismatch = frozenset(
        view.position
        for view in views
        if question_is_base and _scoped_to_upper_roles(view.evidence.content)
        if not _mentions_base_role(view.evidence.content)
    )
    candidates = tuple(view for view in views if view.position not in scope_mismatch)
    signals["scope_mismatch_positions"] = sorted(scope_mismatch)
    signals["topic_chunk_positions"] = [
        view.position for view in candidates if view.fragments & question_fragments
    ]

    # EV-04：冲突优先于支持。两条片段各说一个值，单看每条都"命中"；此时若先判
    # SUPPORTED 就会把一个互相矛盾的事实当成确定答案发出去（§7.1 第二种结果）。
    conflicts = _detect_conflicts(question_fragments, candidates, asked_categories=asked_categories)
    if conflicts:
        positions = [position for conflict in conflicts for position in conflict.positions]
        wanted = set(positions)
        signals["conflicts"] = [conflict.as_signal() for conflict in conflicts]
        return EvidenceAssessment(
            verdict=Verdict.CONFLICTING,
            supporting_chunk_ids=tuple(
                view.evidence.chunk_id for view in views if view.position in wanted
            ),
            reason="；".join(dict.fromkeys(conflict.reason for conflict in conflicts)),
            unsupported_claims=tuple(conflict.claim for conflict in conflicts),
            signals=signals,
        )

    # EV-01：存在一条片段覆盖问句全部数值锚点（无数字时退回主题片段规则），
    # 且 required_facts 全部落在**同一条**片段里。分开的片段各有一半事实不算数：
    # 模型是逐段读的，"5 天"和"仅限转正后"分属两段时，把两条拼起来的是一个猜测。
    supporting: list[_ChunkView] = []
    for view in candidates:
        covered, _, _ = _covers(
            view,
            question_anchors=question_anchors,
            question_fragments=question_fragments,
            asked_quantity=asked_quantity,
            facts=facts,
            asked_categories=asked_categories,
        )
        if covered:
            supporting.append(view)

    if supporting:
        signals["supported_positions"] = [view.position for view in supporting]
        signals["supported_values"] = [
            [anchor.category, anchor.value] for view in supporting for anchor in view.anchors
        ]
        return EvidenceAssessment(
            verdict=Verdict.SUPPORTED,
            supporting_chunk_ids=tuple(view.evidence.chunk_id for view in supporting),
            reason=_supported_reason(
                supporting,
                question_anchors=question_anchors,
                question_fragments=question_fragments,
                facts=facts,
            ),
            unsupported_claims=(),
            signals=signals,
        )

    # EV-02 / 通用不足：只有主题相关（或连主题都不相关）。reason 必须说清缺什么，
    # 否则用户只看到"不知道"，无从判断是自己问错了、还是资料里真没有。
    best = _best_view(
        candidates,
        question_anchors=question_anchors,
        question_fragments=question_fragments,
        asked_quantity=asked_quantity,
        facts=facts,
        asked_categories=asked_categories,
    )
    reason, claims, missing_anchor_texts, missing_facts = _insufficient_detail(
        best,
        scope_mismatch=scope_mismatch,
        question_anchors=question_anchors,
        question_fragments=question_fragments,
        asked_quantity=asked_quantity,
        facts=facts,
        asked_categories=asked_categories,
    )
    signals["missing_anchor_texts"] = list(missing_anchor_texts)
    signals["missing_required_facts"] = list(missing_facts)
    return EvidenceAssessment(
        verdict=Verdict.INSUFFICIENT,
        supporting_chunk_ids=(),
        reason=reason,
        unsupported_claims=claims,
        signals=signals,
    )


def _supported_reason(
    supporting: Sequence[_ChunkView],
    *,
    question_anchors: Sequence[_NumericAnchor],
    question_fragments: frozenset[str],
    facts: Sequence[str],
) -> str:
    positions = _positions_text([view.position for view in supporting])
    first = supporting[0]
    parts: list[str] = []
    if question_anchors:
        displays = _unique([anchor.display for anchor in question_anchors])
        parts.append(f"第 {positions} 段覆盖全部数值锚点（{'、'.join(displays)}）")
    else:
        hits = sorted(first.fragments & question_fragments)
        parts.append(f"第 {positions} 段命中 {len(hits)} 个主题片段（{_fragments_text(hits)}）")
    if facts:
        parts.append("required_facts 全部命中")
    return "，".join(parts)


def _insufficient_detail(
    best: _ChunkView | None,
    *,
    scope_mismatch: frozenset[int],
    question_anchors: Sequence[_NumericAnchor],
    question_fragments: frozenset[str],
    asked_quantity: bool,
    facts: Sequence[str],
    asked_categories: frozenset[str] = frozenset(),
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """把"为什么不足"拆成人话：返回（reason，claims，缺失锚点，缺失事实）。"""
    reasons: list[str] = []
    claims: list[str] = []

    if scope_mismatch:
        positions = _positions_text(sorted(scope_mismatch))
        reasons.append(
            f"第 {positions} 段仅适用于经理等上层角色，而问题问的是普通员工（适用对象不匹配）"
        )
        claims.append(
            f"片段 {positions} 的适用对象不匹配（仅限经理及以上），不能作为普通员工的依据"
        )

    missing_anchor_texts: tuple[str, ...] = ()
    missing_facts: tuple[str, ...] = ()
    if best is None:
        # 走到这里只有两种情形：召回片段全被判成"适用对象不匹配"，或一条都没命中主题。
        # 前者的原因已经写在上面那句里，再说"没有任何片段命中主题"就是假的——经理条款
        # 明明命中了"年假"。
        if scope_mismatch:
            reasons.append("除上述不适用的片段外，没有其它片段命中问题主题")
            claims.append("不适用条款之外，没有可用于回答的片段")
        else:
            reasons.append("没有任何片段命中问题主题（非停用词汉字片段重合为 0）")
            claims.append("问题涉及的资料未被检索到任何相关内容")
    else:
        _, missing_anchor_texts, missing_facts = _covers(
            best,
            question_anchors=question_anchors,
            question_fragments=question_fragments,
            asked_quantity=asked_quantity,
            facts=facts,
            asked_categories=asked_categories,
        )
        hits = sorted(best.fragments & question_fragments)
        if missing_anchor_texts:
            joined = "、".join(missing_anchor_texts)
            reasons.append(f"第 {best.position} 段缺少数值锚点（{joined}）")
            claims.append(f"没有片段同时给出这些数值：{joined}")
        if asked_quantity and not question_anchors and not best.anchors:
            # EV-02 的准确说法：主题对上了，但片段里一个数字都没有。
            reasons.append(
                f"第 {best.position} 段只命中主题（{_fragments_text(hits)}），"
                "但问题在要具体数值，该片段没有任何数字"
            )
            claims.append(f"问题要的是数值，最相关的第 {best.position} 段不含任何数字")
        elif (
            asked_quantity
            and not question_anchors
            and best.anchors
            and asked_categories
            and not any(anchor.category in asked_categories for anchor in best.anchors)
        ):
            # 有数字，但不是被问到的那类（问金额却只写了"三天""一年"）。这类片段看着
            # 有数，其实答不了问题——真实样本 `noev-02` 就是被它放行的。
            got = "、".join(sorted({anchor.display for anchor in best.anchors}))
            want = "、".join(sorted(asked_categories))
            reasons.append(f"第 {best.position} 段虽有数值（{got}），但不是问题要的那类（{want}）")
            claims.append(f"没有片段给出问题要的那类数值（{want}）")
        if not question_anchors and not asked_quantity and len(hits) < 2:
            reasons.append(f"第 {best.position} 段只命中 {len(hits)} 个主题片段，未达 2 个的下限")
            claims.append(
                f"第 {best.position} 段与问题只有 {len(hits)} 个词面重合，不足以确认同一件事"
            )
        if missing_facts:
            reasons.append(f"第 {best.position} 段缺少必需要素（{'、'.join(missing_facts)}）")
            claims.append(f"问题要求的要素在片段里找不到：{'、'.join(missing_facts)}")

    if not reasons:  # 兜底：判据全不满足但上面没命中任何具体分支时，也必须给出一句可读理由
        reasons.append("证据不足以判定")
        claims.append("证据不足以支撑任何具体结论")
    return "；".join(reasons), tuple(claims), missing_anchor_texts, missing_facts


# ---------------------------------------------------------------- 正文二次比对

_SENTENCE_SEPARATORS: Final[re.Pattern[str]] = re.compile(r"[。！？；!?;\n]+")


def _conflict_values(signals: dict[str, object]) -> set[tuple[str, float]]:
    """从 ``signals["conflicts"]`` 里取回（类别，值）集合。

    按"可能不是理想类型"来读：signals 会绕一圈进日志/事件体再回到进程里（重放、回填
    评测结果），那时它是 JSON 解出来的 dict，不是我们放进去的原对象。直接下标访问
    会在畸形数据上抛 KeyError/TypeError，把一次"读日志"变成一次崩溃。
    """
    values: set[tuple[str, float]] = set()
    raw = signals.get("conflicts")
    if not isinstance(raw, list):
        return values
    for item in raw:
        if not isinstance(item, dict):
            continue
        category = item.get("category")
        raw_values = item.get("values")
        if not isinstance(category, str) or not isinstance(raw_values, list):
            continue
        for value in raw_values:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.add((category, float(value)))
    return values


def _supported_values(signals: dict[str, object]) -> set[tuple[str, float]]:
    """从 ``signals["supported_values"]`` 里取回（类别，值）集合；理由同上。"""
    values: set[tuple[str, float]] = set()
    raw = signals.get("supported_values")
    if not isinstance(raw, list):
        return values
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        category, value = item
        # bool 是 int 的子类：不排除它，``True`` 会被当成 1.0 参与数值比对。
        if (
            isinstance(category, str)
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            values.add((category, float(value)))
    return values


def split_unsupported_claims(answer_text: str, assessment: EvidenceAssessment) -> tuple[str, ...]:
    """挑出正文里**证据撑不住**的句子（极简版：只按数值锚点比对，不做语义核验）。

    为什么要有它：门控判的是"检索到的片段够不够"，而模型仍可能在一段 SUPPORTED 的
    上下文里编出第二个数字（"年假 5 天，另有 3 天带薪病假"——后半句没依据）。事后能
    机检的硬信号只有"正文里的数字在证据里出现过吗"，所以先只做这一层。

    三态各自的语义：

    - ``INSUFFICIENT``：**整段正文都是猜测**，返回全部非空句子。逐句豁免等于承认其中
      某句有据，而判定已经说了没有依据，这在语义上自相矛盾。
    - ``CONFLICTING``：只返回带冲突取值的句子（"住宿上限 500 元"），其余叙述句不判：
      它们不冒充结论，把"制度适用于全体出差人员"也标成"撑不住"只会稀释告警。
    - ``SUPPORTED``：返回带**未在支持片段里出现过**的数字的句子；纯叙述句不判。

    返回值是句式串（已 strip），**不改正文**——流式 token 已经发出去了，改正文会让
    客户端看到的内容与"模型实际说了什么"不一致（与 `answer.py` 的
    `invalid_cited_indices` 同一取舍：只报告，不改写）。
    """
    sentences = [item.strip() for item in _SENTENCE_SEPARATORS.split(answer_text)]
    sentences = [item for item in sentences if item]
    if not sentences:
        return ()

    if assessment.verdict is Verdict.INSUFFICIENT:
        return tuple(sentences)

    if assessment.verdict is Verdict.CONFLICTING:
        disputed = _conflict_values(assessment.signals)
        return tuple(
            sentence
            for sentence in sentences
            if any(
                (anchor.category, anchor.value) in disputed for anchor in _numeric_anchors(sentence)
            )
        )

    attested = _supported_values(assessment.signals)
    return tuple(
        sentence
        for sentence in sentences
        if any(
            (anchor.category, anchor.value) not in attested for anchor in _numeric_anchors(sentence)
        )
    )
