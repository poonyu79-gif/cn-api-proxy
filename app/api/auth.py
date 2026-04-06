import os
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from jose import jwt
from pydantic import BaseModel
from app.models.base import get_db
from app.models.user import User
from app.core.security import hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
SECRET = os.getenv("SECRET_KEY", "change-me-secret-key-32chars-min!")
ALGO = "HS256"

class RegisterIn(BaseModel):
    email: str
    password: str

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

def make_jwt(user_id: int, is_admin: bool) -> str:
    exp = datetime.utcnow() + timedelta(days=7)
    return jwt.encode({"sub": str(user_id), "admin": is_admin, "exp": exp}, SECRET, algorithm=ALGO)

@router.post("/register", response_model=TokenOut)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(400, "邮箱已注册")
    user = User(email=body.email, hashed_password=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenOut(access_token=make_jwt(user.id, user.is_admin))

@router.post("/login", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(401, "邮箱或密码错误")
    if not user.is_active:
        raise HTTPException(403, "账户已被禁用")
    return TokenOut(access_token=make_jwt(user.id, user.is_admin))
