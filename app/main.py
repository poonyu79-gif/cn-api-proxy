import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.models.base import init_db, Session
from app.models.user import User
from app.core.security import hash_password
from app.api import auth, user, tokens, recharge, proxy, admin

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _ensure_admin()
    yield

def _ensure_admin():
    db = Session()
    try:
        admin_email = os.getenv("ADMIN_EMAIL", "admin@nexus.com")
        admin_pass  = os.getenv("ADMIN_PASSWORD", "qwer1234")
        u = db.query(User).filter(User.email == admin_email).first()
        if not u:
            u = User(
                email=admin_email,
                hashed_password=hash_password(admin_pass),
                is_admin=True,
                balance=9999999.0,
            )
            db.add(u)
            db.commit()
            print(f"[INIT] Admin created: {admin_email} / {admin_pass}")
        else:
            print(f"[INIT] Admin exists: {admin_email}")
    finally:
        db.close()

app = FastAPI(title="API 中转站", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 路由
app.include_router(auth.router)
app.include_router(user.router)
app.include_router(tokens.router)
app.include_router(recharge.router)
app.include_router(proxy.router)
app.include_router(admin.router)

@app.get("/health")
def health():
    return {"status": "ok"}

# 静态文件：管理后台优先
if os.path.exists("admin"):
    app.mount("/admin-panel", StaticFiles(directory="admin", html=True), name="admin")
if os.path.exists("public"):
    app.mount("/", StaticFiles(directory="public", html=True), name="public")
