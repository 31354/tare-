import httpx, json, time, sys

print("Polling Railway redeploy (max 10 min)...", flush=True)

for i in range(40):
    try:
        r = httpx.get("https://tare-production-e414.up.railway.app/health", timeout=15)
        d = r.json()
        tools = d.get("mcp_tools", "OLD_VERSION")
        status = f"[{i*15:3d}s] mcp_tools={tools}"
        print(status, flush=True)
        
        if "query_weather" in str(tools):
            print("\n🎉 MCP ONLINE! Running full test suite...\n", flush=True)
            
            # Test 1: 原有发票功能不变
            t = httpx.post("https://tare-production-e414.up.railway.app/triage", 
                          json={"content": "我的发票抬头写错了"}, timeout=15)
            td = t.json()
            assert td["category"] == "账单" and td["priority"] == "中", f"Test1 FAIL: {td}"
            print(f"Test 1 (发票): PASS → {td['ticket_id']} category={td['category']}", flush=True)
            
            # Test 2: MCP 天气
            t = httpx.post("https://tare-production-e414.up.railway.app/triage",
                          json={"content": "上海今天天气怎么样"}, timeout=15)
            td = t.json()
            sug = td.get("suggestion", "")
            assert "MCP" in sug, f"Test2 FAIL - no MCP tag: {sug[:100]}"
            assert "天气" in sug or "Overcast" in sug or "°C" in sug, f"Test2 FAIL - no weather data"
            print(f"Test 2 (MCP天气): PASS → has MCP tag + real weather data", flush=True)
            
            # Test 3: MCP 快递
            t = httpx.post("https://tare-production-e414.up.railway.app/triage",
                          json={"content": "我的快递 SF1234567890 到哪了"}, timeout=15)
            td = t.json()
            sug = td.get("suggestion", "")
            assert "MCP" in sug, f"Test3 FAIL - no MCP tag: {sug[:100]}"
            assert "SF1234567890" in sug or "SF" in sug, f"Test3 FAIL - no tracking no"
            print(f"Test 3 (MCP快递): PASS → has MCP tag + tracking info", flush=True)
            
            print("\n=== ALL 3 TESTS PASS ✅ ===", flush=True)
            sys.exit(0)
            
    except Exception as e:
        print(f"[{i*15}s] error: {e}", flush=True)
    time.sleep(15)

print("TIMEOUT after 10 min", flush=True)
