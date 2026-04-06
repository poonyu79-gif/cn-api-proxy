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

@router.get("", response_model=list[TokenOut])
def list_tokens(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.query(Token).filter(Token.user_id == user.id).all()

@router.post("", response_model=TokenOut)
def create_token(body: TokenCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    key = "sk-" + secrets.token_hex(24)
    token = Token(user_id=user.id, name=body.name, key=key)
    db.add(token)
    db.commit()
    db.refresh(token)
    return token

@router.delete("/{token_id}")
def delete_token(token_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    token = db.query(Token).filter(Token.id == token_id, Token.user_id == user.id).first()
    if not token:
        raise HTTPException(404, "令牌不存在")
    db.delete(token)
    db.commit()
    return {"ok": True}
