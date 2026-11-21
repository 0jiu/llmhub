"""LangGraph 编排：agent(LLM 决策) ⇄ tools(执行) 循环，直到模型不再要工具。

节点职责：
- agent 节点：带 TOOL_SCHEMAS 调网关（复用第 2 周降级链），把助手消息追加进 state；
- tools 节点：逐个执行 tool_calls，结果以 role=tool 消息回填（"观察"），同时记录轨迹；
- 条件边：模型要了工具且未超轮次 → tools，否则 → END。

MAX_ITERATIONS 是防死循环的保险丝：模型若反复索要工具，到轮次上限强制收口。
"""

import json
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.agent.tools import TOOL_SCHEMAS, execute_tool
from app.gateway.router import chat_completion

MAX_ITERATIONS = 6  # 单次问答最多 LLM 决策轮数
TOOL_RESULT_MAX_CHARS = 3000  # 单个工具结果回传给模型的最大长度


class AgentState(TypedDict):
    messages: list[dict]          # OpenAI 协议消息历史（含 system/user/assistant/tool）
    tool_trace: list[dict]        # 工具调用轨迹（返回给前端展示）
    db: Any                       # SQLAlchemy Session（运行时注入，不序列化）
    model_chain: list[str]        # 降级链
    bound_kb_id: int | None       # 会话绑定的知识库 ID（kb_search 未传 kb_id 时兜底）
    iterations: int               # 已执行的 LLM 决策轮数
    model_used: str | None
    fallback_from: str | None


async def agent_node(state: AgentState) -> dict:
    """LLM 决策节点：带工具定义调大模型，输出 assistant 消息（可能含 tool_calls）。"""
    payload = {
        "messages": state["messages"],
        "tools": TOOL_SCHEMAS,
        "temperature": 0.3,   # 工具调用要确定性，压低随机度
    }
    result = await chat_completion(payload, state["model_chain"])
    msg = result["data"]["choices"][0]["message"]
    return {
        "messages": state["messages"] + [msg],
        "model_used": result["model_used"],
        "fallback_from": result["fallback_from"],
        "iterations": state["iterations"] + 1,
    }


def tools_node(state: AgentState) -> dict:
    """工具执行节点：执行上一条 assistant 消息里的全部 tool_calls。"""
    db, trace, new_msgs = state["db"], list(state["tool_trace"]), []
    last = state["messages"][-1]

    for tc in last.get("tool_calls", []):
        fn = tc.get("function", {})
        result = execute_tool(db, fn.get("name", ""), fn.get("arguments", ""),
                              default_kb_id=state.get("bound_kb_id"))
        trace.append({
            "tool": fn.get("name"),
            "arguments": fn.get("arguments"),
            "ok": "error" not in result,
            "result_preview": json.dumps(result, ensure_ascii=False)[:TOOL_RESULT_MAX_CHARS],
        })
        # OpenAI 协议：工具结果必须带 tool_call_id 回传
        new_msgs.append({
            "role": "tool",
            "tool_call_id": tc.get("id", ""),
            "content": json.dumps(result, ensure_ascii=False)[:TOOL_RESULT_MAX_CHARS],
        })

    return {"tool_trace": trace, "messages": state["messages"] + new_msgs}


def route_after_agent(state: AgentState) -> str:
    """条件边：要工具且未超轮次 → tools；否则结束。"""
    if state["iterations"] >= MAX_ITERATIONS:
        return END  # 轮次兜底：即使模型还想调工具也强制收口
    last = state["messages"][-1]
    return "tools" if last.get("tool_calls") else END


# 模块级编译一次，全请求复用（图结构静态，业务数据走 state 注入）
graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", tools_node)
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")  # 工具结果回给模型，进入下一轮决策
agent_app = graph.compile()


SYSTEM_PROMPT = (
    "你是 LLMHub 平台的智能助手。你可以调用工具：kb_search（知识库检索）、"
    "calculator（精确计算）、now_time（当前时间）。规则：\n"
    "1. kb_search 只用于检索用户上传到知识库的私有文档/资料（如项目文档、笔记）。"
    "只有当用户明确提到'我的资料/我的项目/知识库里的内容'时才调用它；\n"
    "2. 通用知识与常识问题（如科普、营养、历史、闲聊）直接用自己的知识回答，"
    "不要调用 kb_search，更不要因为会话没关联知识库而拒绝回答；\n"
    "3. 涉及数值计算的问题，必须调用 calculator，不要心算；\n"
    "4. 回答基于工具返回的结果；kb_search 结果中没有的信息要明确说明'资料中未提及'，不要编造；\n"
    "5. 回答用中文，简洁清晰。"
)


def build_messages(question: str, kb_id: int | None = None) -> list[dict]:
    """构造初始消息。kb_id 有值时在系统提示词里声明会话绑定的知识库，
    并禁止模型向用户索要内部编号（用户体验：选了库就直接问）。"""
    system = SYSTEM_PROMPT
    if kb_id is not None:
        system += (
            f"\n5. 当前会话已关联知识库（内部编号 {kb_id}）。涉及资料/文档/项目内容的"
            "问题直接调用 kb_search 并使用该编号作为 kb_id 参数；"
            "绝不要向用户询问知识库编号——用户界面不展示内部 ID，用户不可能知道。"
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
