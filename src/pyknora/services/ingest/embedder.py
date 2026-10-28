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


class HashingEmbedder:
    """确定性后端，仅供测试与离线 seed 使用。

    不依赖任何模型权重，输出长度严格等于配置维度。**不可用于生产检索**：
    它捕捉不到语义相似性，只能保证链路结构正确。
    """

    def __init__(self, model_key: str = "test/hashing", dim: int = 512) -> None:
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
        return HashingEmbedder(dim=settings.embedding_dim)
    raise AppError(
        ErrorCode.INVALID_REQUEST,
        f"unknown embedding backend: {choice} (expected local | openai | hashing)",
    )
