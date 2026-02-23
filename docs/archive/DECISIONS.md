# Decisions Log

- **Transport**: Standard WebSockets using FastAPI (`websockets` standard lib) and Next.js native `WebSocket` API.
- **Language**: Python 3.10+ for Backend, TypeScript for Frontend.
- **Agent Orchestration**: LangGraph for state machine, ensuring single-session streaming to UI.
- **Tools Mocking**: Implemented deterministic mock tools since Nova 2 Sonic is not provided.
- **Styling**: TailwindCSS for the client to quickly build standard interactive components (gauge, slider, cards).
- **A2UI Protocol Compliance**: We are fully compliant with the official Google A2UI v0.9 Server-Driven JSON Schema. The backend generates standard `updateComponents` ASTs, and the frontend implements a generic recursive renderer to parse and display component trees (Column, Row, Text, Gauge, ProductCard) dynamically.
