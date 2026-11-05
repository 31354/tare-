"""线上 LLM 效果测试"""
import httpx, time

BASE = "https://skillful-miracle-production-49a0.up.railway.app"

print("等待容器启动...")
for i in range(6):
    try:
        r = httpx.get(f"{BASE}/health", timeout=10)
        d = r.json()
        llm_on = d.get("llm_enabled")
        print(f"  [{i+1}] health OK, llm_enabled={llm_on}")
        if llm_on:
            break
    except Exception as e:
        print(f"  [{i+1}] 还在启动... {e}")
    time.sleep(15)

print("\n=== Health 最终状态 ===")
r = httpx.get(f"{BASE}/health", timeout=10)
d = r.json()
print(f"status={d['status']}")
print(f"llm_enabled={d['llm_enabled']}")
print(f"llm_model={d['llm_model']}")
print(f"mcp_tools={d['mcp_tools']}")

if d.get("llm_enabled") is True:
    print("\n🎉 LLM 在线上启用成功！开始测试效果...")
    
    print("\n=== Router 同义词测试 ===")
    tests = [
        ("我那个退费怎么还没到", "账单"),
        ("app 打不开一直闪退", "技术"),
        ("我想退货换个颜色", "售后"),
    ]
    for content, expected in tests:
        r = httpx.post(f"{BASE}/triage", json={"content": content}, timeout=20)
        cat = r.json().get("category", "?")
        ok = "✅" if cat == expected else "❌"
        print(f'{ok}  "{content}" → {cat}  (期望: {expected})')

    print("\n=== Specialist 自然话术 ===")
    r = httpx.post(f"{BASE}/triage", json={"content": "我的发票抬头写错了"}, timeout=20)
    d = r.json()
    is_llm = "AI 生成" in d["suggestion"] or "💡" in d["suggestion"]
    print(f"  category={d['category']}, priority={d['priority']}")
    print(f"  suggestion={d['suggestion']}")
    print(f"  → LLM 生成: {'✅ YES' if is_llm else '❌ NO（降级模板）'}")

    print("\n=== MCP 天气（不变）===")
    r = httpx.post(f"{BASE}/triage", json={"content": "上海今天天气怎么样"}, timeout=20)
    print(f"  → {'✅ 正常' if '天气查询' in r.json()['suggestion'] else '❌ 异常'}")

    print("\n=== 空字符串 400（不变）===")
    r = httpx.post(f"{BASE}/triage", json={"content": ""}, timeout=10)
    print(f"  → HTTP {r.status_code}: {'✅' if r.status_code == 400 else '❌'}")
else:
    print("\n⚠️ LLM 仍然未启用... 检查 Railway Variables 和容器日志")
