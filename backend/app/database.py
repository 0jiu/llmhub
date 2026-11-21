from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,   # 连接取用前探活，避免 MySQL 8 小时断连报错
    pool_size=10,
    max_overflow=20,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖注入：每请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
