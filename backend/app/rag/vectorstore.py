"""FAISS 向量索引：每个知识库一个索引文件，支持增、删、查与持久化。

- IndexFlatIP（内积）+ 归一化向量 = 余弦相似度检索；
- IndexIDMap2 以 chunk_id 作为向量 id，便于与 MySQL 分块表关联；
- 删除用 remove_ids（IndexIDMap2 支持），小规模场景够用；
- 索引文件按 kb_id 存在持久卷，进程内只缓存已加载的索引。
"""

import os
import threading

import faiss

from app.rag.embedder import EMBED_DIM

INDEX_DIR = "/app/faiss_data/indexes"

_loaded: dict[int, "faiss.Index"] = {}
_lock = threading.Lock()


def _index_path(kb_id: int) -> str:
    return os.path.join(INDEX_DIR, f"kb_{kb_id}.index")


def _ensure_dir() -> None:
    os.makedirs(INDEX_DIR, exist_ok=True)


def load_index(kb_id: int):
    """加载指定知识库的索引；不存在则新建空索引并落盘。"""
    with _lock:
        if kb_id in _loaded:
            return _loaded[kb_id]
        _ensure_dir()
        path = _index_path(kb_id)
        if os.path.exists(path):
            index = faiss.read_index(path)
        else:
            index = faiss.IndexIDMap2(faiss.IndexFlatIP(EMBED_DIM))
            faiss.write_index(index, path)
        _loaded[kb_id] = index
        return index


def save_index(kb_id: int) -> None:
    with _lock:
        index = _loaded.get(kb_id)
        if index is not None:
            _ensure_dir()
            faiss.write_index(index, _index_path(kb_id))


def add_vectors(kb_id: int, chunk_ids: list[int], vecs) -> None:
    """向索引追加向量（vecs 顺序与 chunk_ids 一一对应）。"""
    index = load_index(kb_id)
    import numpy as np
    id_arr = np.asarray(chunk_ids, dtype="int64")
    vec_arr = np.asarray(vecs, dtype="float32")
    index.add_with_ids(vec_arr, id_arr)
    save_index(kb_id)


def search(kb_id: int, query_vec, top_k: int = 4) -> list[tuple[int, float]]:
    """检索 top_k，返回 [(chunk_id, score), ...]，按相似度降序。"""
    index = load_index(kb_id)
    if index.ntotal == 0:
        return []
    import numpy as np
    q = np.asarray([query_vec], dtype="float32")
    k = min(top_k, index.ntotal)
    scores, ids = index.search(q, k)
    return [
        (int(cid), float(s))
        for cid, s in zip(ids[0], scores[0])
        if cid != -1
    ]


def remove_vectors(kb_id: int, chunk_ids: list[int]) -> None:
    """从索引中移除向量并落盘（文档删除时调用）。"""
    index = load_index(kb_id)
    import numpy as np
    if index.ntotal == 0:
        return
    selector = faiss.IDSelectorBatch(np.asarray(chunk_ids, dtype="int64"))
    index.remove_ids(selector)
    save_index(kb_id)
