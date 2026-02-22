from pathlib import Path


def test_graph_invocation_exists_in_main():
    main_path = Path(__file__).resolve().parent / "server" / "app" / "main.py"
    lines = main_path.read_text().splitlines()

    found = False
    for i, line in enumerate(lines[:-1]):
        if "try:" in line and "res = await asyncio.to_thread(app_graph.invoke, state)" in lines[i + 1]:
            found = True
            break

    assert found, "Expected graph invocation pattern not found in server/app/main.py"
