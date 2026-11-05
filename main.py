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

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 环境变量（验收标准 5：无硬编码 API Key）
# ---------------------------------------------------------------------------
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "glm-4-flash")  # 免费最快
LLM_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
_HAS_LLM = bool(LLM_API_KEY)

# ---------------------------------------------------------------------------
# LLM 调用工具（有 Key 就用，无 Key / 失败都返回 None 让调用方降级）
# ---------------------------------------------------------------------------
def _llm_call(system_prompt: str, user_prompt: str, expect_json: bool = True) -> Optional[str]:
    """调智谱 LLM，失败返回 None。expect_json=True 时要求返回可解析 JSON。"""
    if not _HAS_LLM:
        return None
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        resp = httpx.post(
            LLM_API_URL,
            headers={
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "messages": messages,
                "temperature": 0.1,   # 低温度保证稳定
                "max_tokens": 512,
                "response_format": {"type": "json_object"} if expect_json else None,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not content:
            return None
        if expect_json:
            json.loads(content)  # 验证能解析
        return content
    except Exception:
        return None

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
    content: str = Field(..., description="用户工单内容")


class TriageResponse(BaseModel):
    ticket_id: str
    category: str
    priority: str
    assignee: str
    suggestion: str
    trace: List[str]


# ---------------------------------------------------------------------------
# Agent 1 — Router Agent（有 LLM 用语义分类，无 LLM 降级关键词匹配）
# ---------------------------------------------------------------------------
_ROUTER_SYSTEM = (
    "你是一个工单分诊 Router，判断用户工单属于哪一类。"
    "只输出 {\"category\": \"账单\"} / {\"category\": \"技术\"} / {\"category\": \"售后\"} 三种之一的 JSON。"
    "账单=发票/支付/退款/扣费；技术=报错/闪退/无法登录；售后=退货/换货/破损/投诉。"
)


def router_agent(content: str) -> str:
    """根据关键词判断工单类型：技术 / 账单 / 售后 / 售后(默认)"""
    # --- 优先 LLM 语义分类 ---
    if _HAS_LLM:
        raw = _llm_call(_ROUTER_SYSTEM, f"用户工单：{content}")
        if raw:
            try:
                data = json.loads(raw)
                cat = data.get("category", "")
                if cat in ("账单", "技术", "售后"):
                    return cat
            except Exception:
                pass

    # --- 降级：关键词匹配 ---
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
# Agent 2 — Specialist Agent（有 LLM 用自然话术，无 LLM 降级模板分支）
# ---------------------------------------------------------------------------
_SPECIALIST_SYSTEM = (
    "你是智能客服专员，根据工单分类和用户内容，生成一段 80 字以内的处理建议。"
    "要求：专业、友好、不出现'保证''一定''100%'等绝对化承诺词。"
    "只输出 {\"suggestion\": \"...\"} 的 JSON，不要其他内容。"
)


def _llm_suggestion(category: str, content: str) -> Optional[str]:
    """LLM 生成自然话术建议，失败返回 None"""
    prompt = f"工单分类：{category}\n用户原话：{content}\n请生成处理建议。"
    raw = _llm_call(_SPECIALIST_SYSTEM, prompt)
    if raw:
        try:
            data = json.loads(raw)
            s = data.get("suggestion", "").strip()
            if s and len(s) >= 10:
                return s
        except Exception:
            pass
    return None


def _calc_priority(content: str) -> str:
    lower = content.lower()
    for keywords, p in PRIORITY_RULES:
        for kw in keywords:
            if kw.lower() in lower:
                return p
    return "中"


def _build_suggestion(category: str, content: str) -> str:
    """有 LLM 优先用自然话术，无 LLM / 失败降级模板"""
    # --- 优先 LLM ---
    llm_sug = _llm_suggestion(category, content)
    if llm_sug:
        return llm_sug + "\n\n💡 （AI 生成）"

    # --- 降级：模板分支 ---
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


def _try_mcp_tools(content: str) -> Optional[str]:
    """尝试通过 MCP 调用外部工具，返回工具输出（None 表示不适用）"""
    from mcp_server import call_tool_sync

    # --- 天气 Tool ---
    if any(kw in content for kw in ("天气", "下雨", "出门", "带伞", "气温", "冷不冷", "热不热")):
        city_match = re.search(r"([\u4e00-\u9fa5]{2,4})(?:的?天气|市|省|区)", content)
        if not city_match:
            city_match = re.search(
                r"(北京|上海|广州|深圳|杭州|成都|武汉|南京|西安|重庆|天津|苏州)", content
            )
        city = city_match.group(1) if city_match else "北京"
        result = call_tool_sync("query_weather", {"city": city})
        if result:
            return f"【MCP · 天气查询】{result}"

    # --- 快递 Tool ---
    tracking_match = re.search(r"([A-Za-z0-9]{8,20})", content)
    tracking_kw = any(kw in content for kw in ("快递", "物流", "到哪", "发货", "包裹", "运单", "单号"))
    if tracking_match or tracking_kw:
        tracking_no = tracking_match.group(1) if tracking_match else "SF1234567890"
        result = call_tool_sync("query_express", {"tracking_no": tracking_no})
        if result:
            return f"【MCP · 快递查询】\n{result}"

    return None


def specialist_agent(category: str, content: str) -> Dict[str, str]:
    """专员 Agent：根据类型提取信息并生成初步建议，支持 MCP 外部 Tool"""
    priority = _calc_priority(content)
    assignee = ASSIGNEE_MAP[category]

    # 先尝试调 MCP Tool，拿到真实外部数据
    mcp_data = _try_mcp_tools(content)

    suggestion = _build_suggestion(category, content)
    if mcp_data:
        suggestion = f"{mcp_data}\n\n📋 标准处理建议：\n{suggestion}"

    return {
        "category": category,
        "priority": priority,
        "assignee": assignee,
        "suggestion": suggestion,
    }


# ---------------------------------------------------------------------------
# Agent 3 — QA Agent（有 LLM 用语义审核，无 LLM 降级关键词正则）
# ---------------------------------------------------------------------------
_QA_SYSTEM = (
    "你是 QA 质检，检查客服输出是否合格。不合格原因："
    "1) 字段为空；2) 含'保证''一定解决''100%'等绝对化承诺；3) 建议含糊不专业。"
    "只输出 {\"pass\": true} 或 {\"pass\": false, \"reason\": \"...\"} 的 JSON。"
)


def qa_agent(result: Dict[str, str]) -> Optional[str]:
    """质检：返回 None 表示合格，否则返回违规原因。有 LLM 用语义审核，无 LLM 降级正则"""
    # --- 优先 LLM 语义审核 ---
    if _HAS_LLM:
        required_ok = all(v and str(v).strip() for v in result.values())
        if not required_ok:
            return "存在空字段"
        text = json.dumps(result, ensure_ascii=False)
        raw = _llm_call(_QA_SYSTEM, f"客服输出：{text}")
        if raw:
            try:
                data = json.loads(raw)
                if not data.get("pass", True):
                    return data.get("reason", "输出不合格")
                return None  # LLM 说合格
            except Exception:
                pass
        # LLM 调用失败 → 降级下面的正则检查（不直接放行）

    # --- 降级：关键词正则检查 ---
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
    from mcp_server import list_tools_sync
    conn = _get_conn()
    count = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    conn.close()
    return {
        "status": "ok",
        "tickets": count,
        "db": _DB_PATH,
        "mcp_tools": list_tools_sync(),
        "llm_enabled": _HAS_LLM,
        "llm_model": LLM_MODEL if _HAS_LLM else None,
        "llm_key_len": len(LLM_API_KEY) if LLM_API_KEY else 0,  # debug: 看 Key 到底有没有注入
    }


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
