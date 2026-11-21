"""会话管理 CRUD：多轮对话的容器层。

设计要点：
- 管理接口走 JWT（用户身份），与 /keys 一致；
- 消息按 created_at,id 升序返回（对话回放顺序）；
- 删除会话连带删除消息（属主的级联清理，手动完成而非 DB 级联，可控）。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Conversation, ConversationMessage, User

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationIn(BaseModel):
    title: str = Field(default="新会话", max_length=128)
    kb_id: int | None = None


class RenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=128)


@router.post("")
def create_conversation(body: ConversationIn,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    conv = Conversation(user_id=user.id, title=body.title, kb_id=body.kb_id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {"id": conv.id, "title": conv.title, "kb_id": conv.kb_id}


@router.get("")
def list_conversations(user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    rows = (db.query(Conversation)
            .filter_by(user_id=user.id)
            .order_by(Conversation.updated_at.desc())
            .limit(100).all())
    counts = dict(
        db.query(ConversationMessage.conversation_id, sa_func.count())
        .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
        .filter(Conversation.user_id == user.id)
        .group_by(ConversationMessage.conversation_id).all()
    )
    return [{"id": c.id, "title": c.title, "kb_id": c.kb_id,
             "message_count": counts.get(c.id, 0),
             "updated_at": c.updated_at.isoformat() if c.updated_at else None}
            for c in rows]


@router.get("/{conv_id}/messages")
def get_messages(conv_id: int, limit: int = 100, offset: int = 0,
                 user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    conv = _get_owned(db, conv_id, user)
    rows = (db.query(ConversationMessage)
            .filter_by(conversation_id=conv.id)
            .order_by(ConversationMessage.id.asc())
            .offset(offset).limit(min(limit, 500)).all())
    return [{"id": m.id, "role": m.role, "content": m.content,
             "tool_trace": m.tool_trace,
             "created_at": m.created_at.isoformat() if m.created_at else None}
            for m in rows]


@router.patch("/{conv_id}")
def rename_conversation(conv_id: int, body: RenameIn,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    conv = _get_owned(db, conv_id, user)
    conv.title = body.title
    db.commit()
    return {"id": conv.id, "title": conv.title}


@router.delete("/{conv_id}")
def delete_conversation(conv_id: int,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    conv = _get_owned(db, conv_id, user)
    db.query(ConversationMessage).filter_by(conversation_id=conv.id).delete()
    db.delete(conv)
    db.commit()
    return {"deleted": conv_id}


def _get_owned(db: Session, conv_id: int, user: User) -> Conversation:
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv
