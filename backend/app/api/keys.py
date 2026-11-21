"""平台 API Key 签发与管理。

安全设计：
- 明文 Key 只在签发响应中返回一次，服务端只存 SHA256 哈希——拖库也无法还原明文；
- 列表接口只返回前缀（脱敏展示），运维能看到 Key 用在哪但拿不到完整 Key；
- 吊销是软删除（status=0），保留审计痕迹。
"""

import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import ApiKey, User

router = APIRouter(prefix="/keys", tags=["keys"])

KEY_PREFIX = "sk-llmhub-"


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, description="应用名称，便于辨识 Key 用在哪")
    rpm_limit: int = Field(default=10, ge=1, le=600, description="每分钟允许请求数（令牌桶容量）")


@router.post("")
def create_key(
    body: KeyCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """签发新 Key（需 JWT 登录态）。明文仅此一次返回。"""
    raw = KEY_PREFIX + secrets.token_hex(16)
    row = ApiKey(
        user_id=user.id,
        name=body.name,
        key_prefix=raw[:18],
        key_hash=_hash_key(raw),
        rpm_limit=body.rpm_limit,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "name": row.name,
        "rpm_limit": row.rpm_limit,
        "key": raw,
        "warning": "请立即保存此 Key，服务端只存哈希，刷新后无法再次查看",
    }


@router.get("")
def list_keys(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """当前用户的全部 Key（脱敏展示）。"""
    rows = (
        db.query(ApiKey)
        .filter_by(user_id=user.id)
        .order_by(ApiKey.id.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "name": r.name,
            "key_masked": r.key_prefix + "***",
            "rpm_limit": r.rpm_limit,
            "status": "启用" if r.status == 1 else "已吊销",
            "created_at": str(r.created_at),
        }
        for r in rows
    ]


@router.delete("/{key_id}")
def revoke_key(
    key_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """吊销 Key（软删除：置 status=0，保留审计记录）。"""
    row = db.get(ApiKey, key_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Key 不存在")
    row.status = 0
    db.commit()
    return {"id": key_id, "status": "已吊销"}
