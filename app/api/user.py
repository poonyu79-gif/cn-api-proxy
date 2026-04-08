from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from pydantic import BaseModel
from app.models.base import get_db
from app.models.user import User
from app.models.log import RequestLog
from app.core.security import hash_password, verify_password
from app.api.deps import current_user

router = APIRouter(prefix="/api/user", tags=["user"])


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "balance": user.balance,
        "is_admin": user.is_admin,
        "created_at": user.created_at,
    }


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


@router.patch("/password")
def change_password(
    body: PasswordChange,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(body.old_password, user.hashed_password):
        raise HTTPException(400, "原密码错误")
    if len(body.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    user.hashed_password = hash_password(body.new_password)
    db.commit()
    return {"ok": True}


@router.get("/logs")
def my_logs(
    limit: int = 50,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """查询当前用户最近的调用日志"""
    limit = min(limit, 200)
    logs = (
        db.query(RequestLog)
        .filter(RequestLog.user_id == user.id)
        .order_by(desc(RequestLog.id))
        .limit(limit)
        .all()
    )
    return [
        {
            "id": l.id,
            "model": l.model,
            "prompt_tokens": l.prompt_tokens,
            "completion_tokens": l.completion_tokens,
            "cost": l.cost,
            "status_code": l.status_code,
            "latency_ms": l.latency_ms,
            "created_at": l.created_at,
        }
        for l in logs
    ]
