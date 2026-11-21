"""Agent 流式执行器：手写 agent⇄tools 循环的流式版本。

与 graph.py 的非流式图逻辑等价，但没有用 LangGraph 编译图，原因：
1. 流式需要把 LLM 的增量 token 实时转发给前端（SSE delta 事件），
   而编译图的节点粒度是「整轮完成」，拿不到 token 级流；
2. OpenAI 流式协议下 tool_calls 是分片到达的（首片带 id/name，
   后续片只追加 arguments 字符串），必须手动累积后才能执行工具；
3. 需要自定义 SSE 事件协议（meta/delta/tool/final），工具轨迹
   要在执行的瞬间推给前端，而不是全部结束后一次性返回。

降级语义与 stream.py 一致：仅建连/首个 chunk 前失败才切换下一厂商，
一旦开始输出内容，中途失败只能终止。
"""

import asyncio
import json
from collections.abc import AsyncGenerator

from app.agent.graph import MAX_ITERATIONS, TOOL_RESULT_MAX_CHARS
from app.agent.tools import TOOL_SCHEMAS, execute_tool
from app.gateway.stream import stream_chat

# 整链瞬时失败（限流/抖动）的自动重试：仅当本轮尚未向前端输出任何
# 内容时才安全——已经流出去的字无法撤回，中途失败只能终止。
MAX_RETRIES = 2
RETRY_DELAYS = [2.0, 5.0]   # 指数退避


async def run_agent_stream(
    messages: list[dict], chain: list[str], db,
    bound_kb_id: int | None = None,
) -> AsyncGenerator[dict, None]:
    """跑一遍 agent 循环，产出事件字典流。

    bound_kb_id：会话绑定的知识库 ID，kb_search 未传 kb_id 时兜底，
    避免模型反过来向用户索要内部编号。

    事件类型：
    - {"type": "delta", "text": str}              LLM 增量文本（打字机）
    - {"type": "tool", **trace_entry}             某个工具刚执行完
    - {"type": "final", ...}                      全部结束（含统计信息）
    - {"type": "error", "detail": str}            上游错误
    """
    tool_trace: list[dict] = []
    iterations = 0
    model_used: str | None = None
    fallback_from: str | None = None
    usage: dict | None = None
    answer = ""

    for _round in range(MAX_ITERATIONS):
        content_buf = ""
        tool_acc: dict[int, dict] = {}   # index -> {"id","name","arguments"}

        payload = {"messages": messages, "tools": TOOL_SCHEMAS, "temperature": 0.3}

        for attempt in range(MAX_RETRIES + 1):
            round_failed = False
            async for name, data, _emitted in stream_chat(payload, chain):
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if "error" in chunk:
                    raw = chunk["error"]
                    # 建连阶段整链失败且本轮还没有任何输出 → 退避后自动重试
                    if ("all providers failed" in raw and attempt < MAX_RETRIES
                            and not content_buf and not tool_acc):
                        round_failed = True
                        break
                    # 区分「整链失败」与「输出中途断流」，给前端可读文案
                    hint = ("上游模型暂时不可用（可能触发限流），降级链也已失败，请稍后重试"
                            if "all providers failed" in raw
                            else "回答生成中途连接中断，本轮无法恢复，请重新提问")
                    yield {"type": "error", "detail": hint, "raw": raw}
                    return
                if chunk.get("usage"):           # include_usage 的收尾 chunk
                    usage = chunk["usage"]
                if model_used is None:
                    model_used = name
                    if name != chain[0]:         # 实际命中的不是链首 → 发生了降级
                        fallback_from = chain[0]

                delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                if delta.get("content"):
                    content_buf += delta["content"]
                    yield {"type": "delta", "text": delta["content"]}
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]

            if not round_failed:
                break
            # 重试：重置本轮累积（本轮没有输出过任何内容，重置是安全的）
            content_buf = ""
            tool_acc = {}
            await asyncio.sleep(RETRY_DELAYS[attempt])

        iterations += 1

        if not tool_acc:
            # 本轮没有要工具 → 最终答案，循环结束
            answer = content_buf
            break

        # 中间轮：把带 tool_calls 的 assistant 消息拼回上下文（OpenAI 协议要求）
        tc_list = [{"id": s["id"], "type": "function",
                    "function": {"name": s["name"], "arguments": s["arguments"]}}
                   for s in tool_acc.values()]
        messages = messages + [{"role": "assistant",
                                "content": content_buf or None,
                                "tool_calls": tc_list}]

        # 逐个执行工具：结果回填消息流，轨迹即时推给前端
        for s in tool_acc.values():
            result = execute_tool(db, s["name"], s["arguments"],
                                  default_kb_id=bound_kb_id)
            trace_entry = {
                "tool": s["name"],
                "arguments": s["arguments"],
                "ok": "error" not in result,
                "result_preview": json.dumps(result, ensure_ascii=False)[:TOOL_RESULT_MAX_CHARS],
            }
            tool_trace.append(trace_entry)
            yield {"type": "tool", **trace_entry}
            messages = messages + [{
                "role": "tool",
                "tool_call_id": s["id"],
                "content": json.dumps(result, ensure_ascii=False)[:TOOL_RESULT_MAX_CHARS],
            }]
        # 继续下一轮 LLM 决策

    yield {
        "type": "final",
        "answer": answer,
        "tool_trace": tool_trace,
        "iterations": iterations,
        "model_used": model_used,
        "fallback_from": fallback_from,
        "usage": usage,
        "max_iterations": MAX_ITERATIONS,
    }
