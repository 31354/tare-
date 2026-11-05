"""
mcp_server.py — 售后客服 MCP Server (MCP Python SDK v2)
注册了 2 个 Tool：
  1. query_weather(city)        — 查天气（wttr.in，免费无 key）
  2. query_express(tracking_no) — 查快递（mock 数据，模拟真实轨迹）
"""
from __future__ import annotations
import asyncio
import random
from typing import Annotated

import httpx
from mcp.server import MCPServer

mcp = MCPServer("售后客服 MCP Server")

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
# Tool 2 — 查快递（mock 数据，模拟真实轨迹）
# ---------------------------------------------------------------------------
_MOCK_TRACKING = [
    {"time": "2026-11-04 10:23", "status": "已签收", "detail": "本人已签收，感谢使用快递服务"},
    {"time": "2026-11-04 08:15", "status": "派送中", "detail": "快递员 张师傅 (138****6789) 正在派送中"},
    {"time": "2026-11-03 20:42", "status": "到达集散中心", "detail": "【北京市朝阳区集散中心】已到达，预计明日派送"},
    {"time": "2026-11-03 09:10", "status": "运输中", "detail": "已离开【上海转运中心】，下一站【北京朝阳区集散中心】"},
    {"time": "2026-11-02 18:30", "status": "已发货", "detail": "商家已发货，快递员 李师傅 已揽收"},
    {"time": "2026-11-02 14:00", "status": "已下单", "detail": "用户提交订单，等待商家发货"},
]


@mcp.tool()
def query_express(tracking_no: Annotated[str, "快递单号，例如 SF1234567890"]) -> str:
    """查询快递物流轨迹。当用户问快递到哪了、什么时候到、发货了没有时调用。"""
    if not tracking_no or len(tracking_no) < 5:
        return "请提供有效的快递单号（至少 5 位），例如 SF1234567890"

    # 根据单号生成稳定的 mock（同单号每次返回一样）
    rng = random.Random(tracking_no)
    pick = rng.choice([0, 1, 2, 3, 4, 5])
    steps = _MOCK_TRACKING[pick:]

    lines = [f"📦 快递单号：{tracking_no}", f"🚚 当前状态：{steps[0]['status']}", ""]
    for s in steps[:3]:
        lines.append(f"  [{s['time']}] {s['detail']}")
    if len(steps) > 3:
        lines.append(f"  ... 共 {len(steps)} 条轨迹")

    if steps[0]["status"] == "已签收":
        lines.append("\n✅ 已签收完毕")
    elif steps[0]["status"] in ("派送中", "到达集散中心"):
        lines.append("\n⏰ 预计今天送达，注意查收 🚪")
    elif steps[0]["status"] == "运输中":
        lines.append("\n⏰ 预计 1-2 天内送达 📦")
    else:
        lines.append("\n⏰ 预计 3-5 天内送达")

    return "\n".join(lines)


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
