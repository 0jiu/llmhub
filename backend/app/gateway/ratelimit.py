"""Redis 令牌桶限流：Lua 脚本原子执行，按 API Key 维度限流。

为什么用 Lua：令牌桶的「读余额 → 补充 → 扣减 → 写回」必须是原子操作，
并发请求下否则会出现超卖（两个请求同时读到 1 个令牌都放行）。
Redis 单线程执行 Lua 天然保证原子性，这是业界标准做法。
"""

import time

import redis.asyncio as aioredis

from app.config import settings

# 令牌桶参数：capacity 容量 / refill_rate 每秒补充速率 / now 毫秒时间戳 / requested 本次请求数
# 返回 {allowed(1/0), 剩余令牌(整数), retry_after(秒)}
_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local ts = tonumber(bucket[2])
if tokens == nil then
    tokens = capacity
    ts = now
end

local elapsed = math.max(0, now - ts) / 1000.0
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
local retry_after = 0
if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
else
    retry_after = math.ceil((requested - tokens) / refill_rate)
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', key, math.ceil(capacity / refill_rate * 1000) + 60000)
return {allowed, tostring(math.floor(tokens)), retry_after}
"""

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def check_rate_limit(api_key_id: int, rpm_limit: int) -> dict:
    """令牌桶检查：桶容量与每分钟补充量均为 rpm_limit。

    返回 {"allowed": bool, "remaining": int, "retry_after": 秒}。
    """
    now_ms = int(time.time() * 1000)
    result = await get_redis().eval(
        _TOKEN_BUCKET_LUA, 1,
        f"ratelimit:apikey:{api_key_id}",
        rpm_limit, rpm_limit / 60.0, now_ms, 1,
    )
    return {
        "allowed": int(result[0]) == 1,
        "remaining": int(result[1]),
        "retry_after": int(result[2]),
    }
