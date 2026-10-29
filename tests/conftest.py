"""全局测试夹具（所有测试目录共享）。

## 为什么要把 embedding 身份钉在 hashing 后端上

测试数据把 `knowledge_bases.embedding_model_key` 写成 `test/hashing`
（`tests/api/conftest.py`、`tests/integration/conftest.py` 都如此），注入的 embedder
也是 `HashingEmbedder`。但 `Settings` 的默认值是 `local` + `BAAI/bge-small-zh-v1.5`。

C1 接缝原先只比维度（两边都是 512），这个不一致看不出来；现在
`check_profile_compat` 连模型名一起比，上传入口会拿 `BAAI/bge-small-zh-v1.5`
去比 `test/hashing` 的配方，**每条上传都 4xx**。

生产里不存在这个问题：embedder 就是 `build_embedder(settings)` 造的，身份同源。
所以这是测试夹具自身的不一致，在进程入口一次钉齐即可，不必改任何被测代码。
`tests/unit/test_profile_compat.py` 另有等价断言，防止 `resolve_embedding_identity`
与 `build_embedder` 的映射漂移。
"""

from __future__ import annotations

from pyknora.core.settings import settings
from pyknora.services.ingest.embedder import HASHING_MODEL_KEY, resolve_embedding_identity

# 进程级一次性对齐：pytest 在同一进程里跑完所有用例，没有跨用例恢复的必要。
settings.embedding_backend = "hashing"
settings.embedding_model_key = HASHING_MODEL_KEY
settings.embedding_dim = 512

# 自检：对齐后进程身份必须就是测试数据里的那份身份，否则夹具说谎。
assert resolve_embedding_identity(settings) == (HASHING_MODEL_KEY, 512)
