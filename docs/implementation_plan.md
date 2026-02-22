# Migration to Fully Compliant Google A2UI Specification

## Goal Description
The user has decided to reverse the previous decision and wants to make the application fully compliant with the official Google A2UI SDK and A2A Protocol Specification (v0.9). We will rip out our custom domain-specific payload (`{"intent": {"ltv": 62.5}}`) and replace it with a true Server-Driven UI (SDUI) architecture where the backend explicitly dictates the UI tree layout using standard catalog components (e.g., `Row`, `Column`, `Text`, `Gauge`).

## User Review Required

> [!WARNING]
> This is a significant architectural shift. Our React frontend will go from being a smart "Mortgage View" that knows how to lay out data, to a "dumb framework" that just executes layout commands from the server.

Are you happy with the proposed structure below?

## Proposed Changes

### 1. Server-Side Schema Generation (FastAPI / LangGraph)
The backend must stop sending raw domain data and start sending UI manifests.

#### [MODIFY] server/app/graph.py
- Update the `build_a2ui_payload` function (or equivalent state mapping logic).
- Instead of returning a raw `dict`, it must construct a fully compliant A2UI v0.9 `updateComponents` payload.
- Example structure to generate:
  ```json
  {
    "version": "v0.9",
    "updateComponents": {
      "surfaceId": "main",
      "components": [
        {
          "id": "root",
          "component": "Column",
          "children": ["header_text", "ltv_gauge"]
        },
        {
          "id": "header_text",
          "component": "Text",
          "text": "Your Comparative Analysis",
          "variant": "h2"
        },
        {
          "id": "ltv_gauge",
          "component": "Gauge", 
          "value": 62.5,
          "max": 100
        }
      ]
    }
  }
  ```

#### [MODIFY] server/app/main.py
- Ensure the WebSocket emitter handles the initial `createSurface` message when the connection starts or the A2UI pane first opens.
- Ensure the `server.a2ui.patch` event type aligns with the `updateComponents`/`updateDataModel` standard.

### 2. Client-Side Generic Renderer (React / Next.js)
The frontend must become a recursive component factory.

#### [NEW] client/src/components/A2UIRenderer.tsx
- Create a recursive React component that takes an A2UI payload and maps the `"component"` string to actual React/Tailwind elements.
- Example: 
  - `if (node.component === "Row") return <div className="flex flex-row">{children}</div>;`
  - `if (node.component === "Text") return <span>{node.text}</span>;`
  - `if (node.component === "Gauge") return <LtvGauge value={node.value} />;`

#### [MODIFY] client/src/hooks/useMortgageSocket.ts
- Update the state handler for `server.a2ui.patch` to expect and store the complex `updateComponents` array instead of the simple `data` object.

#### [MODIFY] client/src/app/page.tsx
- Replace the current hardcoded `DiscoveryCanvas` layout (which manually mounts `LtvGauge` and `ProductCard`s) with the new `<A2UIRenderer components={a2uiState.components} />`. Let the backend control exactly where the cards and gauges go.

## Verification Plan

### Manual Verification
1. I will boot up the backend API and frontend React app.
2. I will send a mocked test message down the WebSocket to simulate an A2UI `updateComponents` payload with nested `Column`, `Row`, and `Text` nodes.
3. I will visually verify that the generic React renderer perfectly reconstructs the AST layout tree into DOM elements without knowing what a "mortgage" is.
