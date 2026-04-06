from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.models.base import get_db
from app.models.user import User
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
