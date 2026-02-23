# Build context must be the repository root so that the icon asset files
# (btl_b64.txt, ftb_b64.txt, etc.) can be copied in.
#
# Build:   docker build -f server/Dockerfile -t mortgage-server .
# Run:     docker run -p 8000:8000 --env-file .env mortgage-server

FROM python:3.12-slim

# ── System dependencies ────────────────────────────────────────────────────────
# Install Node.js 20 (required for nova_sonic_stt.mjs and nova_sonic_tts.mjs)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python dependencies ────────────────────────────────────────────────────────
COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# ── Node dependencies (STT / TTS scripts) ─────────────────────────────────────
COPY server/package.json server/package-lock.json ./
RUN npm ci --omit=dev

# ── Application code ──────────────────────────────────────────────────────────
COPY server/ ./

# ── Icon assets (base64-encoded images served via A2UI) ───────────────────────
COPY btl_b64.txt ftb_b64.txt remortgage_b64.txt moving_b64.txt /assets/

# ── Runtime configuration ─────────────────────────────────────────────────────
ENV ASSETS_DIR=/assets
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
