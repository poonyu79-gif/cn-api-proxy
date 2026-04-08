import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.models.base import get_db
from app.models.user import User
from app.models.token import Token
from app.api.deps import current_user

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class TokenCreate(BaseModel):
    name: str = "默认令牌"


class TokenOut(BaseModel):
    id: int
    name: str
    key: str
    is_active: bool
    rpm_limit: int
    daily_limit: float
    speed_ratio: float
    used_today: float
    total_used: float

    class Config:
        from_attributes = True


def _get_own_token(token_id: int, user: User, db: Session) -> Token:
    token = db.query(Token).filter(Token.id == token_id, Token.user_id == user.id).first()
    if not token:
        raise HTTPException(404, "令牌不存在")
    return token


@router.get("", response_model=list[TokenOut])
def list_tokens(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.query(Token).filter(Token.user_id == user.id).all()


@router.post("", response_model=TokenOut)
def create_token(
    body: TokenCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    existing = db.query(Token).filter(Token.user_id == user.id).count()
    if existing >= 20:
        raise HTTPException(400, "每个账户最多创建 20 个令牌")
    key = "sk-" + secrets.token_hex(24)
    token = Token(user_id=user.id, name=body.name, key=key)
    db.add(token)
    db.commit()
    db.refresh(token)
    return token


@router.patch("/{token_id}/toggle", response_model=TokenOut)
def toggle_token(
    token_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """用户自己启用/停用令牌"""
    token = _get_own_token(token_id, user, db)
    token.is_active = not token.is_active
    db.commit()
    db.refresh(token)
    return token


@router.delete("/{token_id}")
def delete_token(
    token_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    token = _get_own_token(token_id, user, db)
    db.delete(token)
    db.commit()
    return {"ok": True}
