"""认证相关依赖：JWT（用户登录态）与 API Key（应用调用）双鉴权。"""

import hashlib

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ApiKey, User
from app.security import decode_token

bearer = HTTPBearer(auto_error=False)

API_KEY_PREFIX = "sk-llmhub-"


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    """从 Authorization: Bearer <JWT> 解析当前用户（管理接口用）。"""
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    user_id = decode_token(cred.credentials)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期，请重新登录")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    return user


def get_principal(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> tuple[User, ApiKey | None]:
    """网关双鉴权：凭据按前缀分流。

    - sk-llmhub- 开头 → API Key：查 SHA256 哈希且 status=1；
    - 否则按 JWT 解析（Swagger 调试 / 前端会话）。

    返回 (user, api_key_row | None)，第二项为 None 表示 JWT 调用。
    """
    if cred is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未提供凭据")
    token = cred.credentials

    if token.startswith(API_KEY_PREFIX):
        key_hash = hashlib.sha256(token.encode()).hexdigest()
        row = db.query(ApiKey).filter_by(key_hash=key_hash, status=1).first()
        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API Key 无效或已吊销")
        user = db.get(User, row.user_id)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Key 归属用户不存在")
        return user, row

    user_id = decode_token(token)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期，请重新登录")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    return user, None
