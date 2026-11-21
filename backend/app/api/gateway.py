"""OpenAI 兼容网关入口：/v1/chat/completions（流式 / 非流式）。

鉴权：JWT（调试）或平台 API Key（应用调用）双模式；
API Key 调用先过 Redis 令牌桶限流，用量按 Key 归因落库。
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_principal
from app.gateway.provider import PROVIDERS, is_configured
from app.gateway.router import chat_completion
from app.gateway.stream import stream_chat
from app.gateway.ratelimit import check_rate_limit
from app.models import ModelPrice, UsageLog

router = APIRouter(prefix="/v1", tags=["gateway"])


@router.get("/models")
async def list_models(_principal=Depends(get_principal)):
    """可用模型列表（OpenAI 协议的 GET /v1/models 端点）。

    前端模型选择器据此渲染；新增厂商在 provider.py 注册后自动出现，
    未配置 Key 的厂商被过滤（避免用户选到必失败的模型）。
    """
    return [
        {"id": name, "provider": p["provider"], "owned_by": p["provider"]}
        for name, p in PROVIDERS.items() if is_configured(name)
    ]


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "deepseek-chat"
    messages: list[Message] = Field(min_length=1)
    temperature: float = 0.7
    stream: bool = False
    # 降级链：默认只请求一个模型；显式传入如 ["deepseek-chat","glm-4-flash"] 启用降级
    model_chain: list[str] | None = None


def _log_usage(db: Session, api_key_id: int, model_used: str, fallback_from: str | None,
               prompt_tokens: int, completion_tokens: int,
               status: str, latency_ms: int) -> None:
    """按单价表折算成本并落库（api_key_id=0 表示 JWT 调试调用）。"""
    price = db.get(ModelPrice, model_used)
    cost = 0.0
    if price:
        cost = float(
            prompt_tokens / 1e6 * float(price.input_price_per_m)
            + completion_tokens / 1e6 * float(price.output_price_per_m)
        )
    db.add(UsageLog(
        api_key_id=api_key_id,
        model_name=model_used,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=cost,
        status=status,
        fallback_from=fallback_from,
        latency_ms=latency_ms,
    ))
    db.commit()


@router.post("/chat/completions")
async def chat_completions(
    body: ChatRequest,
    principal=Depends(get_principal),
    db: Session = Depends(get_db),
):
    user, key_row = principal
    api_key_id = key_row.id if key_row else 0

    # API Key 调用 → Redis 令牌桶限流（JWT 调试调用不限）
    rate_headers = {}
    if key_row is not None:
        rl = await check_rate_limit(key_row.id, key_row.rpm_limit)
        rate_headers = {
            "X-RateLimit-Limit": str(key_row.rpm_limit),
            "X-RateLimit-Remaining": str(rl["remaining"]),
        }
        if not rl["allowed"]:
            raise HTTPException(
                status_code=429,
                detail=f"请求过于频繁：该 Key 限 {key_row.rpm_limit} 次/分钟，请 {rl['retry_after']} 秒后重试",
                headers={**rate_headers, "Retry-After": str(rl["retry_after"])},
            )

    chain = body.model_chain or [body.model]
    for m in chain:
        if m not in _known_models():
            raise HTTPException(status_code=400, detail=f"未知模型: {m}")

    payload = {
        "messages": [m.model_dump() for m in body.messages],
        "temperature": body.temperature,
    }

    if body.stream:
        async def sse_gen():
            async for model_used, data, _emitted in stream_chat(payload, chain):
                if data == "[DONE]":
                    yield "data: [DONE]\n\n"
                    return
                chunk = json.loads(data)
                usage = chunk.pop("usage", None)  # include_usage 时最后一个 chunk 携带
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                if usage:
                    _log_usage(db, api_key_id, model_used, None,
                               usage.get("prompt_tokens", 0),
                               usage.get("completion_tokens", 0),
                               "success", 0)
        return StreamingResponse(sse_gen(), media_type="text/event-stream",
                                 headers=rate_headers)

    # 非流式
    try:
        result = await chat_completion(payload, chain)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"所有上游模型调用失败: {type(e).__name__}")
    data = result["data"]
    usage = data.get("usage", {})
    _log_usage(
        db, api_key_id, result["model_used"], result["fallback_from"],
        usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
        "fallback" if result["fallback_from"] else "success",
        result["latency_ms"],
    )
    data["model_used"] = result["model_used"]  # 非标准字段，便于调试观察降级
    return JSONResponse(content=data, headers=rate_headers)


def _known_models() -> set[str]:
    from app.gateway.provider import PROVIDERS
    return set(PROVIDERS.keys())
