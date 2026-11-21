"""向量化：bge-small-zh 模型懒加载 + 编码。

设计要点：
- 优先从本地目录加载（./model_cache 已随镜像挂载），离线可用、不依赖 HF 网络；
- 本地目录不存在时回退到在线下载（HF_ENDPOINT 指向国内镜像）；
- 模型懒加载（首次调用才加载），避免启动阶段阻塞健康检查；
- 归一化后用内积（IP）检索，等价于余弦相似度。
"""

import os

_model = None

HUB_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
LOCAL_MODEL_DIR = "/app/model_cache/bge-small-zh-v1.5"
EMBED_DIM = 512  # bge-small 系列输出维度
CACHE_DIR = "/app/faiss_data/hf_cache"


def _model_source() -> str:
    if os.path.isdir(LOCAL_MODEL_DIR) and os.path.isfile(
            os.path.join(LOCAL_MODEL_DIR, "model.safetensors")):
        return LOCAL_MODEL_DIR
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.makedirs(CACHE_DIR, exist_ok=True)
    return HUB_MODEL_NAME


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        source = _model_source()
        _model = SentenceTransformer(source,
                                     cache_folder=CACHE_DIR,
                                     local_files_only=source == LOCAL_MODEL_DIR)
    return _model


def embed_texts(texts: list[str]):
    """批量编码，返回归一化后的 numpy float32 矩阵。"""
    model = get_model()
    vecs = model.encode(texts, normalize_embeddings=True,
                        show_progress_bar=False)
    import numpy as np
    return np.asarray(vecs, dtype="float32")
