#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 02-build-push.sh
#
# Builds and pushes Docker images for the server and client to ECR.
#
# The client image has NEXT_PUBLIC_WS_URL baked in at build time.
# If WS_URL is not supplied the script derives it from the ALB DNS name
# provisioned by the ingress (see 03-deploy-app.sh for the full flow).
#
# Usage:
#   export AWS_REGION=us-east-1
#   export CLUSTER_NAME=barclays-mortgage
#
#   # Supply the WebSocket URL explicitly:
#   WS_URL=wss://k8s-barclays-mortgage-xxxxx.us-east-1.elb.amazonaws.com/ws \
#     ./scripts/02-build-push.sh
#
#   # Or let the script look it up from the live ingress:
#   ./scripts/02-build-push.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-barclays-mortgage}"
AWS_REGION="${AWS_REGION:-us-east-1}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
ECR_STACK="${CLUSTER_NAME}-ecr"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

log()  { echo "[$(date +%T)] $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

# ── Resolve ECR URIs ──────────────────────────────────────────────────────────
log "Fetching ECR repository URIs from CloudFormation..."
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

SERVER_REPO=$(aws cloudformation describe-stacks \
    --stack-name "$ECR_STACK" --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ServerRepositoryUri'].OutputValue" \
    --output text)

CLIENT_REPO=$(aws cloudformation describe-stacks \
    --stack-name "$ECR_STACK" --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ClientRepositoryUri'].OutputValue" \
    --output text)

[[ -z "$SERVER_REPO" ]] && die "Could not find ServerRepositoryUri in stack $ECR_STACK"
[[ -z "$CLIENT_REPO" ]] && die "Could not find ClientRepositoryUri in stack $ECR_STACK"

SERVER_IMAGE="${SERVER_REPO}:${IMAGE_TAG}"
CLIENT_IMAGE="${CLIENT_REPO}:${IMAGE_TAG}"

log "Server image: $SERVER_IMAGE"
log "Client image: $CLIENT_IMAGE"

# ── ECR login ─────────────────────────────────────────────────────────────────
log "Authenticating Docker with ECR..."
aws ecr get-login-password --region "$AWS_REGION" \
    | docker login --username AWS --password-stdin \
        "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# ── Resolve WebSocket URL ─────────────────────────────────────────────────────
if [[ -n "${WS_URL:-}" ]]; then
    log "Using supplied WS_URL: $WS_URL"
else
    log "WS_URL not set — looking up ALB DNS from live ingress..."
    ALB_DNS=$(kubectl get ingress mortgage-ingress \
        -n barclays-mortgage \
        -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")

    if [[ -z "$ALB_DNS" ]]; then
        log "WARNING: Ingress not yet provisioned. Building client with placeholder WS URL."
        log "         Run 03-deploy-app.sh after the ingress is ready, then re-run this script."
        WS_URL="ws://PLACEHOLDER/ws"
    else
        WS_URL="ws://${ALB_DNS}/ws"
        log "Resolved WS_URL: $WS_URL"
    fi
fi

# ── Build and push: server ────────────────────────────────────────────────────
log "=== Building server image ==="
docker build \
    --file "$REPO_ROOT/server/Dockerfile" \
    --tag "$SERVER_IMAGE" \
    "$REPO_ROOT"

log "Pushing server image..."
docker push "$SERVER_IMAGE"
log "Server image pushed: $SERVER_IMAGE"

# ── Build and push: client ────────────────────────────────────────────────────
log "=== Building client image (WS_URL=$WS_URL) ==="
docker build \
    --file "$REPO_ROOT/client/Dockerfile" \
    --build-arg "NEXT_PUBLIC_WS_URL=${WS_URL}" \
    --tag "$CLIENT_IMAGE" \
    "$REPO_ROOT/client"

log "Pushing client image..."
docker push "$CLIENT_IMAGE"
log "Client image pushed: $CLIENT_IMAGE"

# ── Export for downstream scripts ─────────────────────────────────────────────
echo "SERVER_IMAGE=$SERVER_IMAGE" > "$REPO_ROOT/.image-env"
echo "CLIENT_IMAGE=$CLIENT_IMAGE" >> "$REPO_ROOT/.image-env"

log ""
log "══════════════════════════════════════════════════════════"
log "  Images built and pushed."
log "  SERVER_IMAGE=$SERVER_IMAGE"
log "  CLIENT_IMAGE=$CLIENT_IMAGE"
log ""
log "  Next step:  ./scripts/03-deploy-app.sh"
log "══════════════════════════════════════════════════════════"
