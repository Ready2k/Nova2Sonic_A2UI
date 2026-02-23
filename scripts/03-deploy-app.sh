#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 03-deploy-app.sh
#
# Deploys the application to EKS and handles the two-pass client build
# required because NEXT_PUBLIC_WS_URL must be baked in at image build time.
#
# Pass 1: Deploy server + ingress → ALB DNS is provisioned by AWS (~5-10 min)
# Pass 2: Build client with the real ALB DNS, push, deploy client
#
# Usage:
#   export AWS_REGION=us-east-1
#   export CLUSTER_NAME=barclays-mortgage
#   export IMAGE_TAG=latest          # must match what was used in 02-build-push.sh
#   ./scripts/03-deploy-app.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-barclays-mortgage}"
AWS_REGION="${AWS_REGION:-us-east-1}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
NAMESPACE="barclays-mortgage"
ECR_STACK="${CLUSTER_NAME}-ecr"
EKS_STACK="${CLUSTER_NAME}-eks"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

log()  { echo "[$(date +%T)] $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

# ── Load image URIs ───────────────────────────────────────────────────────────
if [[ -f "$REPO_ROOT/.image-env" ]]; then
    # shellcheck disable=SC1090
    source "$REPO_ROOT/.image-env"
else
    log "No .image-env found — deriving image URIs from ECR stack outputs..."
    SERVER_REPO=$(aws cloudformation describe-stacks \
        --stack-name "$ECR_STACK" --region "$AWS_REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='ServerRepositoryUri'].OutputValue" \
        --output text)
    CLIENT_REPO=$(aws cloudformation describe-stacks \
        --stack-name "$ECR_STACK" --region "$AWS_REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='ClientRepositoryUri'].OutputValue" \
        --output text)
    SERVER_IMAGE="${SERVER_REPO}:${IMAGE_TAG}"
    CLIENT_IMAGE="${CLIENT_REPO}:${IMAGE_TAG}"
fi

BEDROCK_ROLE_ARN=$(aws cloudformation describe-stacks \
    --stack-name "$EKS_STACK" --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='BedrockRoleArn'].OutputValue" \
    --output text)

log "Server image:      $SERVER_IMAGE"
log "Client image:      $CLIENT_IMAGE"
log "Bedrock role ARN:  $BEDROCK_ROLE_ARN"

# ── Ensure kubectl context is correct ────────────────────────────────────────
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION" --quiet

# ── Pass 1: namespace, service account, server, and ingress ──────────────────
log ""
log "=== Pass 1: Deploying server and ingress ==="

kubectl apply -f "$REPO_ROOT/k8s/namespace.yaml"
kubectl apply -f "$REPO_ROOT/k8s/ingressclass.yaml"

BEDROCK_ROLE_ARN="$BEDROCK_ROLE_ARN" \
    envsubst '${BEDROCK_ROLE_ARN}' \
    < "$REPO_ROOT/k8s/serviceaccount.yaml" \
    | kubectl apply -f -

SERVER_IMAGE="$SERVER_IMAGE" CLIENT_IMAGE="$CLIENT_IMAGE" \
    envsubst '${SERVER_IMAGE}' \
    < "$REPO_ROOT/k8s/server-deployment.yaml" \
    | kubectl apply -f -

kubectl apply -f "$REPO_ROOT/k8s/server-service.yaml"
kubectl apply -f "$REPO_ROOT/k8s/ingress.yaml"

log "Waiting for server rollout..."
kubectl rollout status deployment/server -n "$NAMESPACE" --timeout=120s

# ── Wait for ALB DNS ──────────────────────────────────────────────────────────
log ""
log "=== Waiting for ALB DNS to be provisioned (up to 15 minutes) ==="
ALB_DNS=""
for i in $(seq 1 90); do
    ALB_DNS=$(kubectl get ingress mortgage-ingress \
        -n "$NAMESPACE" \
        -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")
    if [[ -n "$ALB_DNS" ]]; then
        log "ALB DNS: $ALB_DNS"
        break
    fi
    log "  Attempt $i/90 — ALB not ready yet, waiting 10s..."
    sleep 10
done

[[ -z "$ALB_DNS" ]] && die "ALB DNS was not assigned after 15 minutes. Check the ALB controller logs."

WS_URL="ws://${ALB_DNS}/ws"
log "WebSocket URL: $WS_URL"

# ── Pass 2: rebuild and push client with the real WS URL ─────────────────────
log ""
log "=== Pass 2: Building client image with WS_URL=$WS_URL ==="

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws ecr get-login-password --region "$AWS_REGION" \
    | docker login --username AWS --password-stdin \
        "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

CLIENT_REPO="${CLIENT_IMAGE%:*}"   # strip the tag
docker build \
    --file "$REPO_ROOT/client/Dockerfile" \
    --build-arg "NEXT_PUBLIC_WS_URL=${WS_URL}" \
    --tag "$CLIENT_IMAGE" \
    "$REPO_ROOT/client"

docker push "$CLIENT_IMAGE"
log "Client image pushed: $CLIENT_IMAGE"

# ── Deploy client ─────────────────────────────────────────────────────────────
log ""
log "=== Deploying client ==="

SERVER_IMAGE="$SERVER_IMAGE" CLIENT_IMAGE="$CLIENT_IMAGE" \
    envsubst '${CLIENT_IMAGE}' \
    < "$REPO_ROOT/k8s/client-deployment.yaml" \
    | kubectl apply -f -

kubectl apply -f "$REPO_ROOT/k8s/client-service.yaml"

log "Waiting for client rollout..."
kubectl rollout status deployment/client -n "$NAMESPACE" --timeout=120s

# ── Summary ───────────────────────────────────────────────────────────────────
log ""
log "══════════════════════════════════════════════════════════"
log "  Deployment complete!"
log ""
log "  Application URL:   http://${ALB_DNS}"
log "  WebSocket URL:     ${WS_URL}"
log ""
log "  Check pod status:  kubectl get pods -n $NAMESPACE"
log "  View server logs:  kubectl logs -n $NAMESPACE -l app=server -f"
log "  View client logs:  kubectl logs -n $NAMESPACE -l app=client -f"
log "══════════════════════════════════════════════════════════"
