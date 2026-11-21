"""RAG 问答：检索知识库 → 组装带引用的上下文 → 走模型网关生成回答。

流程：用户提问 → bge 向量化 → FAISS top-k 检索 → 分块拼上下文
→ LLM 按"只依据资料回答"生成 → 返回回答 + 引用明细。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_principal
from app.gateway.router import chat_completion
from app.kb_helpers import retrieve
from app.models import KnowledgeBase

router = APIRouter(prefix="/chat", tags=["rag-chat"])

SYSTEM_PROMPT = (
    "你是企业知识库问答助手。回答规则：\n"
    "1. 只依据下面提供的资料回答问题，不要编造；\n"
    "2. 资料不足以回答时，明确说「资料中未提及」；\n"
    "3. 回答末尾用 [编号] 标注引用了哪些资料段。"
)


class RagChatIn(BaseModel):
    kb_id: int
    question: str = Field(min_length=1, max_length=512)
    top_k: int = Field(default=4, ge=1, le=20)
    model: str = "deepseek-chat"
    model_chain: list[str] | None = None


@router.post("")
async def rag_chat(body: RagChatIn, principal=Depends(get_principal),
                   db: Session = Depends(get_db)):
    user, key_row = principal
    kb = db.get(KnowledgeBase, body.kb_id)
    if kb is None or kb.user_id != user.id:
        raise HTTPException(status_code=404, detail="知识库不存在")

    # 1. 检索
    hits = retrieve(db, body.kb_id, body.question, body.top_k)
    if not hits:
        return {"answer": "该知识库还没有可检索的内容，请先上传文档。",
                "citations": [], "model_used": None}

    # 2. 组装上下文（带编号，便于 LLM 标注引用）
    blocks = []
    for i, h in enumerate(hits, 1):
        blocks.append(f"[{i}] (来源: {h['filename']}, 相似度: {h['score']})\n{h['content']}")
    context = "\n\n".join(blocks)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",
         "content": f"资料：\n{context}\n\n问题：{body.question}"},
    ]

    # 3. 走网关调大模型（复用降级链与计费逻辑）
    chain = body.model_chain or [body.model]
    payload = {"messages": messages, "temperature": 0.3}
    try:
        result = await chat_completion(payload, chain)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"所有上游模型调用失败: {type(e).__name__}")

    answer = result["data"]["choices"][0]["message"]["content"]
    return {
        "answer": answer,
        "citations": [
            {"index": i, "chunk_id": h["chunk_id"], "filename": h["filename"],
             "score": h["score"], "content": h["content"]}
            for i, h in enumerate(hits, 1)
        ],
        "model_used": result["model_used"],
        "fallback_from": result["fallback_from"],
    }
