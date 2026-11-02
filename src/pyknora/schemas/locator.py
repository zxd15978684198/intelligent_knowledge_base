"""格式无关的「位置」契约（ADR 0008 D2）。

解析层不区分「第几页」和「第几张表」，但下游必须按来源格式渲染引用：PDF 给页码，
XLSX 给工作表 + 单元格，PPTX 给幻灯片。这里用 Pydantic discriminated union
（`kind` 字段）表达 6 种 kind，而不是自由 `dict`——自由 `dict` 会把类型错误推迟到
运行期，而 `locator` 要落进 `chunks.locator`（JSONB）并被前端直接渲染。

本模块**只定义类型**，不接线：`Block.locator` 的替换、`chunks.locator` 的落库与
迁移 0014 的存量回填都不在这里做。

两处口径以 ADR 0008 D2 为准，别在这里加业务判断：

- `table.cells` 是 Excel 的 `A1` 坐标，只允许电子表格来源；DOCX 表格没有列标，
  只能给 `table.index` + `rows`。这条由 parser 在产出时保证（见 ADR D2 末段），
  本模块不按来源格式拒绝，因为类型本身看不到 `source_format`。
- `locator_ref`（切分层的文本偏移句柄）与 `locator` 分工并存，不互相取代（ADR D2a）。
"""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 单元格区间：`B2` 或 `B2:C4`。列最多 3 位字母，行号从 1 起、不允许前导 0。
_CELLS_PATTERN = r"^[A-Z]{1,3}[1-9]\d*(:[A-Z]{1,3}[1-9]\d*)?$"

# 行区间：`R1200` 或 `R1200:R1350`。只表达行号，列由 `cells` 或 `table.index` 兜底。
_ROWS_PATTERN = r"^R[1-9]\d*(:R[1-9]\d*)?$"


class _LocatorBase(BaseModel):
    """6 种 kind 的公共壳：不可变、拒收未知字段、带 `inferred` 标记。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    inferred: bool = Field(
        default=False,
        description="True 表示该位置是推断/回填出来的，不是解析器真实读到的",
    )


class PageLocator(_LocatorBase):
    """PDF / 图片类：页码 + 可选标题（原 `heading_path` 下沉到这里）。"""

    kind: Literal["page"] = "page"
    page: int = Field(ge=1, description="从 1 开始的页码")
    heading: str | None = None


class ParaLocator(_LocatorBase):
    """DOCX / TXT / MD / HTML：段落序号 + 可选标题。

    `index` 允许为 None：存量回填的 `{"kind": "para", "inferred": true}` 没有段落
    序号，硬填一个具体值等于编造位置（ADR 0008 D9 补充 4）。
    """

    kind: Literal["para"] = "para"
    index: int | None = Field(default=None, ge=1, description="从 1 开始的段落序号")
    heading: str | None = None


class SheetLocator(_LocatorBase):
    """XLSX / XLS / ODS / CSV：工作表名 + 可选单元格区间（如 `B2:C4`）。"""

    kind: Literal["sheet"] = "sheet"
    sheet: str = Field(min_length=1, description="工作表名，不能为空串")
    cells: str | None = Field(default=None, pattern=_CELLS_PATTERN)


class SlideLocator(_LocatorBase):
    """PPTX / ODP：幻灯片序号 + 可选形状名。"""

    kind: Literal["slide"] = "slide"
    slide: int = Field(ge=1, description="从 1 开始的幻灯片序号")
    shape: str | None = None


class TableLocator(_LocatorBase):
    """表格块：第几张表 + 可选行区间，`cells` 仅当来源是电子表格。

    `rows` 是**表内行号**（含表头那一行也算），`R1200:R1350` 表示第 1200 到 1350 行。
    """

    kind: Literal["table"] = "table"
    index: int = Field(ge=1, description="文档内第几张表，从 1 开始")
    rows: str | None = Field(default=None, pattern=_ROWS_PATTERN)
    cells: str | None = None
    # 表格块只带 `index` 会丢掉「它在哪一页/哪张表里」——PPTX 里第 3 张幻灯片上的表
    # 与第 1 张幻灯片上的表都只是「第 2 张表」，引用点过去用户不知道是哪一页。
    # 两个容器字段都可选：DOCX 的表格在段落流里，两者都为 None 是正常的。
    slide: int | None = Field(default=None, ge=1, description="所在幻灯片序号（PPTX/ODP）")
    sheet: str | None = Field(default=None, description="所在工作表名（XLSX/ODS/CSV）")


class RegionLocator(_LocatorBase):
    """视觉块（扫描页 / OCR / 待补的图片区域）：页码 + 页内坐标。

    `bbox` 用 4 元 tuple 而不是 `list`：坐标天然是定长的，tuple 让长度错误在类型
    层就被拒（给 3 个值直接 ValidationError），也顺带表达「不可就地改写」。
    Pydantic 的 `model_dump()` 在 python 模式下保留 tuple，`model_dump_json()`
    落成 JSON 数组，两种形态都能往返。
    """

    kind: Literal["region"] = "region"
    page: int = Field(ge=1, description="从 1 开始的页码")
    bbox: tuple[float, float, float, float] = Field(
        description="页内坐标 (x0, y0, x1, y1)，单位随解析器（PDF 为 pt）",
    )

    @model_validator(mode="after")
    def _bbox_ordered(self) -> RegionLocator:
        """左上角不能跑到右下角外面，否则前端画不出高亮框。"""
        x0, y0, x1, y1 = self.bbox
        if x0 > x1:
            raise ValueError(f"bbox x0({x0}) 不能大于 x1({x1})")
        if y0 > y1:
            raise ValueError(f"bbox y0({y0}) 不能大于 y1({y1})")
        return self


LocatorKind = Literal["page", "para", "sheet", "slide", "table", "region"]

# 顺序即 kind 字面量的声明顺序；由 LocatorKind 派生，避免与 union 两处漂移。
LOCATOR_KINDS: tuple[str, ...] = get_args(LocatorKind)

# `Locator` 是对外的唯一入口：`model_validate` / `model_dump` 都走这个 discriminated
# union，非法 kind 与字段错配在解析时立刻抛 ValidationError。
Locator = Annotated[
    PageLocator | ParaLocator | SheetLocator | SlideLocator | TableLocator | RegionLocator,
    Field(discriminator="kind"),
]
