# Simple Q&A Agent

A minimal LangGraph agent for testing the A2UI import wizard.

This agent receives a user question via `messages` (a `List[BaseMessage]`) and
returns a plain-text answer in the same list.  It uses the standard
`messages`-based state so the **Thin Wrapper** strategy works out of the box.

## How to import

1. Start the A2UI server and client (`./manage.sh start`)
2. Open `http://localhost:3000/transfer`
3. Paste the absolute path to this directory, e.g.:
   `/path/to/A2UI_S2S/test-agents/simple_qa`
4. Set plugin ID: `simple_qa`
5. Keep strategy as **Thin Wrapper**
6. Click **Analyse & Preview**

After installing, restart the server and open:
`http://localhost:3000/?agent=simple_qa`
