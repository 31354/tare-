"""
mcp_server.py — 售后客服 MCP Server (MCP Python SDK v2)
注册了 2 个 Tool：
  1. query_weather(city)        — 查天气（wttr.in，免费无 key）
  2. query_express(tracking_no, shipper_code) — 查快递（快递鸟真实 API，无 Key 时降级 mock）

快递鸟免费注册：https://www.kdniao.com/
注册后在 Railway Settings → Variables 里加：
  KDN_ENTERPRISE_ID=你的企业ID
  KDN_API_KEY=你的API Key
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import random
import re
from typing import Annotated, Optional

import httpx
from mcp.server import MCPServer

mcp = MCPServer("售后客服 MCP Server")

# ---------------------------------------------------------------------------
# 环境变量：快递鸟 API Key（注册 https://www.kdniao.com/ 免费拿）
# ---------------------------------------------------------------------------
KDN_ENTERPRISE_ID = os.environ.get("KDN_ENTERPRISE_ID", "")
KDN_API_KEY = os.environ.get("KDN_API_KEY", "")
KDN_API_URL = "https://api.kdniao.com/Ebusiness/EbusinessOrderHandle.aspx"
_HAS_KDN = bool(KDN_ENTERPRISE_ID and KDN_API_KEY)

# ---------------------------------------------------------------------------
# 快递公司编码识别（根据单号前缀/格式推断）
# ---------------------------------------------------------------------------
_SHIPPER_PATTERNS = [
    ("SF", re.compile(r"^SF\d{10,15}$", re.I)),          # 顺丰
    ("YTO", re.compile(r"^(YT|YTO)\d{12,15}$", re.I)),    # 圆通
    ("ZTO", re.compile(r"^\d{12,15}$")),                   # 中通（纯数字）
    ("STO", re.compile(r"^(STO|77)\d{12,15}$", re.I)),    # 申通
    ("YD", re.compile(r"^(YD|D)\d{12,15}$", re.I)),       # 韵达
    ("JD", re.compile(r"^(JD|V)\d{10,15}$", re.I)),       # 京东
    ("EMS", re.compile(r"^[A-Z]{2}\d{9}[A-Z]{2}$", re.I)),  # EMS 国际
    ("JD", re.compile(r"^(JDGA|JDX)\d{10,15}$", re.I)),   # 京东
    ("YJ", re.compile(r"^(YJ|8)\d{12,15}$", re.I)),       # 极兔
]


def _detect_shipper(tracking_no: str) -> str:
    """根据单号推断快递公司编码，推断不出默认 SF"""
    for code, pat in _SHIPPER_PATTERNS:
        if pat.match(tracking_no.strip()):
            return code
    return "SF"  # 默认顺丰（最常见）

# ---------------------------------------------------------------------------
# Tool 1 — 查天气（真实 API，wttr.in 完全免费无 key）
# ---------------------------------------------------------------------------
@mcp.tool()
def query_weather(city: Annotated[str, "城市名，例如 北京 / Shanghai / 上海"]) -> str:
    """查询指定城市的当前天气。当用户问天气、能不能出门、需不需要带伞时调用。"""
    try:
        resp = httpx.get(
            f"https://wttr.in/{city}?format=j1",
            timeout=8,
            headers={"User-Agent": "curl/7.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        cur = data["current_condition"][0]
        temp = cur["temp_C"]
        # wttr.in 的 lang_zh 有时返回空，fallback 到英文描述
        desc = cur["lang_zh"][0]["value"] if cur.get("lang_zh") else cur["weatherDesc"][0]["value"]
        humidity = cur["humidity"]
        wind = cur["windspeedKmph"]
        return f"{city}当前天气：{desc}，温度 {temp}°C，湿度 {humidity}%，风速 {wind} km/h"
    except Exception as e:
        return f"天气查询失败：{e}。请稍后再试。"


# ---------------------------------------------------------------------------
# Tool 2 — 查快递（优先快递鸟真实 API，无 Key 降级 mock）
# ---------------------------------------------------------------------------
_MOCK_TRACKING = [
    {"time": "2026-11-04 10:23", "status": "已签收", "detail": "本人已签收，感谢使用快递服务"},
    {"time": "2026-11-04 08:15", "status": "派送中", "detail": "快递员 张师傅 (138****6789) 正在派送中"},
    {"time": "2026-11-03 20:42", "status": "到达集散中心", "detail": "【北京市朝阳区集散中心】已到达，预计明日派送"},
    {"time": "2026-11-03 09:10", "status": "运输中", "detail": "已离开【上海转运中心】，下一站【北京朝阳区集散中心】"},
    {"time": "2026-11-02 18:30", "status": "已发货", "detail": "商家已发货，快递员 李师傅 已揽收"},
    {"time": "2026-11-02 14:00", "status": "已下单", "detail": "用户提交订单，等待商家发货"},
]


def _query_kdn(tracking_no: str, shipper_code: str) -> Optional[str]:
    """调快递鸟真实 API，返回格式化结果；失败返回 None"""
    try:
        request_data = json.dumps({
            "OrderCode": "",
            "ShipperCode": shipper_code,
            "LogisticCode": tracking_no,
        })
        sign_src = request_data + KDN_API_KEY
        sign = hashlib.md5(sign_src.encode("utf-8")).hexdigest()
        sign = sign.upper()

        resp = httpx.post(
            KDN_API_URL,
            data={
                "RequestData": request_data,
                "EBusinessID": KDN_ENTERPRISE_ID,
                "RequestType": "1002",       # 实时查询
                "DataSign": sign,
                "DataType": "2",              # JSON
            },
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("Success"):
            return None  # API 业务失败，降级 mock

        traces = data.get("Traces", [])
        if not traces:
            return None

        # 快递鸟返回的 Traces 是倒序（最新在前）
        latest = traces[0]
        shipper = data.get("ShipperName", shipper_code)

        lines = [f"📦 快递单号：{tracking_no}", f"🏢 快递公司：{shipper}",
                 f"🚚 当前状态：{latest.get('StateName', latest.get('AcceptStatusText', '未知'))}", ""]
        for t in traces[:5]:
            accept_time = t.get("AcceptTime", "")
            accept_station = t.get("AcceptStation", "")
            lines.append(f"  [{accept_time}] {accept_station}")
        if len(traces) > 5:
            lines.append(f"  ... 共 {len(traces)} 条轨迹")

        state_code = str(latest.get("State", ""))
        if state_code == "3" or "签收" in latest.get("AcceptStation", ""):
            lines.append("\n✅ 已签收完毕")
        elif state_code == "2":
            lines.append("\n⏰ 运输中，请耐心等待 📦")
        elif state_code == "1":
            lines.append("\n⏰ 已揽收，正在运输中")
        else:
            lines.append("")

        return "\n".join(lines)
    except Exception:
        return None


@mcp.tool()
def query_express(
    tracking_no: Annotated[str, "快递单号，例如 SF1234567890"],
    shipper_code: Annotated[Optional[str], "快递公司编码（可选，不传自动识别）：SF=顺丰 YTO=圆通 ZTO=中通 JD=京东 EMS=邮政"] = None,
) -> str:
    """查询快递物流轨迹。当用户问快递到哪了、什么时候到、发货了没有时调用。"""
    if not tracking_no or len(tracking_no.strip()) < 5:
        return "请提供有效的快递单号（至少 5 位），例如 SF1234567890"

    tracking_no = tracking_no.strip()
    shipper = shipper_code or _detect_shipper(tracking_no)

    # --- 优先快递鸟真实 API ---
    if _HAS_KDN:
        real_result = _query_kdn(tracking_no, shipper)
        if real_result:
            return f"【MCP · 快递查询（快递鸟实时）】\n{real_result}"

    # --- 降级：mock 数据 ---
    rng = random.Random(tracking_no)
    pick = rng.choice([0, 1, 2, 3, 4, 5])
    steps = _MOCK_TRACKING[pick:]

    shipper_map = {"SF": "顺丰", "YTO": "圆通", "ZTO": "中通", "JD": "京东", "EMS": "邮政"}
    shipper_name = shipper_map.get(shipper, shipper)

    lines = [
        f"📦 快递单号：{tracking_no}",
        f"🏢 快递公司：{shipper_name}",
        f"🚚 当前状态：{steps[0]['status']}",
        "",
    ]
    for s in steps[:3]:
        lines.append(f"  [{s['time']}] {s['detail']}")
    if len(steps) > 3:
        lines.append(f"  ... 共 {len(steps)} 条轨迹")

    lines.append("")
    if steps[0]["status"] == "已签收":
        lines.append("✅ 已签收完毕")
    elif steps[0]["status"] in ("派送中", "到达集散中心"):
        lines.append("⏰ 预计今天送达，注意查收 🚪")
    elif steps[0]["status"] == "运输中":
        lines.append("⏰ 预计 1-2 天内送达 📦")
    else:
        lines.append("⏰ 预计 3-5 天内送达")

    if not _HAS_KDN:
        lines.append("\n💡 提示：当前为模拟数据，配置快递鸟 API Key 后可查询真实轨迹")

    return f"【MCP · 快递查询（模拟）】\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# 供外部同步调用的 wrapper
# ---------------------------------------------------------------------------
def call_tool_sync(name: str, arguments: dict) -> str | None:
    """同步方式调用 MCP Tool（FastAPI 同步 endpoint 里用）"""
    try:
        result = asyncio.run(mcp.call_tool(name, arguments))
        if result.content:
            return result.content[0].text
        return None
    except Exception:
        return None


def list_tools_sync() -> list[str]:
    """同步方式列出所有已注册 MCP Tool 名"""
    return [t.name for t in asyncio.run(mcp.list_tools())]


# ---------------------------------------------------------------------------
# SSE 入口（独立部署用）
# ---------------------------------------------------------------------------
def create_mcp_app():
    """返回 ASGI app，可 mount 到 FastAPI"""
    return mcp.sse_app()


if __name__ == "__main__":
    asyncio.run(mcp.run_sse_async(host="127.0.0.1", port=8765))
