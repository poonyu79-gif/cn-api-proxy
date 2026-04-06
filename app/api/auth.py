import os, secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from jose import jwt
from pydantic import BaseModel, EmailStr
from app.models.base import get_db
from app.models.user import User

router = APIRouter(prefix="/api/auth", tags=["auth"])
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
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
    user = User(email=body.email, hashed_password=pwd.hash(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenOut(access_token=make_jwt(user.id, user.is_admin))

@router.post("/login", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not pwd.verify(form.password, user.hashed_password):
        raise HTTPException(401, "邮箱或密码错误")
    if not user.is_active:
        raise HTTPException(403, "账户已被禁用")
    return TokenOut(access_token=make_jwt(user.id, user.is_admin))
