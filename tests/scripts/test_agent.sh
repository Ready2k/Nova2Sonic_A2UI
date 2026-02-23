#!/bin/bash

# Default model (Haiku)
MODEL_ID="anthropic.claude-3-haiku-20240307-v1:0"

# Check for --nova flag
if [[ "$1" == "--nova" ]]; then
    MODEL_ID="amazon.nova-lite-v1:0"
    echo "--- Switching to Amazon Nova Lite ---"
else
    echo "--- Using Claude 3 Haiku (Baseline) ---"
fi

# Export for the Python script
export AGENT_MODEL_ID="$MODEL_ID"
export TEST_MODEL_ID="$MODEL_ID"

# Running the test script using the server's virtual environment
server/.venv/bin/python test_agent_workflow.py
