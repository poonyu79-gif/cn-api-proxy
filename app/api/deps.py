import os
from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session
from jose import jwt, JWTError
from app.models.base import get_db
from app.models.user import User
from app.models.token import Token

SECRET = os.getenv("SECRET_KEY", "change-me-secret-key-32chars-min!")
ALGO = "HS256"

def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET, algorithms=[ALGO])
    except JWTError:
        raise HTTPException(401, "Token 无效或已过期")

def current_user(authorization: str = Header(...), db: Session = Depends(get_db)) -> User:
    scheme, _, token = authorization.partition(" ")
    payload = _decode(token)
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise HTTPException(401, "用户不存在或已禁用")
    return user

def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return user

def get_api_token(authorization: str = Header(...), db: Session = Depends(get_db)) -> Token:
    """验证下游 API Key（sk- 开头）"""
    scheme, _, key = authorization.partition(" ")
    token = db.query(Token).filter(Token.key == key, Token.is_active == True).first()
    if not token:
        raise HTTPException(401, detail={"error": {"message": "Invalid API key", "type": "invalid_request_error", "code": "invalid_api_key"}})
    return token
