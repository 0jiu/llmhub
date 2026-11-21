"""知识库 API：建库 / 上传文档（解析→切分→向量化）/ 检索测试 / 文档管理。

文件解析支持：txt / md（UTF-8 直读）、pdf（pypdf 逐页提取）。
原始文件落盘 /app/uploads（持久卷），分块内容存 MySQL，向量存 FAISS。
"""

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import KbChunk, KbDocument, KnowledgeBase, User
from app.rag import chunker, vectorstore
from app.rag.embedder import embed_texts

router = APIRouter(prefix="/kbs", tags=["kb"])

UPLOAD_DIR = "/app/uploads"
ALLOWED_EXT = {".txt", ".md", ".pdf"}


def _own_kb(db: Session, kb_id: int, user: User) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.user_id != user.id:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb


# ---------- 知识库管理 ----------


class KbCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=255)


@router.post("")
def create_kb(body: KbCreate, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    kb = KnowledgeBase(user_id=user.id, name=body.name, description=body.description)
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return {"id": kb.id, "name": kb.name, "description": kb.description}


@router.get("")
def list_kbs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(KnowledgeBase).filter_by(user_id=user.id).order_by(
        KnowledgeBase.id.desc()).all()
    return [{"id": r.id, "name": r.name, "description": r.description,
             "created_at": str(r.created_at)} for r in rows]


@router.delete("/{kb_id}")
def delete_kb(kb_id: int, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    kb = _own_kb(db, kb_id, user)
    # 级联清理：MySQL 分块 + FAISS 索引文件 + 原始上传文件
    doc_ids = [d.id for d in db.query(KbDocument.id).filter_by(kb_id=kb_id)]
    db.query(KbChunk).filter_by(kb_id=kb_id).delete()
    db.query(KbDocument).filter_by(kb_id=kb_id).delete()
    db.delete(kb)
    db.commit()
    index = vectorstore.load_index(kb_id)
    if index.ntotal > 0:
        import faiss as _faiss
        vectorstore._loaded[kb_id] = _faiss.IndexIDMap2(
            _faiss.IndexFlatIP(vectorstore.EMBED_DIM))
        vectorstore.save_index(kb_id)
    return {"id": kb_id, "deleted": True, "documents_removed": len(doc_ids)}


# ---------- 文档管理 ----------


def _parse_file(filename: str, data: bytes) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".txt", ".md"):
        return data.decode("utf-8", errors="replace")
    if ext == ".pdf":
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    raise HTTPException(status_code=400,
                        detail=f"不支持的文件类型 {ext}，仅支持 txt/md/pdf")


@router.post("/{kb_id}/documents")
async def upload_document(
    kb_id: int,
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传文档：解析 → 切分 → 向量化 → 入库（MySQL 分块 + FAISS 向量）。"""
    _own_kb(db, kb_id, user)
    filename = os.path.basename(file.filename or "unnamed")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400,
                            detail=f"不支持的文件类型 {ext}，仅支持 txt/md/pdf")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    text = _parse_file(filename, data)

    chunks = chunker.split_text(text)
    if not chunks:
        raise HTTPException(status_code=400, detail="文档解析后无可索引文本（可能是纯图片扫描件）")

    # 向量化（CPU 推理，同步执行；当前文档量级下开销可接受）
    vecs = embed_texts(chunks)

    doc = KbDocument(kb_id=kb_id, filename=filename, chunk_count=len(chunks))
    db.add(doc)
    db.flush()  # 拿到 doc.id

    chunk_rows = [KbChunk(kb_id=kb_id, doc_id=doc.id, seq=i, content=c)
                  for i, c in enumerate(chunks)]
    db.add_all(chunk_rows)
    db.commit()  # chunk 自增 id 已生成

    chunk_ids = [r.id for r in chunk_rows]
    vectorstore.add_vectors(kb_id, chunk_ids, vecs)

    # 原始文件归档（审计/重跑用）
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    saved = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex[:8]}_{filename}")
    with open(saved, "wb") as f:
        f.write(data)

    return {"doc_id": doc.id, "filename": filename,
            "chunk_count": len(chunks), "vector_count": int(len(chunk_ids))}


@router.get("/{kb_id}/documents")
def list_documents(kb_id: int, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    _own_kb(db, kb_id, user)
    rows = db.query(KbDocument).filter_by(kb_id=kb_id, status=1).order_by(
        KbDocument.id.desc()).all()
    return [{"id": r.id, "filename": r.filename, "chunk_count": r.chunk_count,
             "created_at": str(r.created_at)} for r in rows]


@router.delete("/{kb_id}/documents/{doc_id}")
def delete_document(kb_id: int, doc_id: int,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """删除文档：MySQL 分块 + FAISS 向量同步移除。"""
    _own_kb(db, kb_id, user)
    doc = db.get(KbDocument, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    chunk_ids = [c.id for c in db.query(KbChunk.id).filter_by(doc_id=doc_id)]
    db.query(KbChunk).filter_by(doc_id=doc_id).delete()
    doc.status = 0
    db.commit()
    if chunk_ids:
        vectorstore.remove_vectors(kb_id, chunk_ids)
    return {"doc_id": doc_id, "chunks_removed": len(chunk_ids)}


# ---------- 检索测试 ----------


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=512)
    top_k: int = Field(default=4, ge=1, le=20)


@router.post("/{kb_id}/search")
def search_kb(kb_id: int, body: SearchIn,
              user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """向量检索测试：问题 → 最相关的 k 个分块（含相似度分数）。"""
    _own_kb(db, kb_id, user)
    q_vec = embed_texts([body.query])[0]
    hits = vectorstore.search(kb_id, q_vec, body.top_k)
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
    return {"query": body.query, "results": results}
