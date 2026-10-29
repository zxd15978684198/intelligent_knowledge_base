"""C2 契约：成员2 向成员3/5 提供的只读面。

这里**不连库**，只钉两件事：

1. 实现确实满足对应 Protocol——下游可以靠 `isinstance` 做替换校验，不必假设。
2. R3 硬约束：每个读方法第一个参数必须是 `scope` 且**没有默认值**。
   有默认值就一定会有人不传；一不传就退化成跨区读全库，而这种漏洞不会报错。

真正的 SQL 语义（跨区返回空、retired build 可读、失效 id 缺席）在
`tests/integration/test_read_contracts.py`，那里有真 PG。
"""

from __future__ import annotations

import inspect

import pytest

from pyknora.db.repos.reads import (
    SqlChunkRepo,
    SqlChunkSearchRepo,
    SqlDocumentRepo,
    SqlIndexBuildRepo,
)
from pyknora.schemas.document import DocumentVersionInfo
from pyknora.services.contracts.reads import (
    BuildChunkRepo,
    BuildListRepo,
    ChunkLookupRepo,
    ChunkRepo,
    ChunkSearchRepo,
    DocumentRepo,
    DocumentVersionRepo,
    IndexBuildRepo,
)


class _DummySession:
    """只用于构造 repo；这些用例不会执行任何 SQL。"""


@pytest.mark.parametrize(
    ("factory", "protocol"),
    [
        (SqlDocumentRepo, DocumentRepo),
        (SqlDocumentRepo, DocumentVersionRepo),
        (SqlChunkRepo, ChunkRepo),
        (SqlChunkRepo, ChunkLookupRepo),
        (SqlChunkRepo, BuildChunkRepo),
        (SqlChunkSearchRepo, ChunkSearchRepo),
        (SqlIndexBuildRepo, IndexBuildRepo),
        (SqlIndexBuildRepo, BuildListRepo),
    ],
)
def test_implementation_satisfies_protocol(factory, protocol) -> None:
    assert isinstance(factory(_DummySession()), protocol)


_READ_METHODS = [
    (SqlDocumentRepo, "get_active"),
    (SqlDocumentRepo, "get_version"),
    (SqlChunkRepo, "list_active_chunks"),
    (SqlChunkRepo, "get_chunks"),
    (SqlChunkRepo, "list_chunks_by_build"),
    (SqlChunkSearchRepo, "search_similar"),
    (SqlChunkSearchRepo, "search_keyword"),
    (SqlIndexBuildRepo, "get"),
    (SqlIndexBuildRepo, "list_builds"),
]


@pytest.mark.parametrize(("factory", "method"), _READ_METHODS)
def test_scope_is_required_first_argument(factory, method) -> None:
    """R3：scope 必须是无默认值的第一个参数（看绑定后的方法，不是拿掉 self 之前）。"""
    bound = getattr(factory(_DummySession()), method)
    first = next(iter(inspect.signature(bound).parameters.values()))
    assert first.name == "scope", f"{method} 的第一个参数是 {first.name}"
    assert first.default is inspect.Parameter.empty, f"{method} 的 scope 不能有默认值"


def test_document_version_info_carries_metadata_but_no_body() -> None:
    """版本快照带定位所需的哈希/对象键，但刻意不带正文。"""
    assert DocumentVersionInfo.model_config.get("frozen") is True
    fields = set(DocumentVersionInfo.model_fields)
    assert {"document_version_id", "content_hash", "object_key", "byte_size"} <= fields
    assert "content" not in fields
