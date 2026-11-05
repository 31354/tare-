"""
智能客服工单分诊系统 — 多 Agent 协作单文件后端
Router Agent → Specialist Agent → QA Agent
"""
from __future__ import annotations
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 环境变量（验收标准 5：无硬编码 API Key）
# ---------------------------------------------------------------------------
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")

# ---------------------------------------------------------------------------
# SQLite 持久化存储
# ---------------------------------------------------------------------------
# Railway 容器内数据目录；本地开发用项目根目录
_DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).resolve().parent / "tickets.db"))


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id   TEXT PRIMARY KEY,
            content     TEXT NOT NULL,
            category    TEXT NOT NULL,
            priority    TEXT NOT NULL,
            assignee    TEXT NOT NULL,
            suggestion  TEXT NOT NULL,
            trace       TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


_init_db()

# ---------------------------------------------------------------------------
# 关键词词典
# ---------------------------------------------------------------------------
CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "账单": [
        "发票", "抬头", "账单", "付款", "支付", "退款", "扣费", "充值",
        "会员费", "价格", "优惠", "券", "续费", "开票", "税务", "税号",
    ],
    "技术": [
        "报错", "无法登录", "登录不了", "闪退", "崩溃", "加载失败", "卡",
        "慢", "打不开", "不显示", "错误", "异常", "bug", "黑屏", "白屏",
        "上传失败", "下载失败", "连接不上", "网络", "系统", "app", "APP",
    ],
    "售后": [
        "退货", "换货", "退款", "商品坏了", "收到破损", "少件", "发错",
        "质量问题", "投诉", "差评", "售后", "维修", "保质期", "保修",
    ],
}

ASSIGNEE_MAP: Dict[str, str] = {
    "账单": "李财务 (finance@corp.com)",
    "技术": "王工 (tech@corp.com)",
    "售后": "张客服 (service@corp.com)",
}

PRIORITY_RULES: List[tuple] = [
    # (关键词列表, 优先级)
    (["紧急", "尽快", "马上", "立刻", "critical", "urgent"], "高"),
    (["报错", "崩溃", "无法登录", "不能用", "坏了", "损失", "影响大"], "高"),
    (["发票", "抬头", "退款", "退货", "换货", "续费", "扣费", "支付"], "中"),
]

VIOLATION_PATTERNS = [
    re.compile(r"保证|一定解决|100%|包修包换|无限责任|终身免费", re.I),
]

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
class TriageRequest(BaseModel):
    content: str = Field(..., min_length=1, description="用户工单内容")


class TriageResponse(BaseModel):
    ticket_id: str
    category: str
    priority: str
    assignee: str
    suggestion: str
    trace: List[str]


# ---------------------------------------------------------------------------
# Agent 1 — Router Agent
# ---------------------------------------------------------------------------
def router_agent(content: str) -> str:
    """根据关键词判断工单类型：技术 / 账单 / 售后 / 售后(默认)"""
    scores: Dict[str, int] = {k: 0 for k in CATEGORY_KEYWORDS}
    lower = content.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in lower:
                scores[cat] += 1
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    if scores[best] == 0:
        return "售后"  # 无法识别时归入售后兜底
    return best


# ---------------------------------------------------------------------------
# Agent 2 — Specialist Agent（共享逻辑，按 category 分支）
# ---------------------------------------------------------------------------
def _calc_priority(content: str) -> str:
    lower = content.lower()
    for keywords, p in PRIORITY_RULES:
        for kw in keywords:
            if kw.lower() in lower:
                return p
    return "中"


def _build_suggestion(category: str, content: str) -> str:
    if category == "账单":
        if "发票" in content or "抬头" in content or "税号" in content:
            return "请提供正确的发票抬头（公司全称或个人姓名）及税号，财务专员将在 1 个工作日内重新开具。"
        if "退款" in content or "退" in content:
            return "请说明退款原因及订单号，财务专员核实后将原路退回，预计 3-5 个工作日到账。"
        return "请提供相关账单编号或支付凭证，财务专员将尽快核查处理。"
    if category == "技术":
        if "登录" in content:
            return "建议先尝试清除浏览器缓存或切换网络；若仍无法登录，请提供账号和报错截图，技术专员跟进。"
        if "崩溃" in content or "闪退" in content:
            return "请提供设备型号、操作系统版本和崩溃复现步骤，技术专员将排查日志并修复。"
        return "请详细描述出现问题的操作步骤和现象，技术专员将根据描述复现并修复。"
    # 售后
    if "退" in content or "换" in content:
        return "请提供订单号和商品问题照片，售后专员审核通过后将发送退货地址。"
    if "坏" in content or "破损" in content:
        return "请在签收后 48 小时内联系我们并提供破损照片，售后专员将安排补发或退款。"
    return "请提供订单号及问题商品信息，售后专员将在 24 小时内联系你处理。"


def specialist_agent(category: str, content: str) -> Dict[str, str]:
    """专员 Agent：根据类型提取信息并生成初步建议"""
    priority = _calc_priority(content)
    assignee = ASSIGNEE_MAP[category]
    suggestion = _build_suggestion(category, content)
    return {
        "category": category,
        "priority": priority,
        "assignee": assignee,
        "suggestion": suggestion,
    }


# ---------------------------------------------------------------------------
# Agent 3 — QA Agent
# ---------------------------------------------------------------------------
def qa_agent(result: Dict[str, str]) -> Optional[str]:
    """质检：返回 None 表示合格，否则返回违规原因"""
    required = ["category", "priority", "assignee", "suggestion"]
    for k in required:
        v = result.get(k)
        if not v or not str(v).strip():
            return f"字段 {k} 为空"
    text = " ".join(str(v) for v in result.values())
    for pat in VIOLATION_PATTERNS:
        if pat.search(text):
            return "输出包含违规承诺词"
    return None


# ---------------------------------------------------------------------------
# 多 Agent 编排
# ---------------------------------------------------------------------------
def run_pipeline(content: str) -> Dict[str, Any]:
    trace: List[str] = []

    # Router
    category = router_agent(content)
    trace.append(f"Router→{category}")

    # Specialist
    result = specialist_agent(category, content)
    trace.append(f"Specialist({category})→完成")

    # QA：不合格则打回重做一次
    violation = qa_agent(result)
    if violation is not None:
        trace.append(f"QA→不合格({violation})，打回重做")
        # 重做：强制 suggestion 更保守、去除承诺词
        suggestion = result["suggestion"]
        for bad, good in [("保证", "建议"), ("一定", "可能"), ("100%", ""), ("包", "")]:
            suggestion = suggestion.replace(bad, good)
        result["suggestion"] = suggestion.strip() or "请联系对应专员进一步确认处理方案。"
        trace.append(f"Specialist(重做)→完成")
        violation2 = qa_agent(result)
        if violation2 is not None:
            # 即使二次仍不合格，也返回结果（已尽力修复）
            trace.append(f"QA→二次仍存问题({violation2})，强制放行")
        else:
            trace.append("QA→二次合格")
    else:
        trace.append("QA→合格")

    ticket_id = f"T{uuid.uuid4().hex[:8].upper()}"
    ticket = {
        "ticket_id": ticket_id,
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **result,
        "trace": trace,
    }
    # 写入 SQLite
    conn = _get_conn()
    conn.execute(
        "INSERT INTO tickets (ticket_id, content, category, priority, assignee, suggestion, trace, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ticket["ticket_id"],
            ticket["content"],
            ticket["category"],
            ticket["priority"],
            ticket["assignee"],
            ticket["suggestion"],
            json.dumps(ticket["trace"], ensure_ascii=False),
            ticket["created_at"],
        ),
    )
    conn.commit()
    conn.close()
    return ticket


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
app = FastAPI(title="智能客服工单分诊系统")


@app.post("/triage", response_model=TriageResponse)
def triage(req: TriageRequest):
    text = req.content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="工单内容不能为空")
    ticket = run_pipeline(text)
    return TriageResponse(
        ticket_id=ticket["ticket_id"],
        category=ticket["category"],
        priority=ticket["priority"],
        assignee=ticket["assignee"],
        suggestion=ticket["suggestion"],
        trace=ticket["trace"],
    )


@app.get("/health")
def health():
    conn = _get_conn()
    count = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    conn.close()
    return {"status": "ok", "tickets": count, "db": _DB_PATH}


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    conn = _get_conn()
    row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="工单不存在")
    ticket = dict(row)
    ticket["trace"] = json.loads(ticket["trace"])
    return ticket


# 根路径返回前端页面
@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
