from datetime import datetime

from sqlalchemy import BigInteger, String, DateTime, func, Integer, Text, DECIMAL
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ModelPrice(Base):
    """模型单价表：用量计费的依据（元 / 百万 token）。"""
    __tablename__ = "model_price"

    model_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    input_price_per_m: Mapped[float] = mapped_column(DECIMAL(10, 4), nullable=False)
    output_price_per_m: Mapped[float] = mapped_column(DECIMAL(10, 4), nullable=False)


class ApiKey(Base):
    """平台对外签发的 API Key：库中只存 SHA256 哈希，明文仅在签发时返回一次。"""
    __tablename__ = "api_key"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)  # 列表脱敏展示用
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    rpm_limit: Mapped[int] = mapped_column(Integer, default=10)  # 每分钟允许请求数（令牌桶容量）
    status: Mapped[int] = mapped_column(Integer, default=1)  # 1 启用 0 吊销
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UsageLog(Base):
    """调用用量日志：计费、报表、降级分析的数据源。"""
    __tablename__ = "usage_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    api_key_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(DECIMAL(10, 6), default=0)
    status: Mapped[str] = mapped_column(String(16), default="success")
    fallback_from: Mapped[str] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )


class KnowledgeBase(Base):
    """知识库：RAG 检索的逻辑边界（向量索引与分块按 kb_id 隔离）。"""
    __tablename__ = "knowledge_base"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KbDocument(Base):
    """知识库文档：一次上传对应一条记录。"""
    __tablename__ = "kb_document"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=1)  # 1 正常 0 已删除
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KbChunk(Base):
    """文档分块：向量化与引用溯源的最小单位。"""
    __tablename__ = "kb_chunk"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # 在文档内的块序号
    content: Mapped[str] = mapped_column(Text, nullable=False)


class Conversation(Base):
    """会话：多轮对话的容器，"记忆"的载体（LLM 本身无状态，历史由平台拼装）。"""
    __tablename__ = "conversation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(128), default="新会话")
    kb_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 默认关联知识库
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ConversationMessage(Base):
    """会话消息：持久化的对话历史（多轮拼装的数据源）。

    role 限定 user / assistant；assistant 消息可附带 tool_trace（Agent 工具轨迹
    的 JSON 串，仅用于前端回放展示，不参与历史拼装——工具中间过程无需重放）。
    """
    __tablename__ = "conversation_message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
