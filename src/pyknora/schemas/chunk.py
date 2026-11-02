"""切片的数据形态。S09 的输出、chunks 表的输入。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pyknora.schemas.locator import Locator


class Chunk(BaseModel):
    """解析 + 切片器的输出单元。纯数据，不绑定数据库。"""

    model_config = ConfigDict(frozen=True)

    ordinal: int = Field(ge=0, description="文档内顺序，从 0 开始")
    content: str = Field(min_length=1)
    page_no: int | None = None
    page_end_no: int | None = Field(
        default=None,
        description="末字符所在页。跨页切片 page_no < page_end_no；单页切片两者相等。",
    )
    heading_path: str | None = None
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    token_count: int | None = None
    locator_ref: str | None = None
    # 格式无关的定位（0014，ADR 0008 D2/D2a）：直接吃 `chunks.locator` 那个 JSONB dict，
    # Pydantic 按 `kind` 分派到 `schemas/locator.py` 的六个子模型之一——非法 kind 在
    # 解析时就抛 ValidationError，不会拖到前端渲染时才炸。
    # 与 `locator_ref` 分工并存（D2a）：这里只是同一行的另一个字段，谁也不取代谁。
    locator: Locator | None = None

    @model_validator(mode="after")
    def _boundaries_ordered(self) -> Chunk:
        """红线 R2：page_no 可空，但 char_start 必须小于 char_end。"""
        if not self.char_start < self.char_end:
            raise ValueError(
                f"char_start({self.char_start}) must be < char_end({self.char_end}) "
                f"for chunk ordinal {self.ordinal}"
            )
        # 页码版 R2：结束页不能小于起始页，否则引用范围反向。
        if (
            self.page_end_no is not None
            and self.page_no is not None
            and self.page_end_no < self.page_no
        ):
            raise ValueError(
                f"page_end_no({self.page_end_no}) 不能小于 page_no({self.page_no}) "
                f"for chunk ordinal {self.ordinal}"
            )
        return self

    @property
    def length(self) -> int:
        return self.char_end - self.char_start
