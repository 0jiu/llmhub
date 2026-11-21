# 基于 llmhub-base:torch（第 4 周容器快照，已内置 torch==2.5.1+cpu 与全部 RAG 依赖）。
# 背景：torch CPU 轮子 174MB 国内 CDN 下载极慢且常挂起，commit 快照后彻底跳过该步骤。
# 以后 requirements 新增依赖都从这里增量安装，torch 层永不再下载。
FROM llmhub-base:torch

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_ENDPOINT=https://hf-mirror.com

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://mirrors.cloud.tencent.com/pypi/simple

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
