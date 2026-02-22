with open("/Users/jamescregeen/A2UI_S2S/server/app/main.py", "r") as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if "try:" in line and "res = await asyncio.to_thread(app_graph.invoke, state)" in lines[i+1]:
        print(f"Graph invocation found around line {i+1}")
