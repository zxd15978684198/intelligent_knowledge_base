"""Embedding 双后端（S10）。

两个实现：
- `LocalHFEmbedder`：sentence-transformers 加载 HuggingFace 权重，离线可用
- `OpenAIEmbedder`：OpenAI 兼容 HTTP 接口

共同的硬约束：**输出维度必须等于配置的 embedding_dim，不符则抛错，绝不截断**。
静默截断的后果是插库成功、检索全错、且没有任何报错——这是本模块最贵的 bug。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import httpx

from pyknora.core.errors import AppError, ErrorCode
from pyknora.core.settings import Settings

logger = logging.getLogger(__name__)


class EmbeddingDimensionError(AppError):
    """模型实际输出维度与配置不符。"""

    def __init__(self, expected: int, actual: int, model_key: str) -> None:
        super().__init__(
            ErrorCode.INVALID_REQUEST,
            f"embedding dimension mismatch for {model_key}: expected {expected}, got {actual}",
            details={"expected": expected, "actual": actual, "model_key": model_key},
        )


class EmbeddingProfileMismatch(AppError):
    """KB 的 embedding 配方与当前进程 embedder 在**模型名**上不一致。

    只比维度不够：512 维的模型不止一个。维度相同、向量空间不同时混用，
    检索不会报错，只是把两套语义不兼容的向量放在一起排序——结果全错。
    """

    def __init__(self, field: str, expected: object, actual: object) -> None:
        super().__init__(
            ErrorCode.INVALID_REQUEST,
            f"embedding profile mismatch on {field}: kb wants {expected!r}, process has {actual!r}",
            details={"field": field, "expected": expected, "actual": actual},
        )


@runtime_checkable
class EmbeddingProvider(Protocol):
    """向量化端口。纯函数式：输入文本，输出等长向量，不碰数据库。"""

    model_key: str
    dim: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def _check_dim(vectors: list[list[float]], expected: int, model_key: str) -> None:
    for vec in vectors:
        if len(vec) != expected:
            raise EmbeddingDimensionError(expected, len(vec), model_key)


def check_profile_compat(profile: object, *, dim: int, model_key: str) -> None:
    """C1 接缝闸门：KB 的 embedding 配方必须与当前进程的 embedder 一致。

    `knowledge_bases.profile` 是这份配方的唯一事实来源（成员1 交付），上传入口
    与 worker 都以它为准，而不是以 `settings` 为准。不一致时**必须抛错**：
    否则 `index_builds` 会记下一份与真实向量来源不符的元数据，事后用正确维度
    反而查不到——这条属于红线 I7（不静默接受不一致）。

    **维度与模型名都要比。** 只比维度会放过"两个都是 512 维、但模型不同"的情况：
    那等于把两套互不兼容的向量空间混在一起，检索结果全错且没有任何报错。

    两个调用点：
    - `services/ingest/documents.py` 上传入口：`dim=settings.embedding_dim`
    - `workers/ingest_worker.py` 作业执行：`dim=embedder.dim`
    """
    want_dim = getattr(profile, "embedding_dim", None)
    if want_dim is not None and int(want_dim) != int(dim):
        raise EmbeddingDimensionError(int(want_dim), int(dim), model_key)

    want_model = getattr(profile, "embedding_model_key", None)
    if want_model is not None and str(want_model) != str(model_key):
        raise EmbeddingProfileMismatch("embedding_model_key", want_model, model_key)


class LocalHFEmbedder:
    """本地权重后端。首次使用才 import，避免没装 sentence-transformers 时炸在模块加载。"""

    def __init__(
        self,
        model_key: str,
        dim: int,
        *,
        endpoint: str,
        mirror: str,
        normalize: bool = True,
    ) -> None:
        self.model_key = model_key
        self.dim = dim
        self._endpoint = endpoint
        self._mirror = mirror
        self._normalize = normalize
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - 环境相关
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "sentence-transformers 未安装；请 uv sync --extra local-embed，"
                "或把 embedding_backend 设为 openai",
            ) from exc

        # 官方源优先；官方不可达时回落国内镜像。HF_ENDPOINT 由库读取。
        os.environ.setdefault("HF_ENDPOINT", self._endpoint)
        try:
            self._model = SentenceTransformer(self.model_key)
        except Exception as exc:  # noqa: BLE001 - 网络/权重异常都要回落到镜像
            logger.warning("官方源加载失败（%s），回落镜像 %s", exc, self._mirror)
            os.environ["HF_ENDPOINT"] = self._mirror
            self._model = SentenceTransformer(self.model_key)
        return self._model

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        vectors = model.encode(
            list(texts),
            normalize_embeddings=self._normalize,
            convert_to_numpy=True,
            batch_size=len(texts),
        )
        out = [v.tolist() for v in vectors]
        _check_dim(out, self.dim, self.model_key)
        return out

    async def probe(self) -> bool:
        try:
            await self.embed(["probe"])
            return True
        except Exception:  # noqa: BLE001
            return False


class OpenAIEmbedder:
    """OpenAI 兼容 HTTP 后端。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dim: int,
        timeout_s: float = 30.0,
    ) -> None:
        self.model_key = model
        self.dim = dim
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_s

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/embeddings",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self.model_key, "input": list(texts)},
            )
            resp.raise_for_status()
            payload = resp.json()
        out = [item["embedding"] for item in payload["data"]]
        _check_dim(out, self.dim, self.model_key)
        return out

    async def probe(self) -> bool:
        if not self._api_key:
            return False
        try:
            await self.embed(["probe"])
            return True
        except Exception:  # noqa: BLE001
            return False


HASHING_MODEL_KEY = "test/hashing"
"""hashing 后端的虚拟模型名。

它不是"某次测试的临时值"，而是 `HashingEmbedder` 对外申报的身份：向量由这个后端
产生时，`index_builds.embedding_model_key`、`embedding_cache` 的键、以及 KB profile
的校验都必须用同一个字符串，否则三处各说各话。
"""


class HashingEmbedder:
    """确定性后端，仅供测试与离线 seed 使用。

    不依赖任何模型权重，输出长度严格等于配置维度。**不可用于生产检索**：
    它捕捉不到语义相似性，只能保证链路结构正确。
    """

    def __init__(self, model_key: str = HASHING_MODEL_KEY, dim: int = 512) -> None:
        self.model_key = model_key
        self.dim = dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        import hashlib

        out: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            # 用摘要循环填充到目标维度，再归一化
            raw = [(digest[i % len(digest)] - 128) / 128.0 for i in range(self.dim)]
            norm = sum(x * x for x in raw) ** 0.5 or 1.0
            out.append([x / norm for x in raw])
        _check_dim(out, self.dim, self.model_key)
        return out

    async def probe(self) -> bool:
        return True


def build_embedder(settings: Settings, *, backend: str | None = None) -> EmbeddingProvider:
    """按配置构造后端。`backend` 显式传入时优先（测试用）。"""
    choice = (backend or settings.embedding_backend).lower()
    if choice == "local":
        return LocalHFEmbedder(
            settings.embedding_model_key,
            settings.embedding_dim,
            endpoint=settings.huggingface_endpoint,
            mirror=settings.huggingface_mirror,
        )
    if choice == "openai":
        return OpenAIEmbedder(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=settings.openai_embedding_model,
            dim=settings.embedding_dim,
            timeout_s=settings.embedding_timeout_s,
        )
    if choice == "hashing":
        return HashingEmbedder(model_key=HASHING_MODEL_KEY, dim=settings.embedding_dim)
    raise AppError(
        ErrorCode.INVALID_REQUEST,
        f"unknown embedding backend: {choice} (expected local | openai | hashing)",
    )


def resolve_embedding_identity(
    settings: Settings, *, backend: str | None = None
) -> tuple[str, int]:
    """`build_embedder` **会**产出的 `(model_key, dim)`，但不加载任何权重。

    上传入口需要一个便宜的判断："这份 KB 配方是不是喂给将要真正执行 embedding 的
    那个后端"。这不能用 `settings.embedding_model_key` 一概而论——它只有 local 后端
    才是权威，openai 后端走 `openai_embedding_model`，hashing 固定 `HASHING_MODEL_KEY`。
    拿错来源的后果是校验在非 local 部署下**永远误报**（真库集成测试里就撞过：
    KB profile 是 `test/hashing`，上传口却拿 `BAAI/bge-small-zh-v1.5` 去比）。

    与 `build_embedder` 的一致性由 `tests/unit/test_profile_compat.py` 的等价断言守住
    （hashing / openai 两个便宜后端直接构造后对比）。
    """
    choice = (backend or settings.embedding_backend).lower()
    if choice == "local":
        return settings.embedding_model_key, settings.embedding_dim
    if choice == "openai":
        return settings.openai_embedding_model, settings.embedding_dim
    if choice == "hashing":
        return HASHING_MODEL_KEY, settings.embedding_dim
    raise AppError(
        ErrorCode.INVALID_REQUEST,
        f"unknown embedding backend: {choice} (expected local | openai | hashing)",
    )
