"""Agent 问答接口：LangGraph 编排（LLM 决策 ⇄ 工具执行循环）+ 多轮会话。

与 /chat（固定 RAG 流水线）的区别：模型自己决定调不调工具、调几个、
按什么顺序调——图跑完后返回回答 + 完整工具调用轨迹。

多轮记忆的实现（面试核心点）：LLM 无状态，"记忆"是平台每次把历史
从库里取出来拼进 messages 演出来的。本模块负责三件事：
1. 历史拼装：conversation_id 有值时加载 user/assistant 历史（tool 中间
   消息不参与——工具过程无需重放，只保留结论）；
2. 上下文截断：HISTORY_MAX 条数预算防 token 线性膨胀（越聊越贵的治理）；
3. 持久化：本轮 user 消息与最终 assistant 回答落库，title 自动取首条消息。
"""

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.graph import MAX_ITERATIONS, agent_app, build_messages
from app.agent.stream_runner import run_agent_stream
from app.api.gateway import _log_usage
from app.database import SessionLocal, get_db
from app.deps import get_principal
from app.models import Conversation, ConversationMessage, KnowledgeBase

router = APIRouter(prefix="/agent", tags=["agent"])

HISTORY_MAX_MESSAGES = 20   # 拼进上下文的最大历史条数（条数预算版截断）
TITLE_MAX_LEN = 24          # 自动标题截取长度


class AgentChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=1024)
    conversation_id: int | None = None   # 传则续聊，不传则自动建会话
    kb_id: int | None = None             # 提供时 kb_search 工具才可用
    model: str = "deepseek-chat"
    model_chain: list[str] | None = None


def _prepare(body: AgentChatIn, principal, db: Session, create_conv: bool = True):
    """两个端点共用的前置逻辑：会话定位/创建 + 历史拼装 + 完整消息构造。

    create_conv=False 时（流式端点）只做校验与历史拼装，会话创建由
    调用方用独立数据库会话完成，避免请求 session 遗留未提交数据。
    返回 (user, key_row, conv|None, history, messages, chain, created, kb_id)。
    """
    user, key_row = principal

    # ---- 会话定位 / 创建 -------------------------------------------------
    if body.conversation_id is not None:
        conv = db.get(Conversation, body.conversation_id)
        if conv is None or conv.user_id != user.id:
            raise HTTPException(status_code=404, detail="会话不存在")
        created = False
    elif create_conv:
        conv = Conversation(
            user_id=user.id,
            title=body.question[:TITLE_MAX_LEN],
            kb_id=body.kb_id,
        )
        db.add(conv)
        db.flush()  # 拿到 conv.id，消息外键要用
        created = True
    else:
        conv, created = None, False

    # ---- 知识库解析：请求显式指定优先，否则沿用会话绑定的库 ---------------
    kb_id = body.kb_id or (conv.kb_id if conv is not None else None)
    if kb_id is not None:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None or kb.user_id != user.id:
            raise HTTPException(status_code=404, detail="知识库不存在")

    # ---- 历史拼装 + 截断 -------------------------------------------------
    history_rows = (db.query(ConversationMessage)
                    .filter(ConversationMessage.conversation_id == body.conversation_id,
                            ConversationMessage.role.in_(["user", "assistant"]))
                    .order_by(ConversationMessage.id.desc())
                    .limit(HISTORY_MAX_MESSAGES).all())
    history = [{"role": r.role, "content": r.content}
               for r in reversed(history_rows)]  # 反转回时间正序

    # ---- 消息构造（system + 历史 + 本轮问题）------------------------------
    chain = body.model_chain or [body.model]
    messages = build_messages(body.question, kb_id)
    # 历史插在 system 之后、本轮 user 问题之前
    messages = messages[:1] + history + messages[1:]
    return user, key_row, conv, history, messages, chain, created, kb_id


@router.post("/chat")
async def agent_chat(body: AgentChatIn, principal=Depends(get_principal),
                     db: Session = Depends(get_db)):
    _user, _key_row, conv, history, messages, chain, _created, kb_id = _prepare(
        body, principal, db)

    try:
        state = await agent_app.ainvoke({
            "messages": messages,
            "tool_trace": [],
            "db": db,
            "model_chain": chain,
            "bound_kb_id": kb_id,
            "iterations": 0,
            "model_used": None,
            "fallback_from": None,
        })
    except Exception as e:  # noqa: BLE001
        db.commit()  # 新建会话保留壳，避免只有 flush 未提交的数据悬空
        raise HTTPException(status_code=502,
                            detail=f"Agent 执行失败: {type(e).__name__}: {e}")

    answer = state["messages"][-1].get("content") or ""

    # ---- 本轮消息持久化 ---------------------------------------------------
    db.add(ConversationMessage(conversation_id=conv.id, role="user",
                               content=body.question))
    db.add(ConversationMessage(
        conversation_id=conv.id, role="assistant", content=answer,
        tool_trace=json.dumps(state["tool_trace"], ensure_ascii=False)
        if state["tool_trace"] else None,
    ))
    db.commit()

    return {
        "answer": answer,
        "conversation_id": conv.id,
        "tool_trace": state["tool_trace"],
        "history_used": len(history),          # 观察截断行为用
        "model_used": state["model_used"],
        "fallback_from": state["fallback_from"],
        "iterations": state["iterations"],
        "max_iterations": MAX_ITERATIONS,
    }


@router.post("/chat/stream")
async def agent_chat_stream(body: AgentChatIn, principal=Depends(get_principal),
                            db: Session = Depends(get_db)):
    """Agent 流式问答（SSE）。事件协议：

    event: meta   → {"conversation_id"}          连接建立即下发，前端续聊要用
    event: delta  → {"text"}                     LLM 增量文本（打字机）
    event: tool   → {"tool","arguments","ok","result_preview"}  工具执行完成
    event: final  → {"answer","tool_trace","iterations","model_used",...}
    event: error  → {"detail"}

    写事务全部走独立 SessionLocal，不复用 Depends 注入的 db：
    StreamingResponse 的生成器在响应阶段执行，跨过了请求依赖的生命周期，
    依赖 session 上 flush 过的未提交数据可能在生成器运行前被提前终结
    （实测：会话行丢失而消息行落库）。独立会话从创建到提交自始至终
    自己管理，彻底绕开这个坑。db（请求 session）只用于只读的 _prepare。
    """
    _user, key_row, _conv_ref, _history, messages, chain, _created, kb_id = _prepare(
        body, principal, db, create_conv=False)
    api_key_id = key_row.id if key_row else 0
    start = time.monotonic()

    import logging
    logger = logging.getLogger("uvicorn.error")

    async def sse_gen():
        s = SessionLocal()   # 独立会话：本生成器所有写事务的唯一通道
        conv = None
        answer, tool_trace, usage = "", [], None
        model_used = fallback_from = None
        ok = True
        try:
            # 会话创建 + 立即提交：meta 事件下发的是已持久化的真实 id
            if body.conversation_id is not None:
                conv = s.get(Conversation, body.conversation_id)
                if conv is None or conv.user_id != _user.id:
                    raise ValueError("会话不存在")
            else:
                conv = Conversation(user_id=_user.id,
                                    title=body.question[:TITLE_MAX_LEN],
                                    kb_id=body.kb_id)
                s.add(conv)
                s.commit()

            yield f"event: meta\ndata: {json.dumps({'conversation_id': conv.id})}\n\n"

            async for ev in run_agent_stream(messages, chain, db, bound_kb_id=kb_id):
                if ev["type"] == "delta":
                    answer += ev["text"]
                elif ev["type"] == "tool":
                    tool_trace.append(ev)
                elif ev["type"] == "final":
                    model_used = ev["model_used"]
                    fallback_from = ev["fallback_from"]
                    usage = ev.get("usage")
                yield f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            ok = False
            detail = f"Agent 流式执行失败: {type(e).__name__}: {e}"
            logger.exception("Agent 流式执行失败")
            yield f"event: error\ndata: {json.dumps({'detail': detail}, ensure_ascii=False)}\n\n"
        finally:
            # 收尾无论成功/失败/客户端中途断开都要做：消息持久化 + 用量计费
            try:
                if conv is not None:
                    s.add(ConversationMessage(conversation_id=conv.id, role="user",
                                              content=body.question))
                    if answer:
                        s.add(ConversationMessage(
                            conversation_id=conv.id, role="assistant", content=answer,
                            tool_trace=json.dumps(tool_trace, ensure_ascii=False)
                            if tool_trace else None,
                        ))
                if model_used and usage:
                    _log_usage(s, api_key_id, model_used, fallback_from,
                               usage.get("prompt_tokens", 0),
                               usage.get("completion_tokens", 0),
                               "success" if ok else "error",
                               int((time.monotonic() - start) * 1000))
                s.commit()
                logger.info("SSE 收尾持久化完成 conv=%s answer_len=%s", conv.id if conv else None, len(answer))
            except Exception:  # noqa: BLE001 收尾失败不能影响已发出的响应
                logger.exception("SSE 收尾持久化失败")
                s.rollback()
            finally:
                s.close()

    return StreamingResponse(
        sse_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
