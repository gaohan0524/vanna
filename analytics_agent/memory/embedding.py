"""Chinese embedding function for local Chroma collections.

Why this exists
---------------
Chroma's default embedding function is an English MiniLM model.  Chinese
business terms such as “复购率” and “净收入” are not represented well by it, so
semantic retrieval would look successful while returning poor neighbors.

This wrapper pins a Chinese/multilingual model and implements the small
protocol Chroma expects (``name``, ``embed_documents``, ``embed_query``).
The model is loaded lazily to avoid downloading weights just by importing the
agent or running unrelated unit tests.
"""

from __future__ import annotations

from typing import List


class ChineseEmbeddingFunction:
    """SentenceTransformer wrapper with Chroma's embedding-function protocol.

    The model is loaded lazily so importing the agent does not download weights.
    Model/version is part of ``name()``; changing it requires rebuilding the
    Chroma collection.

    重要约定：

    - ``embed_documents`` 用于入库文本，不加 query instruction；
    - ``embed_query`` 用于检索问题，加 BGE 的检索指令；
    - 向量统一 L2 归一化，配合 Chroma cosine 距离；
    - ``name()`` 会写入 collection metadata，模型不一致时启动直接报错。
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        *,
        device: str = "cpu",
        query_instruction: str = "为这个句子生成表示以用于检索相关文章：",
    ) -> None:
        """Load the configured Chinese model.

        Args:
            model_name: HuggingFace model id.  Treat it as part of the memory
                schema: changing it requires a new Chroma collection.
            device: ``cpu`` by default; use ``cuda`` only when the runtime has
                a stable GPU and the same model is available there.
            query_instruction: BGE retrieval instruction prepended to queries
                only; documents must not receive it.
        """
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
        """Stable identity stored in Chroma collection metadata."""
        return f"bge-zh:{self.model_name}"

    def __call__(self, input: List[str]) -> List[List[float]]:
        """Chroma's generic callable entry point; same as document encoding."""
        return self.embed_documents(input)

    def embed_documents(self, input: List[str]) -> List[List[float]]:
        """Encode stored documents without the query-side instruction."""
        vectors = self._model.encode(
            input,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_query(self, input: List[str]) -> List[List[float]]:
        """Encode retrieval queries with the BGE query instruction."""
        queries = [f"{self.query_instruction}{text}" for text in input]
        return self.embed_documents(queries)
