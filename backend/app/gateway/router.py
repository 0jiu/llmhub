"""模型路由：按降级链逐个尝试，失败自动切换下一厂商。"""

import time

import httpx

from app.gateway.provider import PROVIDERS

HTTP_TIMEOUT = 60


async def chat_completion(payload: dict, model_chain: list[str]) -> dict:
    """非流式调用。

    返回: {"data": OpenAI 格式响应, "model_used": 实际命中的模型,
           "fallback_from": 首个失败的模型（无降级为 None）, "latency_ms": 耗时}
    全链失败抛出最后一个异常。
    """
    last_err: Exception | None = None
    fallback_from: str | None = None
    start = time.monotonic()

    for name in model_chain:
        p = PROVIDERS[name]
        body = {**payload, "model": p["model"], "stream": False, **p.get("extra_body", {})}
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.post(
                    f"{p['base_url']}/chat/completions",
                    headers={"Authorization": f"Bearer {p['key']}"},
                    json=body,
                )
                resp.raise_for_status()
                return {
                    "data": resp.json(),
                    "model_used": name,
                    "fallback_from": fallback_from,
                    "latency_ms": int((time.monotonic() - start) * 1000),
                }
        except Exception as e:  # noqa: BLE001 网关层吞掉上游差异，统一走降级
            last_err = e
            if fallback_from is None:
                fallback_from = name  # 记录降级链中第一个失败的主模型
            continue

    raise last_err if last_err else RuntimeError("model_chain 为空")
