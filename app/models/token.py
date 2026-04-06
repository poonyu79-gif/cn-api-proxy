from datetime import datetime
from sqlalchemy import Integer, String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base

class Token(Base):
    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(64), default="默认令牌")
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # 流量控制
    rpm_limit: Mapped[int] = mapped_column(Integer, default=0)       # 0=不限，每分钟请求数
    daily_limit: Mapped[float] = mapped_column(Float, default=0.0)   # 0=不限，每日额度上限(元)
    speed_ratio: Mapped[float] = mapped_column(Float, default=1.0)   # 速率倍率 0.5=减速 2.0=加速(优先)
    used_today: Mapped[float] = mapped_column(Float, default=0.0)    # 今日已用额度
    last_reset: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    total_used: Mapped[float] = mapped_column(Float, default=0.0)    # 累计消耗(元)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
