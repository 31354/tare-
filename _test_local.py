"""本地完整验证脚本"""
import threading, time, httpx, sys
import uvicorn

PORT = 8767

def run():
    try:
        uvicorn.run("main:app", host="127.0.0.1", port=PORT, log_level="info")
    except Exception as e:
        print(f"SERVER ERROR: {e}", file=sys.stderr)

t = threading.Thread(target=run, daemon=True)
t.start()
time.sleep(3)

print("=== /health ===")
try:
    r = httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=5)
    print(r.status_code, r.json())
except Exception as e:
    print(f"health failed: {e}")

print("\n=== MCP 天气测试 ===")
try:
    r = httpx.post(f"http://127.0.0.1:{PORT}/triage", json={"content": "上海今天天气怎么样"}, timeout=20)
    d = r.json()
    print(f"ticket_id: {d['ticket_id']}")
    print(f"category: {d['category']}")
    print(f"suggestion:\n{d['suggestion']}")
except Exception as e:
    print(f"weather triage failed: {e}")

print("\n=== 发票测试 ===")
try:
    r = httpx.post(f"http://127.0.0.1:{PORT}/triage", json={"content": "我的发票抬头写错了"}, timeout=10)
    d = r.json()
    print(f"category: {d['category']}, priority: {d['priority']}")
    print(f"suggestion:\n{d['suggestion']}")
except Exception as e:
    print(f"invoice triage failed: {e}")
