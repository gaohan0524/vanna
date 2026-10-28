"""Chinese embedding function for local Chroma collections."""

from __future__ import annotations

from typing import List


class ChineseEmbeddingFunction:
    """SentenceTransformer wrapper with Chroma's embedding-function protocol.

    The model is loaded lazily so importing the agent does not download weights.
    Model/version is part of ``name()``; changing it requires rebuilding the
    Chroma collection.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        *,
        device: str = "cpu",
        query_instruction: str = "为这个句子生成表示以用于检索相关文章：",
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "sentence-transformers is required for Chinese embeddings. "
                "Install with: pip install 'vanna[stage3]'"
            ) from exc

        self.model_name = model_name
        self.device = device
        self.query_instruction = query_instruction
        self._model = SentenceTransformer(model_name, device=device)

    def name(self) -> str:
        return f"bge-zh:{self.model_name}"

    def __call__(self, input: List[str]) -> List[List[float]]:
        return self.embed_documents(input)

    def embed_documents(self, input: List[str]) -> List[List[float]]:
        vectors = self._model.encode(
            input,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_query(self, input: List[str]) -> List[List[float]]:
        queries = [f"{self.query_instruction}{text}" for text in input]
        return self.embed_documents(queries)
