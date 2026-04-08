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

# 粗略计费：每1000 token 约 0.014 元
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


def _upstream_headers() -> dict:
    return {
        "Authorization": f"Bearer {UPSTREAM_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


def _deduct_and_log(user_id: int, token_id: int, model: str,
                    pt: int, ct: int, cost: float, sc: int, lat: int, error: str = ""):
    """独立 session 完成扣费和日志，避免流式响应中原 session 已关闭的问题"""
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
            status_code=sc, latency_ms=lat, error=error[:512] if error else ""
        )
        db.add(log)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


async def _proxy_stream(url: str, body: dict, headers: dict,
                        user_id: int, token_id: int, model: str, start: float):
    async def generate():
        total_tokens = 0
        status_code = 200
        error_msg = ""
        async with httpx.AsyncClient(timeout=120) as client:
            try:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    status_code = resp.status_code
                    async for chunk in resp.aiter_bytes():
                        yield chunk
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
                error_msg = str(e)
                yield f'data: {{"error": "{error_msg}"}}\n\n'.encode()
        cost = round(total_tokens * COST_PER_TOKEN, 6)
        latency = int((time.time() - start) * 1000)
        _deduct_and_log(user_id, token_id, model, 0, total_tokens, cost, status_code, latency, error_msg)

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── /v1/chat/completions ──────────────────────────────────────────────────────

@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    token: Token = Depends(get_api_token),
    db: Session = Depends(get_db),
):
    user = db.get(User, token.user_id)
    if not user:
        raise HTTPException(401, detail={"error": {"message": "User not found", "type": "invalid_request_error"}})
    _reset_daily_if_needed(token, db)
    _check_rpm(token)
    _check_balance(user, token)

    body = await request.json()
    stream = body.get("stream", False)
    start = time.time()
    model = body.get("model", "")
    url = f"{UPSTREAM_BASE}/v1/chat/completions"

    if stream:
        return await _proxy_stream(url, body, _upstream_headers(), user.id, token.id, model, start)

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, json=body, headers=_upstream_headers())
    latency = int((time.time() - start) * 1000)
    try:
        data = resp.json()
    except Exception:
        data = {"error": {"message": "Upstream returned invalid JSON", "type": "api_error"}}
    usage = data.get("usage") or {}
    pt = usage.get("prompt_tokens", 0)
    ct = usage.get("completion_tokens", 0)
    cost = round((pt + ct) * COST_PER_TOKEN, 6)
    _deduct_and_log(user.id, token.id, model, pt, ct, cost, resp.status_code, latency)
    return JSONResponse(content=data, status_code=resp.status_code)


# ── /v1/completions（传统 Completions API）────────────────────────────────────

@router.post("/v1/completions")
async def completions(
    request: Request,
    token: Token = Depends(get_api_token),
    db: Session = Depends(get_db),
):
    user = db.get(User, token.user_id)
    if not user:
        raise HTTPException(401, detail={"error": {"message": "User not found", "type": "invalid_request_error"}})
    _check_balance(user, token)

    body = await request.json()
    stream = body.get("stream", False)
    start = time.time()
    model = body.get("model", "")
    url = f"{UPSTREAM_BASE}/v1/completions"

    if stream:
        return await _proxy_stream(url, body, _upstream_headers(), user.id, token.id, model, start)

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, json=body, headers=_upstream_headers())
    latency = int((time.time() - start) * 1000)
    try:
        data = resp.json()
    except Exception:
        data = {"error": {"message": "Upstream returned invalid JSON", "type": "api_error"}}
    usage = data.get("usage") or {}
    pt = usage.get("prompt_tokens", 0)
    ct = usage.get("completion_tokens", 0)
    cost = round((pt + ct) * COST_PER_TOKEN, 6)
    _deduct_and_log(user.id, token.id, model, pt, ct, cost, resp.status_code, latency)
    return JSONResponse(content=data, status_code=resp.status_code)


# ── /v1/embeddings ───────────────────────────────────────────────────────────

@router.post("/v1/embeddings")
async def embeddings(
    request: Request,
    token: Token = Depends(get_api_token),
    db: Session = Depends(get_db),
):
    user = db.get(User, token.user_id)
    if not user:
        raise HTTPException(401, detail={"error": {"message": "User not found", "type": "invalid_request_error"}})
    _check_balance(user, token)
    body = await request.json()
    start = time.time()
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{UPSTREAM_BASE}/v1/embeddings", json=body, headers=_upstream_headers()
        )
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


# ── /v1/models ───────────────────────────────────────────────────────────────

@router.get("/v1/models")
async def list_models(token: Token = Depends(get_api_token)):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{UPSTREAM_BASE}/v1/models",
                headers={"Authorization": f"Bearer {UPSTREAM_KEY}"}
            )
        return JSONResponse(content=resp.json())
    except Exception:
        return JSONResponse(content={"object": "list", "data": []})


# ── /v1/dashboard（余额查询，供 ChatBox 等工具使用）────────────────────────────

@router.get("/v1/dashboard")
async def dashboard(token: Token = Depends(get_api_token), db: Session = Depends(get_db)):
    """兼容部分第三方工具的余额查询接口"""
    user = db.get(User, token.user_id)
    if not user:
        raise HTTPException(401)
    return {
        "object": "billing_subscription",
        "has_payment_method": True,
        "soft_limit_usd": user.balance / 7.0,
        "hard_limit_usd": user.balance / 7.0,
        "system_hard_limit_usd": user.balance / 7.0,
        "plan": {"title": "Pay-as-you-go", "id": "payg"},
        # 中转站专属字段
        "balance_cny": round(user.balance, 4),
        "balance_usd": round(user.balance / 7.0, 6),
        "token_name": token.name,
        "used_today_cny": round(token.used_today, 4),
        "total_used_cny": round(token.total_used, 4),
    }
