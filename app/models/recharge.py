from datetime import datetime
from sqlalchemy import Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base

class RechargeOrder(Base):
    __tablename__ = "recharge_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    order_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    amount: Mapped[float] = mapped_column(Float)          # 充值金额(元)
    credits: Mapped[float] = mapped_column(Float)         # 到账额度(元，汇率转换后)
    pay_method: Mapped[str] = mapped_column(String(32))   # wechat/alipay/lianlian/pingpong/usdt
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/approved/rejected
    remark: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
