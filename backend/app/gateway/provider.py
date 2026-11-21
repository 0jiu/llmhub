"""厂商注册表：全部走 OpenAI 兼容协议，只差 base_url / key / model 名。

新增厂商只需在此注册，路由与流式层零改动。
"""

from app.config import settings

PROVIDERS: dict[str, dict] = {
    "deepseek-chat": {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "key": settings.deepseek_key,
        "model": "deepseek-chat",
        "supports_stream_options": True,   # 支持 stream_options.include_usage
    },
    "glm-4-flash": {
        "provider": "zhipu",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "key": settings.glm_key,
        "model": "glm-4-flash",
        "supports_stream_options": False,
    },
    "qwen-turbo": {
        "provider": "dashscope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key": settings.qwen_key,
        "model": "qwen-turbo",
        "supports_stream_options": False,
    },
    # 硅基流动：聚合推理平台，以下均为长期免费模型；一个 Key 顶三个选项
    "qwen3-8b": {
        "provider": "siliconflow",
        "base_url": "https://api.siliconflow.cn/v1",
        "key": settings.siliflow_key,
        "model": "Qwen/Qwen3-8B",
        "supports_stream_options": True,
        # Qwen3 默认开启思考模式，演示场景关掉，首字延迟从数十秒降到亚秒
        "extra_body": {"enable_thinking": False},
    },
    "qwen2.5-7b": {
        "provider": "siliconflow",
        "base_url": "https://api.siliconflow.cn/v1",
        "key": settings.siliflow_key,
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "supports_stream_options": True,
    },
    "glm-4-9b": {
        "provider": "siliconflow",
        "base_url": "https://api.siliconflow.cn/v1",
        "key": settings.siliflow_key,
        "model": "THUDM/GLM-4-9B-0414",
        "supports_stream_options": True,
    },
}

# 未配置 Key 的厂商会被 /v1/models 过滤掉（占位符/空值不算配置），
# 填入真实 Key 后模型自动出现在前端选择器
_PLACEHOLDER_MARKS = ("xxxx",)


def is_configured(model_name: str) -> bool:
    key = PROVIDERS.get(model_name, {}).get("key", "")
    return bool(key) and not any(m in key.lower() for m in _PLACEHOLDER_MARKS)
