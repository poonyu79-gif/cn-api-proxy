import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_URL = os.getenv("DATABASE_URL", "sqlite:////tmp/cn_proxy.db")
if DB_URL.startswith("sqlite"):
    os.makedirs(os.path.dirname(DB_URL.replace("sqlite:///", "")), exist_ok=True)

engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if "sqlite" in DB_URL else {})
Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

class Base(DeclarativeBase):
    pass

def get_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()

def init_db():
    from app.models import user, token, recharge, log  # noqa
    Base.metadata.create_all(bind=engine)
