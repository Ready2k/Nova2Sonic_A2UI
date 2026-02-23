# Multi-stage Next.js build using standalone output mode.
# NEXT_PUBLIC_WS_URL is baked in at build time (Next.js client-side env var).
#
# Build:  docker build \
#           --build-arg NEXT_PUBLIC_WS_URL=wss://your-alb.elb.amazonaws.com/ws \
#           -t mortgage-client ./client
# Run:    docker run -p 3000:3000 mortgage-client

# ── Stage 1: install dependencies ─────────────────────────────────────────────
FROM node:18-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ── Stage 2: build ────────────────────────────────────────────────────────────
FROM node:18-alpine AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# WebSocket URL is baked into the Next.js bundle at build time.
# Default points to localhost for local docker-compose use.
ARG NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
ENV NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL

RUN npm run build

# ── Stage 3: production runner ────────────────────────────────────────────────
FROM node:18-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production

# Copy the standalone server and its required static assets
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000

# Hostname must be 0.0.0.0 to be reachable inside Kubernetes
ENV HOSTNAME=0.0.0.0

CMD ["node", "server.js"]
