"""切片的数据形态。S09 的输出、chunks 表的输入。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Chunk(BaseModel):
    """解析 + 切片器的输出单元。纯数据，不绑定数据库。"""

    model_config = ConfigDict(frozen=True)

    ordinal: int = Field(ge=0, description="文档内顺序，从 0 开始")
    content: str = Field(min_length=1)
    page_no: int | None = None
    heading_path: str | None = None
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    token_count: int | None = None
    locator_ref: str | None = None

    @model_validator(mode="after")
    def _boundaries_ordered(self) -> Chunk:
        """红线 R2：page_no 可空，但 char_start 必须小于 char_end。"""
        if not self.char_start < self.char_end:
            raise ValueError(
                f"char_start({self.char_start}) must be < char_end({self.char_end}) "
                f"for chunk ordinal {self.ordinal}"
            )
        return self

    @property
    def length(self) -> int:
        return self.char_end - self.char_start
