with open("/Users/jamescregeen/A2UI_S2S/server/app/agent/graph.py", "r") as f:
    text = f.read()

# I will use Python to safely do this since multi_replace is sometimes tricky with large blocks and indents.
# Actually, I can use multi_replace. Let me view the exact lines for render_missing_inputs.
