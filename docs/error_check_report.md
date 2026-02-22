# Code Error Check Report

Date: 2026-02-22

## Command run

```bash
pytest -q
```

## Result

Pytest failed during test collection with 7 errors.

## Errors found

1. **Missing dependency**: `boto3` not installed.
   - `server/test_aws.py`
   - `test_nova_sonic.py`
   - `test_nova_sonic_sync.py`

2. **Missing dependency**: `python-dotenv` (`dotenv`) not installed.
   - `server/test_fastapi.py` (importing `server/app/main.py`)

3. **Runtime environment dependency**: WebSocket server not running on `localhost:8000`.
   - `server/test_ws.py`

4. **Syntax error in test file**: `await` used outside async function.
   - `test_agent_workflow.py` line 102

5. **Invalid hard-coded local path** causes `FileNotFoundError`.
   - `test_main.py` references `/Users/jamescregeen/A2UI_S2S/server/app/main.py`
