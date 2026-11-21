from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles

from app.database import Base, engine, SessionLocal
from app.api import agent, auth, chat, conversations, gateway, kb, keys
from app.models import ModelPrice

# 开发阶段直接 create_all 建表；表结构稳定后切换 Alembic 迁移
Base.metadata.create_all(bind=engine)


def _seed_prices() -> None:
    """单价表种子数据（单位：元/百万 token，占位价格，以各厂商官网为准）。"""
    rows = [
        ("deepseek-chat", "deepseek", 2.0, 8.0),
        ("glm-4-flash", "zhipu", 0.0, 0.0),
        ("qwen-turbo", "dashscope", 0.3, 0.6),
    ]
    db = SessionLocal()
    try:
        for name, provider, i, o in rows:
            if not db.get(ModelPrice, name):
                db.add(ModelPrice(model_name=name, provider=provider,
                                  input_price_per_m=i, output_price_per_m=o))
        db.commit()
    finally:
        db.close()


_seed_prices()

app = FastAPI(title="LLMHub", version="0.1.0",
              description="轻量级大模型应用开发平台",
              docs_url=None,   # 禁用默认 docs（CDN 版），用下方自定义本地托管版
              redoc_url=None)  # redoc 也依赖境外 CDN，禁用

# Swagger UI 静态资源本地托管（jsdelivr CDN 国内不稳定）
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/docs", include_in_schema=False)
def custom_docs():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        swagger_js_url="/static/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui.css",
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vue3 dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(keys.router)
app.include_router(kb.router)
app.include_router(gateway.router)
app.include_router(chat.router)
app.include_router(agent.router)
app.include_router(conversations.router)


@app.get("/health")
def health():
    return {"status": "ok"}


# ---- 前端生产托管（单端口部署） ------------------------------------------
# 演示/部署时不需要 vite dev server：npm run build 产出的 dist 由后端直接托管，
# 浏览器访问 http://localhost:8000 即完整应用（API 与页面同源，连 CORS 都省了）。
# 挂载顺序说明：上面的 API 路由先注册先匹配，这里的 catch-all 只兜住页面路径。
# SPA fallback：vue-router 是 history 模式，刷新 /login 这类路径时
# 服务器上并不存在该文件，必须返回 index.html 让前端路由接管。
def _find_dist() -> Path | None:
    for p in (Path("/app/dist"),                      # 容器内（compose 挂载）
              Path(__file__).parents[2] / "frontend" / "dist"):  # 本地直跑 uvicorn
        if (p / "index.html").is_file():
            return p
    return None


_DIST = _find_dist()
if _DIST is not None:
    if (_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    from fastapi.responses import FileResponse

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        candidate = (_DIST / full_path).resolve()
        # 防目录穿越：解析后必须仍在 dist 内
        if full_path and candidate.is_file() and str(candidate).startswith(str(_DIST.resolve())):
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
