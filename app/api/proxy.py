import os, time, asyncio
from datetime import datetime, date
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session
import httpx
from app.models.base import get_db
from app.models.token import Token
from app.models.user import User
from app.models.log import RequestLog
from app.api.deps import get_api_token

router = APIRouter(tags=["proxy"])

UPSTREAM_BASE = os.getenv("UPSTREAM_BASE_URL", "https://xinyuanai666.com")
UPSTREAM_KEY  = os.getenv("UPSTREAM_API_KEY", "")

# 粗略计费：每1000 token 约 0.014 元（上游1美元=7元，gpt-4o-mini约0.002$/1k）
COST_PER_TOKEN = float(os.getenv("COST_PER_TOKEN", "0.000014"))

# 内存 RPM 计数器 {token_id: [timestamps]}
_rpm_counter: dict[int, list[float]] = {}

def _check_rpm(token: Token) -> None:
    if token.rpm_limit <= 0:
        return
    now = time.time()
    bucket = _rpm_counter.setdefault(token.id, [])
    # 保留1分钟内的请求
    _rpm_counter[token.id] = [t for t in bucket if now - t < 60]
    if len(_rpm_counter[token.id]) >= token.rpm_limit:
        raise HTTPException(429, detail={"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}})
    _rpm_counter[token.id].append(now)

def _reset_daily_if_needed(token: Token, db: Session) -> None:
    today = date.today()
    if token.last_reset.date() < today:
        token.used_today = 0.0
        token.last_reset = datetime.utcnow()
        db.commit()

def _check_balance(user: User, token: Token) -> None:
    if user.balance <= 0:
        raise HTTPException(402, detail={"error": {"message": "Insufficient balance", "type": "insufficient_quota"}})
    if token.daily_limit > 0 and token.used_today >= token.daily_limit:
        raise HTTPException(429, detail={"error": {"message": "Daily limit exceeded", "type": "rate_limit_error"}})

@router.post("/v1/chat/completions")
async def chat_completions(request: Request, token: Token = Depends(get_api_token), db: Session = Depends(get_db)):
    user = db.get(User, token.user_id)
    _reset_daily_if_needed(token, db)
    _check_rpm(token)
    _check_balance(user, token)

    body = await request.json()
    headers = {
        "Authorization": f"Bearer {UPSTREAM_KEY}",
        "Content-Type": "application/json",
    }
    stream = body.get("stream", False)
    start = time.time()

    async with httpx.AsyncClient(timeout=120) as client:
        if stream:
            async def generate():
                total_tokens = 0
                try:
                    async with client.stream(
                        "POST", f"{UPSTREAM_BASE}/v1/chat/completions",
                        json=body, headers=headers
                    ) as resp:
                        async for chunk in resp.aiter_bytes():
                            yield chunk
                            # 粗略估算 token（每个 chunk 约 5 token）
                            total_tokens += 5
                finally:
                    cost = round(total_tokens * COST_PER_TOKEN, 6)
                    latency = int((time.time() - start) * 1000)
                    _deduct(user, token, cost, db)
                    _log(user, token, body.get("model",""), 0, total_tokens, cost, 200, latency, db)

            return StreamingResponse(generate(), media_type="text/event-stream")
        else:
            resp = await client.post(
                f"{UPSTREAM_BASE}/v1/chat/completions",
                json=body, headers=headers
            )
            latency = int((time.time() - start) * 1000)
            data = resp.json()
            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            cost = round((prompt_tokens + completion_tokens) * COST_PER_TOKEN, 6)
            _deduct(user, token, cost, db)
            _log(user, token, body.get("model",""), prompt_tokens, completion_tokens, cost, resp.status_code, latency, db)
            return JSONResponse(content=data, status_code=resp.status_code)

@router.post("/v1/embeddings")
async def embeddings(request: Request, token: Token = Depends(get_api_token), db: Session = Depends(get_db)):
    user = db.get(User, token.user_id)
    _check_balance(user, token)
    body = await request.json()
    headers = {"Authorization": f"Bearer {UPSTREAM_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{UPSTREAM_BASE}/v1/embeddings", json=body, headers=headers)
    data = resp.json()
    usage = data.get("usage", {})
    tokens = usage.get("total_tokens", 0)
    cost = round(tokens * COST_PER_TOKEN * 0.1, 6)
    _deduct(user, token, cost, db)
    _log(user, token, body.get("model",""), tokens, 0, cost, resp.status_code, 0, db)
    return JSONResponse(content=data, status_code=resp.status_code)

@router.get("/v1/models")
async def list_models(token: Token = Depends(get_api_token)):
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{UPSTREAM_BASE}/v1/models",
                                headers={"Authorization": f"Bearer {UPSTREAM_KEY}"})
    return JSONResponse(content=resp.json())

def _deduct(user: User, token: Token, cost: float, db: Session):
    user.balance = max(0.0, user.balance - cost)
    token.used_today += cost
    token.total_used += cost
    db.commit()

def _log(user: User, token: Token, model: str, pt: int, ct: int, cost: float, sc: int, lat: int, db: Session):
    log = RequestLog(
        user_id=user.id, token_id=token.id, model=model,
        prompt_tokens=pt, completion_tokens=ct, cost=cost,
        status_code=sc, latency_ms=lat
    )
    db.add(log)
    db.commit()
