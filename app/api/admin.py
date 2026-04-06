from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.models.base import get_db
from app.models.user import User
from app.models.token import Token
from app.models.recharge import RechargeOrder
from app.models.log import RequestLog
from app.api.deps import admin_user

router = APIRouter(prefix="/admin", tags=["admin"])

# ── 用户管理 ──────────────────────────────────────────────

@router.get("/users")
def list_users(user: User = Depends(admin_user), db: Session = Depends(get_db)):
    users = db.query(User).all()
    return [{"id": u.id, "email": u.email, "balance": u.balance,
             "is_active": u.is_active, "is_admin": u.is_admin,
             "created_at": u.created_at} for u in users]

class UserPatch(BaseModel):
    balance: Optional[float] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None

@router.patch("/users/{uid}")
def patch_user(uid: int, body: UserPatch, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    if body.balance is not None:
        u.balance = body.balance
    if body.is_active is not None:
        u.is_active = body.is_active
    if body.is_admin is not None:
        u.is_admin = body.is_admin
    db.commit()
    return {"ok": True}

# ── 下游 Key 流量管理 ──────────────────────────────────────

@router.get("/tokens")
def list_all_tokens(user: User = Depends(admin_user), db: Session = Depends(get_db)):
    tokens = db.query(Token).all()
    return [{
        "id": t.id, "user_id": t.user_id, "name": t.name, "key": t.key,
        "is_active": t.is_active, "rpm_limit": t.rpm_limit,
        "daily_limit": t.daily_limit, "speed_ratio": t.speed_ratio,
        "used_today": t.used_today, "total_used": t.total_used,
        "created_at": t.created_at
    } for t in tokens]

class TokenControl(BaseModel):
    is_active: Optional[bool] = None     # 启停
    rpm_limit: Optional[int] = None      # 每分钟请求数，0=不限
    daily_limit: Optional[float] = None  # 每日额度上限，0=不限
    speed_ratio: Optional[float] = None  # 速率倍率：0.5=减速 1.0=正常 2.0=加速

@router.patch("/tokens/{tid}")
def control_token(tid: int, body: TokenControl, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    t = db.get(Token, tid)
    if not t:
        raise HTTPException(404, "令牌不存在")
    if body.is_active is not None:
        t.is_active = body.is_active
    if body.rpm_limit is not None:
        t.rpm_limit = max(0, body.rpm_limit)
    if body.daily_limit is not None:
        t.daily_limit = max(0.0, body.daily_limit)
    if body.speed_ratio is not None:
        t.speed_ratio = max(0.1, min(10.0, body.speed_ratio))
    db.commit()
    return {"ok": True, "token_id": tid}

# ── 充值审核 ──────────────────────────────────────────────

@router.get("/recharge")
def list_recharge(status: str = "", user: User = Depends(admin_user), db: Session = Depends(get_db)):
    q = db.query(RechargeOrder)
    if status:
        q = q.filter(RechargeOrder.status == status)
    orders = q.order_by(RechargeOrder.id.desc()).limit(200).all()
    return [{"id": o.id, "user_id": o.user_id, "order_no": o.order_no,
             "amount": o.amount, "credits": o.credits, "pay_method": o.pay_method,
             "status": o.status, "remark": o.remark, "created_at": o.created_at} for o in orders]

class RechargeAction(BaseModel):
    action: str   # approve / reject
    remark: str = ""

@router.post("/recharge/{oid}")
def handle_recharge(oid: int, body: RechargeAction, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    order = db.get(RechargeOrder, oid)
    if not order:
        raise HTTPException(404, "订单不存在")
    if order.status != "pending":
        raise HTTPException(400, "订单已处理")
    if body.action == "approve":
        order.status = "approved"
        order.approved_at = datetime.utcnow()
        order.remark = body.remark
        u = db.get(User, order.user_id)
        if u:
            u.balance += order.credits
    elif body.action == "reject":
        order.status = "rejected"
        order.remark = body.remark
    else:
        raise HTTPException(400, "action 必须是 approve 或 reject")
    db.commit()
    return {"ok": True}

# ── 日志统计 ──────────────────────────────────────────────

@router.get("/logs")
def get_logs(limit: int = 100, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    logs = db.query(RequestLog).order_by(RequestLog.id.desc()).limit(limit).all()
    return [{"id": l.id, "user_id": l.user_id, "token_id": l.token_id,
             "model": l.model, "prompt_tokens": l.prompt_tokens,
             "completion_tokens": l.completion_tokens, "cost": l.cost,
             "status_code": l.status_code, "latency_ms": l.latency_ms,
             "created_at": l.created_at} for l in logs]

@router.get("/stats")
def get_stats(user: User = Depends(admin_user), db: Session = Depends(get_db)):
    from sqlalchemy import func
    total_users = db.query(func.count(User.id)).scalar()
    total_tokens = db.query(func.count(Token.id)).scalar()
    total_cost = db.query(func.sum(RequestLog.cost)).scalar() or 0
    total_requests = db.query(func.count(RequestLog.id)).scalar()
    pending_recharge = db.query(func.count(RechargeOrder.id)).filter(RechargeOrder.status == "pending").scalar()
    return {
        "total_users": total_users,
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 4),
        "total_requests": total_requests,
        "pending_recharge": pending_recharge,
    }
