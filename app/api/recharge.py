import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.models.base import get_db
from app.models.user import User
from app.models.recharge import RechargeOrder
from app.api.deps import current_user

router = APIRouter(prefix="/api/recharge", tags=["recharge"])

PAY_METHODS = {"wechat", "alipay", "lianlian", "pingpong", "usdt"}

class RechargeIn(BaseModel):
    amount: float          # 付款金额(元/美元)
    pay_method: str        # wechat/alipay/lianlian/pingpong/usdt
    remark: str = ""

class RechargeOut(BaseModel):
    id: int
    order_no: str
    amount: float
    credits: float
    pay_method: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

def calc_credits(amount: float, pay_method: str) -> float:
    """汇率换算：微信/支付宝 1元=1元，连连/PingPong/USDT 1美元=7元"""
    if pay_method in ("wechat", "alipay"):
        return round(amount, 4)
    else:
        return round(amount * 7.0, 4)

@router.post("", response_model=RechargeOut)
def create_order(body: RechargeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.pay_method not in PAY_METHODS:
        raise HTTPException(400, f"不支持的支付方式: {body.pay_method}")
    if body.amount <= 0:
        raise HTTPException(400, "金额必须大于 0")

    credits = calc_credits(body.amount, body.pay_method)
    order = RechargeOrder(
        user_id=user.id,
        order_no=uuid.uuid4().hex,
        amount=body.amount,
        credits=credits,
        pay_method=body.pay_method,
        remark=body.remark,
        status="pending",
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order

@router.get("", response_model=list[RechargeOut])
def list_orders(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.query(RechargeOrder).filter(RechargeOrder.user_id == user.id).order_by(RechargeOrder.id.desc()).limit(50).all()
