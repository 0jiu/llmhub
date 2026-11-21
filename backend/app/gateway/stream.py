"""SSE 流式转发：透传上游 OpenAI 格式 chunk，连接失败自动降级。"""

import time

import httpx

from app.gateway.provider import PROVIDERS

HTTP_TIMEOUT = 60


async def stream_chat(payload: dict, model_chain: list[str]):
    """逐行产出 OpenAI 流式 chunk 的 JSON 字符串。

    降级策略：仅在「建立连接 / 首个 chunk 之前」失败时切换下一厂商；
    一旦开始输出内容，中途失败只能终止（HTTP 已提交，无法回退）。
    """
    last_err: Exception | None = None

    for name in model_chain:
        p = PROVIDERS[name]
        body = {**payload, "model": p["model"], "stream": True, **p.get("extra_body", {})}
        if p.get("supports_stream_options"):
            body["stream_options"] = {"include_usage": True}

        emitted = False
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    f"{p['base_url']}/chat/completions",
                    headers={"Authorization": f"Bearer {p['key']}"},
                    json=body,
                ) as resp:
                    if resp.status_code != 200:
                        raise httpx.HTTPStatusError(
                            f"upstream {resp.status_code}", request=resp.request, response=resp
                        )
                    async for raw in resp.aiter_lines():
                        if not raw.startswith("data: "):
                            continue
                        data = raw[6:]
                        if data == "[DONE]":
                            yield name, data, emitted
                            return
                        emitted = True
                        yield name, data, emitted
            return  # 正常结束（无 DONE 行的兜底）
        except Exception as e:  # noqa: BLE001
            if emitted:
                # 中途断流：无法降级，直接终止并让调用方收尾
                yield name, f'{{"error": "stream interrupted: {type(e).__name__}"}}', True
                return
            last_err = e
            continue  # 尚未输出内容，静默降级到下一厂商

    yield (
        model_chain[0] if model_chain else "unknown",
        f'{{"error": "all providers failed: {type(last_err).__name__ if last_err else "empty chain"}"}}',
        False,
    )


def now_ms() -> int:
    return int(time.monotonic() * 1000)
