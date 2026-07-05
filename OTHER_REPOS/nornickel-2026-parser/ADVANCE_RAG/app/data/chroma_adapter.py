"""Chroma adapter for document storage and retrieval."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import chromadb
from chromadb.api.models.Collection import Collection
from loguru import logger
from pydantic import BaseModel

from app.config.settings import ChromaConfig, ChromaMode, RuntimeConfig, SecretsSettings

CPU_LOCAL_DENSE_MODEL = "all-MiniLM-L6-v2"
OPENAPI_DENSE_MODEL = "text-embedding-3-small"
LOCAL_ONNX_DIR_ENV = "ADVANCE_RAG_ONNX_MODEL_DIR"
LOCAL_ONNX_DEFAULT = Path("assets/models/chroma/onnx_models") / CPU_LOCAL_DENSE_MODEL


class DenseEmbeddingInfo(BaseModel):
    mode: str
    model: str
    provider: str


def describe_dense_embedding(config: ChromaConfig) -> DenseEmbeddingInfo:
    if config.mode == ChromaMode.OPENAPI:
        return DenseEmbeddingInfo(
            mode=config.mode.value,
            model=OPENAPI_DENSE_MODEL,
            provider="openai_compatible",
        )
    return DenseEmbeddingInfo(
        mode=config.mode.value,
        model=CPU_LOCAL_DENSE_MODEL,
        provider="chromadb_onnx",
    )


class ChromaAdapter:
    def __init__(
        self,
        config: ChromaConfig,
        base_dir: Path,
        secrets: SecretsSettings | None = None,
    ) -> None:
        self._config = config
        self._base_dir = base_dir
        self._secrets = secrets
        self._client: chromadb.ClientAPI | None = None
        self._collection: Collection | None = None

    @property
    def is_ready(self) -> bool:
        return self._collection is not None

    def initialize(self) -> None:
        _configure_local_onnx_cache(self._base_dir)
        persist = Path(self._config.persist_directory)
        if not persist.is_absolute():
            persist = (self._base_dir / persist).resolve()
        persist.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist))
        embedding_function = None
        if self._config.mode == ChromaMode.OPENAPI:
            if self._secrets is None:
                raise ValueError("OpenAPI mode requires secrets settings")
            if not self._secrets.chroma_openai_api_key or not self._secrets.chroma_openai_base_url:
                raise ValueError(
                    "OpenAPI mode requires CHROMA_OPENAI_API_KEY and CHROMA_OPENAI_BASE_URL"
                )
            embedding_function = _build_openapi_embedding(
                api_key=self._secrets.chroma_openai_api_key,
                api_base=self._secrets.chroma_openai_base_url,
            )

        kwargs: dict[str, Any] = {
            "name": self._config.collection_name,
            "metadata": {"hnsw:space": "cosine"},
        }
        if embedding_function is not None:
            kwargs["embedding_function"] = embedding_function
        self._collection = self._client.get_or_create_collection(**kwargs)
        if self._config.mode == ChromaMode.CPU_LOCAL:
            # Warm ONNX session at startup to remove first-request penalty.
            self._collection.query(query_texts=["warmup"], n_results=1)

    def upsert(
        self,
        document_id: str,
        content: str,
        metadata: dict[str, Any],
    ) -> None:
        if self._collection is None:
            raise RuntimeError("Chroma adapter not initialized")
        self._collection.upsert(
            ids=[document_id],
            documents=[content],
            metadatas=[metadata],
        )

    def query_dense(self, query_text: str, limit: int) -> list[dict[str, Any]]:
        if self._collection is None:
            raise RuntimeError("Chroma adapter not initialized")
        result = self._collection.query(query_texts=[query_text], n_results=limit)
        return _normalize_query_result(cast(dict[str, Any], result))

    def get_all_documents(self) -> list[dict[str, Any]]:
        if self._collection is None:
            raise RuntimeError("Chroma adapter not initialized")
        data = self._collection.get(include=["documents", "metadatas"])  # type: ignore[list-item]
        items: list[dict[str, Any]] = []
        ids = data.get("ids") or []
        docs = data.get("documents") or []
        metas = data.get("metadatas") or []
        for idx, doc_id in enumerate(ids):
            items.append(
                {
                    "id": doc_id,
                    "document": docs[idx] if idx < len(docs) else "",
                    "metadata": metas[idx] if idx < len(metas) else {},
                }
            )
        return items

    def document_count(self) -> int:
        if self._collection is None:
            return 0
        return int(self._collection.count())

    def delete_collection(self) -> bool:
        if self._client is None:
            return False
        try:
            self._client.delete_collection(self._config.collection_name)
        except Exception as exc:
            logger.warning(
                "chroma_delete_collection_failed collection={} error={}",
                self._config.collection_name,
                str(exc),
            )
            return False
        self._collection = None
        return True


def _normalize_query_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    ids = (result.get("ids") or [[]])[0]
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    for idx, doc_id in enumerate(ids):
        score = 1.0 - float(dists[idx]) if idx < len(dists) else 0.0
        items.append(
            {
                "id": doc_id,
                "document": docs[idx] if idx < len(docs) else "",
                "metadata": metas[idx] if idx < len(metas) else {},
                "score": score,
            }
        )
    return items


def _build_openapi_embedding(api_key: str, api_base: str) -> Any:
    from chromadb.utils import embedding_functions

    openai_embedding_cls = getattr(embedding_functions, "OpenAIEmbeddingFunction", None)
    if openai_embedding_cls is None:
        raise RuntimeError("OpenAIEmbeddingFunction is unavailable in installed chromadb")
    try:
        return openai_embedding_cls(
            api_key=api_key,
            model_name="text-embedding-3-small",
            api_base=api_base,
        )
    except TypeError:
        return openai_embedding_cls(
            api_key=api_key,
            model_name="text-embedding-3-small",
        )


def _configure_local_onnx_cache(base_dir: Path) -> None:
    target = os.getenv(LOCAL_ONNX_DIR_ENV)
    cache_dir = Path(target) if target else (base_dir / LOCAL_ONNX_DEFAULT)
    if not cache_dir.is_absolute():
        cache_dir = (base_dir / cache_dir).resolve()
    try:
        from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

        ONNXMiniLM_L6_V2.DOWNLOAD_PATH = cache_dir
        logger.info("chroma_onnx_cache_dir={}", str(cache_dir))
    except Exception as exc:  # pragma: no cover - defensive import guard
        logger.warning("chroma_onnx_cache_override_failed error={}", str(exc))


def create_chroma_adapter(
    runtime: RuntimeConfig,
    base_dir: Path,
    secrets: SecretsSettings | None = None,
) -> ChromaAdapter:
    adapter = ChromaAdapter(runtime.chroma, base_dir, secrets=secrets)
    adapter.initialize()
    return adapter
