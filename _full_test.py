"""全量线上测试"""
import httpx, json, sys

BASE = "https://skillful-miracle-production-49a0.up.railway.app"
PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ PASS  {name}")
    else:
        FAIL += 1
        print(f"  ❌ FAIL  {name}  {detail}")

print(f"目标：{BASE}")
print()

# 1. 首页
print("=== 1. 首页加载 ===")
try:
    r = httpx.get(f"{BASE}/", timeout=10)
    check("状态码 200", r.status_code == 200)
    check("包含标题", "智能客服工单分诊" in r.text)
except Exception as e:
    check("首页可访问", False, str(e))

# 2. health 端点
print("\n=== 2. /health ===")
try:
    r = httpx.get(f"{BASE}/health", timeout=10)
    d = r.json()
    check("status=ok", d.get("status") == "ok")
    check("mcp_tools 数组", "mcp_tools" in d)
    check("含 query_weather", "query_weather" in d.get("mcp_tools", []))
    check("含 query_express", "query_express" in d.get("mcp_tools", []))
    check("db 路径非空", bool(d.get("db")))
except Exception as e:
    check("health 正常", False, str(e))

# 3. 发票分诊（核心验收标准 AC3）
print("\n=== 3. 发票分诊（AC3 核心用例）===")
try:
    r = httpx.post(f"{BASE}/triage", json={"content": "我的发票抬头写错了"}, timeout=15)
    d = r.json()
    check("HTTP 200", r.status_code == 200)
    check("category=账单", d.get("category") == "账单", f"实际={d.get('category')}")
    check("priority=中", d.get("priority") == "中", f"实际={d.get('priority')}")
    check("suggestion 非空", bool(d.get("suggestion", "").strip()))
    check("assignee 非空", bool(d.get("assignee", "").strip()))
    check("trace 非空", len(d.get("trace", [])) >= 3)
    print(f"  📋 返回: category={d.get('category')}, priority={d.get('priority')}")
except Exception as e:
    check("发票分诊正常", False, str(e))

# 4. 空字符串（AC4）
print("\n=== 4. 空字符串（AC4 400）===")
try:
    r = httpx.post(f"{BASE}/triage", json={"content": ""}, timeout=10)
    check("HTTP 400", r.status_code == 400, f"实际={r.status_code}")
except Exception as e:
    check("空串返回 400", False, str(e))

# 5. MCP 天气
print("\n=== 5. MCP 天气 ===")
try:
    r = httpx.post(f"{BASE}/triage", json={"content": "上海今天天气怎么样"}, timeout=20)
    d = r.json()
    check("HTTP 200", r.status_code == 200)
    check("含天气查询标签", "天气查询" in d.get("suggestion", ""))
    check("含温度数据", "°C" in d.get("suggestion", "") or "温度" in d.get("suggestion", ""))
    check("suggestion 非空", bool(d.get("suggestion", "").strip()))
except Exception as e:
    check("MCP 天气正常", False, str(e))

# 6. MCP 快递
print("\n=== 6. MCP 快递 ===")
try:
    r = httpx.post(f"{BASE}/triage", json={"content": "快递SF1234567890到哪了"}, timeout=15)
    d = r.json()
    check("HTTP 200", r.status_code == 200)
    check("含快递查询标签", "快递查询" in d.get("suggestion", ""))
    check("含模拟/实时提示", "模拟" in d.get("suggestion", "") or "快递鸟" in d.get("suggestion", ""))
except Exception as e:
    check("MCP 快递正常", False, str(e))

# 7. 技术类工单
print("\n=== 7. 技术类工单 ===")
try:
    r = httpx.post(f"{BASE}/triage", json={"content": "app 打不开，一直闪退"}, timeout=15)
    d = r.json()
    check("category=技术", d.get("category") == "技术", f"实际={d.get('category')}")
except Exception as e:
    check("技术分类正常", False, str(e))

# 8. 售后类工单
print("\n=== 8. 售后类工单 ===")
try:
    r = httpx.post(f"{BASE}/triage", json={"content": "收到的商品破损了要退货"}, timeout=15)
    d = r.json()
    check("category=售后", d.get("category") == "售后", f"实际={d.get('category')}")
except Exception as e:
    check("售后分类正常", False, str(e))

# 9. 通过 ticket_id 查历史
print("\n=== 9. 历史工单查询 ===")
try:
    r = httpx.post(f"{BASE}/triage", json={"content": "退款怎么还没到账"}, timeout=15)
    tid = r.json().get("ticket_id")
    r2 = httpx.get(f"{BASE}/tickets/{tid}", timeout=10)
    check("查历史 200", r2.status_code == 200, f"实际={r2.status_code}")
    check("ticket_id 匹配", r2.json().get("ticket_id") == tid)
except Exception as e:
    check("历史查询正常", False, str(e))

# 10. 不可识别输入（兜底到售后）
print("\n=== 10. 兜底分类 ===")
try:
    r = httpx.post(f"{BASE}/triage", json={"content": "我想问问关于周末的事"}, timeout=15)
    d = r.json()
    check("能返回结果", r.status_code == 200 and bool(d.get("category")))
except Exception as e:
    check("兜底正常", False, str(e))

print()
print("=" * 50)
print(f"📊 总计：{PASS} PASS / {FAIL} FAIL")
if FAIL == 0:
    print("🎉 全部通过！线上地址可以放心发给朋友测试了")
else:
    print(f"⚠️ 有 {FAIL} 项未通过，请检查")
