"""文本切分：把长文档切成适合向量化的块。

策略：优先按段落（空行）聚合；单段超长再按字符窗口滑动切，
块间保留 overlap 重叠，避免语义被硬切断。
"""

CHUNK_SIZE = 500    # 块目标长度（字符）
CHUNK_OVERLAP = 80  # 相邻块重叠字符数


def split_text(text: str, chunk_size: int = CHUNK_SIZE,
               chunk_overlap: int = CHUNK_OVERLAP) -> list[str]:
    """切分文本为块列表。空块（纯空白）会被丢弃。"""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []

    # 1. 按段落聚合：段落拼到接近 chunk_size 就成块
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    blocks: list[str] = []
    buf = ""
    for para in paragraphs:
        # 2. 段落本身超长 → 滑动窗口硬切
        if len(para) > chunk_size:
            if buf:
                blocks.append(buf)
                buf = ""
            step = chunk_size - chunk_overlap
            for i in range(0, len(para), step):
                blocks.append(para[i:i + chunk_size])
            continue
        if len(buf) + len(para) + 1 > chunk_size:
            blocks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf:
        blocks.append(buf)

    return [b.strip() for b in blocks if b.strip()]
