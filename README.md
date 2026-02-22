# Barclays Mortgage Assistant Demo

This repo contains a voice-first, A2UI-driven mortgage assistant demonstration. It features a Next.js client and a Python FastAPI/LangGraph backend connected via WebSockets.

## Prerequisites
- Node.js 18+
- Python 3.10+

## Running the Server
```bash
cd server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```
The server will start on `ws://localhost:8000/ws`.

## Running the Client
```bash
cd client
npm install
npm run dev
```
The client will start on `http://localhost:3000`.

## Demo Script
1. Open `http://localhost:3000`.
2. Toggle **Voice Mode** on.
3. Hold to talk: "My house is worth four hundred thousand and I owe two hundred and fifty thousand. I want a five year fix."
4. Verify the UI generates the Mortgage Comparison.
5. Move the **Term Slider** to see real-time payment updates.
6. Select a Product and confirm.
