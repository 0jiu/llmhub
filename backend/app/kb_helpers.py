"""检索辅助：向量化问题 → FAISS 查询 → 从 MySQL 取分块内容。"""

from sqlalchemy.orm import Session

from app.models import KbChunk, KbDocument
from app.rag import vectorstore
from app.rag.embedder import embed_texts


def retrieve(db: Session, kb_id: int, query: str, top_k: int = 4) -> list[dict]:
    """返回 [{"chunk_id", "score", "filename", "content"}]，按相似度降序。"""
    q_vec = embed_texts([query])[0]
    hits = vectorstore.search(kb_id, q_vec, top_k)
    results = []
    for chunk_id, score in hits:
        c = db.get(KbChunk, chunk_id)
        if c is None:
            continue  # 索引与库不同步的兜底
        d = db.get(KbDocument, c.doc_id)
        results.append({
            "chunk_id": chunk_id,
            "score": round(score, 4),
            "filename": d.filename if d else "?",
            "content": c.content,
        })
    return results
