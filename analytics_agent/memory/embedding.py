"""本地 Chroma 使用的中文 embedding 适配器。

为什么需要它
------------
Chroma 默认的 embedding function 是英文 MiniLM，对“复购率”“净收入”等
中文业务词的表示能力较弱；如果直接使用，检索看似有结果，实际近邻质量很差。

本类固定一个中文/多语言模型，并实现 Chroma 需要的最小协议：
``name``、``embed_documents``、``embed_query``。
模型采用懒加载，避免仅 import Agent 或跑无关单测时下载权重。
"""

from __future__ import annotations

from typing import List


class ChineseEmbeddingFunction:
    """实现 Chroma embedding-function 协议的 SentenceTransformer 包装。

    模型懒加载；``name()`` 包含模型标识，切换模型必须重建 Chroma collection。

    重要约定：

    - ``embed_documents`` 用于入库文本，不加 query instruction；
    - ``embed_query`` 用于检索问题，加 BGE 的检索指令；
    - 向量统一做 L2 归一化，配合 Chroma 的 cosine 距离；
    - ``name()`` 会写入 collection metadata，模型不一致时启动直接报错。
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        *,
        device: str = "cpu",
        query_instruction: str = "为这个句子生成表示以用于检索相关文章：",
    ) -> None:
        """加载指定的中文模型。

        参数：
            model_name: HuggingFace 模型 id。它属于记忆 schema 的一部分，
                换模型必须新建 Chroma collection。
            device: 默认 ``cpu``；只有运行环境有稳定 GPU 且模型可用时才改
                ``cuda``。
            query_instruction: BGE 的检索指令，只加在 query 上，不能加到
                入库文档上。
        """
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - 可选依赖
            raise ImportError(
                "中文 embedding 需要 sentence-transformers。"
                "请安装：pip install 'vanna[stage3]'"
            ) from exc

        self.model_name = model_name
        self.device = device
        self.query_instruction = query_instruction
        self._model = SentenceTransformer(model_name, device=device)

    def name(self) -> str:
        """返回写入 Chroma collection metadata 的稳定模型标识。"""
        return f"bge-zh:{self.model_name}"

    def __call__(self, input: List[str]) -> List[List[float]]:
        """Chroma 的通用可调用入口；等同于文档编码。"""
        return self.embed_documents(input)

    def embed_documents(self, input: List[str]) -> List[List[float]]:
        """编码入库文档，不加 query 侧指令。"""
        vectors = self._model.encode(
            input,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_query(self, input: List[str]) -> List[List[float]]:
        """编码检索问题，并加上 BGE 的 query 指令。"""
        queries = [f"{self.query_instruction}{text}" for text in input]
        return self.embed_documents(queries)
