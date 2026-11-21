"""Agent 工具集：OpenAI function-calling 格式的工具注册表。

设计要点：
- TOOL_SCHEMAS 直接以 OpenAI tools 协议格式暴露给 LLM（网关透传）；
- TOOL_FUNCS 是实际执行器，统一签名 (db, **args)，异常就地捕获
  —— 工具报错作为"观察结果"回传给模型，而不是打断整条链路；
- calculator 用 AST 白名单求值，绝不 exec()（防注入的经典考点）。
"""

import ast
import json
import operator
from datetime import datetime

from sqlalchemy.orm import Session

from app.kb_helpers import retrieve

# ---------------------------------------------------------------- schemas

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "kb_search",
            "description": "在用户上传的知识库中做语义检索，返回最相关的文档片段。"
                           "只用于用户私有文档/资料（项目文档、笔记等），"
                           "通用知识问题不要调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "integer",
                              "description": "知识库 ID。不传时自动使用当前会话关联的知识库"},
                    "query": {"type": "string", "description": "检索问题（中文即可）"},
                    "top_k": {"type": "integer", "description": "返回条数，默认 4"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "精确计算算术表达式（加减乘除、幂、括号）。"
                           "凡是需要数值计算的问题都必须用此工具，不要心算。",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string",
                                   "description": "合法算术表达式，如 (3.7**2)*12"},
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "now_time",
            "description": "获取当前日期时间。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# ---------------------------------------------------------------- calculator

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MAX_STR_LEN = 200  # 防超长表达式


def _safe_eval(node: ast.AST) -> float:
    """AST 白名单递归求值：只允许数字与四则运算节点。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("指数过大")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError(f"表达式包含不允许的元素: {type(node).__name__}")


def calculator(expression: str) -> dict:
    if len(expression) > _MAX_STR_LEN:
        return {"error": "表达式过长"}
    try:
        tree = ast.parse(expression, mode="eval")
        value = _safe_eval(tree.body)
        return {"expression": expression, "result": value}
    except ZeroDivisionError:
        return {"error": "除数为零"}
    except Exception as e:  # noqa: BLE001 语法错误/白名单拒绝统一回传
        return {"error": f"无法计算: {e}"}


# ---------------------------------------------------------------- executor

def kb_search(db: Session, kb_id: int, query: str, top_k: int = 4) -> dict:
    """知识库语义检索（复用第 4 周的 RAG 链路）。"""
    hits = retrieve(db, kb_id, query, top_k=max(1, min(top_k, 10)))
    return {"kb_id": kb_id, "query": query, "results": hits}


def now_time() -> dict:
    """当前时间。显式用上海时区：Docker 容器默认 UTC，直接 datetime.now()
    会差 8 小时（实测踩坑）。tzdata 缺失时退回容器本地时间。"""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:  # noqa: BLE001 极简镜像可能没有 tzdata
        now = datetime.now()
    return {"now": now.strftime("%Y-%m-%d %H:%M:%S (%A)")}


TOOL_FUNCS = {
    "kb_search": kb_search,
    "calculator": calculator,
    "now_time": now_time,
}


def execute_tool(db: Session, name: str, arguments: str,
                 default_kb_id: int | None = None) -> dict:
    """统一入口：解析参数 → 执行 → 异常降级为错误结果。

    default_kb_id：会话绑定的知识库 ID。kb_search 未显式传 kb_id 时
    自动兜底——用户不需要知道内部编号，选了知识库就能直接问。
    """
    if name not in TOOL_FUNCS:
        return {"error": f"未知工具: {name}"}
    try:
        args = json.loads(arguments) if arguments.strip() else {}
    except json.JSONDecodeError as e:
        return {"error": f"参数不是合法 JSON: {e}"}
    try:
        if name == "kb_search":
            if args.get("kb_id") is None and default_kb_id is not None:
                args["kb_id"] = default_kb_id
            if args.get("kb_id") is None:
                return {"error": "当前会话未关联知识库，无法检索。"
                                 "请提示用户在页面左上角的「关联知识库」下拉框中选择一个知识库后再提问。"
                                 "回复中不要提及任何内部编号、ID 或技术细节。"}
            return kb_search(db, **args)
        if name == "calculator":
            return calculator(**args)
        return now_time(**args)
    except TypeError as e:
        return {"error": f"参数不匹配: {e}"}
    except Exception as e:  # noqa: BLE001 任何工具内部错误都作为观察结果回传
        return {"error": f"工具执行失败: {e}"}
