import os, time, json
from datetime import datetime, date
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session
import httpx
from app.models.base import get_db, Session as DBSession
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

def _deduct_and_log(user_id: int, token_id: int, model: str,
                    pt: int, ct: int, cost: float, sc: int, lat: int):
    """在独立 session 里完成扣费和日志，避免流式响应中原 session 已关闭的问题"""
    db = DBSession()
    try:
        u = db.get(User, user_id)
        t = db.get(Token, token_id)
        if u:
            u.balance = max(0.0, u.balance - cost)
        if t:
            t.used_today += cost
            t.total_used += cost
        log = RequestLog(
            user_id=user_id, token_id=token_id, model=model,
            prompt_tokens=pt, completion_tokens=ct, cost=cost,
            status_code=sc, latency_ms=lat
        )
        db.add(log)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

@router.post("/v1/chat/completions")
async def chat_completions(request: Request, token: Token = Depends(get_api_token), db: Session = Depends(get_db)):
    user = db.get(User, token.user_id)
    if not user:
        raise HTTPException(401, detail={"error": {"message": "User not found", "type": "invalid_request_error"}})
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
    user_id = user.id
    token_id = token.id
    model = body.get("model", "")

    if stream:
        async def generate():
            total_tokens = 0
            status_code = 200
            async with httpx.AsyncClient(timeout=120) as client:
                try:
                    async with client.stream(
                        "POST", f"{UPSTREAM_BASE}/v1/chat/completions",
                        json=body, headers=headers
                    ) as resp:
                        status_code = resp.status_code
                        async for chunk in resp.aiter_bytes():
                            yield chunk
                            # 尝试从 SSE data 里解析 token 数，失败则粗估
                            try:
                                text = chunk.decode("utf-8", errors="ignore")
                                for line in text.splitlines():
                                    if line.startswith("data:") and "[DONE]" not in line:
                                        d = json.loads(line[5:].strip())
                                        u = d.get("usage") or {}
                                        if u.get("total_tokens"):
                                            total_tokens = u["total_tokens"]
                                        elif not total_tokens:
                                            total_tokens += 3
                            except Exception:
                                total_tokens += 3
                except Exception as e:
                    yield f"data: {{\"error\": \"{str(e)}\"}}\n\n".encode()
            cost = round(total_tokens * COST_PER_TOKEN, 6)
            latency = int((time.time() - start) * 1000)
            _deduct_and_log(user_id, token_id, model, 0, total_tokens, cost, status_code, latency)

        return StreamingResponse(generate(), media_type="text/event-stream")
    else:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{UPSTREAM_BASE}/v1/chat/completions",
                json=body, headers=headers
            )
        latency = int((time.time() - start) * 1000)
        try:
            data = resp.json()
        except Exception:
            data = {"error": {"message": "Upstream returned invalid JSON", "type": "api_error"}}
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cost = round((prompt_tokens + completion_tokens) * COST_PER_TOKEN, 6)
        _deduct_and_log(user_id, token_id, model, prompt_tokens, completion_tokens, cost, resp.status_code, latency)
        return JSONResponse(content=data, status_code=resp.status_code)

@router.post("/v1/embeddings")
async def embeddings(request: Request, token: Token = Depends(get_api_token), db: Session = Depends(get_db)):
    user = db.get(User, token.user_id)
    if not user:
        raise HTTPException(401, detail={"error": {"message": "User not found", "type": "invalid_request_error"}})
    _check_balance(user, token)
    body = await request.json()
    headers = {"Authorization": f"Bearer {UPSTREAM_KEY}", "Content-Type": "application/json"}
    start = time.time()
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{UPSTREAM_BASE}/v1/embeddings", json=body, headers=headers)
    latency = int((time.time() - start) * 1000)
    try:
        data = resp.json()
    except Exception:
        data = {"error": {"message": "Upstream returned invalid JSON", "type": "api_error"}}
    usage = data.get("usage") or {}
    total_tokens = usage.get("total_tokens", 0)
    cost = round(total_tokens * COST_PER_TOKEN * 0.1, 6)
    _deduct_and_log(user.id, token.id, body.get("model", ""), total_tokens, 0, cost, resp.status_code, latency)
    return JSONResponse(content=data, status_code=resp.status_code)

@router.get("/v1/models")
async def list_models(token: Token = Depends(get_api_token)):
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{UPSTREAM_BASE}/v1/models",
                                headers={"Authorization": f"Bearer {UPSTREAM_KEY}"})
    try:
        return JSONResponse(content=resp.json())
    except Exception:
        return JSONResponse(content={"object": "list", "data": []})

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
