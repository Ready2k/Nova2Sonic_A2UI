#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 01-deploy-infra.sh
#
# Deploys the three CloudFormation stacks and bootstraps the EKS cluster:
#   1. ECR repositories
#   2. VPC + networking
#   3. EKS cluster, node group, OIDC provider, and IAM roles
#   4. AWS Load Balancer Controller (via Helm)
#   5. IngressClass
#
# Prerequisites:
#   aws CLI     ≥ 2.x   (configured with admin-level credentials)
#   kubectl     ≥ 1.28
#   helm        ≥ 3.x
#   jq          (JSON processing)
#   envsubst    (gettext-base on Debian/Ubuntu, gettext on macOS)
#
# Usage:
#   export AWS_REGION=us-east-1
#   export CLUSTER_NAME=barclays-mortgage   # optional, default shown
#   ./scripts/01-deploy-infra.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
CLUSTER_NAME="${CLUSTER_NAME:-barclays-mortgage}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_STACK="${CLUSTER_NAME}-ecr"
VPC_STACK="${CLUSTER_NAME}-vpc"
EKS_STACK="${CLUSTER_NAME}-eks"
ALB_POLICY_NAME="AWSLoadBalancerControllerIAMPolicy-${CLUSTER_NAME}"
ALB_CHART_VERSION="1.8.3"    # aws-load-balancer-controller Helm chart

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo "[$(date +%T)] $*"; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

check_prereq() {
    for cmd in aws kubectl helm jq envsubst; do
        command -v "$cmd" &>/dev/null || die "Required tool not found: $cmd"
    done
    log "All prerequisites found."
}

cf_deploy() {
    local stack="$1" template="$2"
    shift 2
    log "Deploying CloudFormation stack: $stack"
    aws cloudformation deploy \
        --stack-name "$stack" \
        --template-file "$template" \
        --capabilities CAPABILITY_NAMED_IAM \
        --region "$AWS_REGION" \
        "$@"
    log "Stack $stack: deployed."
}

cf_output() {
    local stack="$1" key="$2"
    aws cloudformation describe-stacks \
        --stack-name "$stack" \
        --region "$AWS_REGION" \
        --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" \
        --output text
}

# ── Step 1: ECR ───────────────────────────────────────────────────────────────
log "=== Step 1: ECR repositories ==="
cf_deploy "$ECR_STACK" "$REPO_ROOT/cloudformation/01-ecr.yaml" \
    --parameter-overrides NamePrefix="$CLUSTER_NAME"

# ── Step 2: VPC ───────────────────────────────────────────────────────────────
log "=== Step 2: VPC ==="
cf_deploy "$VPC_STACK" "$REPO_ROOT/cloudformation/02-vpc.yaml" \
    --parameter-overrides ClusterName="$CLUSTER_NAME"

VPC_ID=$(cf_output "$VPC_STACK" "VpcId")
PRIVATE_SUBNETS=$(cf_output "$VPC_STACK" "PrivateSubnets")
PUBLIC_SUBNETS=$(cf_output "$VPC_STACK" "PublicSubnets")
log "VPC=$VPC_ID  private=$PRIVATE_SUBNETS  public=$PUBLIC_SUBNETS"

# ── Step 3: Compute OIDC thumbprint ──────────────────────────────────────────
# Retrieve the TLS certificate thumbprint for the EKS OIDC endpoint.
log "=== Step 3: Computing OIDC thumbprint ==="
OIDC_HOST="oidc.eks.${AWS_REGION}.amazonaws.com"
OIDC_THUMBPRINT=$(echo | openssl s_client -connect "${OIDC_HOST}:443" 2>/dev/null \
    | openssl x509 -fingerprint -sha1 -noout 2>/dev/null \
    | awk -F= '{print $2}' \
    | tr -d ':' \
    | tr '[:upper:]' '[:lower:]')

if [[ -z "$OIDC_THUMBPRINT" ]]; then
    log "WARNING: Could not compute OIDC thumbprint; using default."
    OIDC_THUMBPRINT="9e99a48a9960b14926bb7f3b02e22da2b0ab7280"
fi
log "OIDC thumbprint: $OIDC_THUMBPRINT"

# ── Step 4: EKS cluster ───────────────────────────────────────────────────────
log "=== Step 4: EKS cluster (this takes ~15 minutes) ==="
cf_deploy "$EKS_STACK" "$REPO_ROOT/cloudformation/03-eks.yaml" \
    --parameter-overrides \
        ClusterName="$CLUSTER_NAME" \
        VpcId="$VPC_ID" \
        PrivateSubnets="$PRIVATE_SUBNETS" \
        PublicSubnets="$PUBLIC_SUBNETS" \
        OIDCThumbprint="$OIDC_THUMBPRINT"

ALB_CONTROLLER_ROLE_ARN=$(cf_output "$EKS_STACK" "ALBControllerRoleArn")
BEDROCK_ROLE_ARN=$(cf_output "$EKS_STACK" "BedrockRoleArn")
log "ALB controller role: $ALB_CONTROLLER_ROLE_ARN"
log "Bedrock IRSA role:   $BEDROCK_ROLE_ARN"

# Export for downstream scripts
export BEDROCK_ROLE_ARN
export ALB_CONTROLLER_ROLE_ARN

# ── Step 5: Configure kubectl ─────────────────────────────────────────────────
log "=== Step 5: Configuring kubectl ==="
aws eks update-kubeconfig \
    --name "$CLUSTER_NAME" \
    --region "$AWS_REGION"
log "kubectl context updated."

# ── Step 6: Install AWS Load Balancer Controller via Helm ─────────────────────
log "=== Step 6: AWS Load Balancer Controller ==="

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Download the official IAM policy and create/update it in IAM
POLICY_FILE="/tmp/alb-controller-policy.json"
log "Downloading ALB controller IAM policy..."
curl -sL \
  "https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v${ALB_CHART_VERSION}/docs/install/iam_policy.json" \
  -o "$POLICY_FILE"

POLICY_ARN=$(aws iam list-policies \
    --query "Policies[?PolicyName=='${ALB_POLICY_NAME}'].Arn" \
    --output text 2>/dev/null)

if [[ -z "$POLICY_ARN" ]]; then
    log "Creating IAM policy: $ALB_POLICY_NAME"
    POLICY_ARN=$(aws iam create-policy \
        --policy-name "$ALB_POLICY_NAME" \
        --policy-document "file://$POLICY_FILE" \
        --query "Policy.Arn" \
        --output text)
else
    log "IAM policy already exists: $POLICY_ARN"
fi

# Attach the policy to the ALB controller role
aws iam attach-role-policy \
    --role-name "${CLUSTER_NAME}-alb-controller-role" \
    --policy-arn "$POLICY_ARN" 2>/dev/null || true

# Add the EKS Helm repo and install / upgrade the controller
helm repo add eks https://aws.github.io/eks-charts 2>/dev/null || true
helm repo update eks

helm upgrade --install aws-load-balancer-controller \
    eks/aws-load-balancer-controller \
    --namespace kube-system \
    --version "$ALB_CHART_VERSION" \
    --set clusterName="$CLUSTER_NAME" \
    --set serviceAccount.create=true \
    --set serviceAccount.name=aws-load-balancer-controller \
    --set "serviceAccount.annotations.eks\.amazonaws\.com/role-arn=${ALB_CONTROLLER_ROLE_ARN}" \
    --set region="$AWS_REGION" \
    --set vpcId="$VPC_ID" \
    --wait --timeout 5m

log "ALB controller installed."

# ── Step 7: Kubernetes bootstrap ─────────────────────────────────────────────
log "=== Step 7: Kubernetes namespace, IngressClass, and ServiceAccount ==="

kubectl apply -f "$REPO_ROOT/k8s/namespace.yaml"
kubectl apply -f "$REPO_ROOT/k8s/ingressclass.yaml"

# Substitute the IRSA role ARN into the service account manifest
BEDROCK_ROLE_ARN="$BEDROCK_ROLE_ARN" \
    envsubst '${BEDROCK_ROLE_ARN}' \
    < "$REPO_ROOT/k8s/serviceaccount.yaml" \
    | kubectl apply -f -

# ── Summary ───────────────────────────────────────────────────────────────────
log ""
log "══════════════════════════════════════════════════════════"
log "  Infrastructure deployment complete."
log ""
log "  ECR stack:  $ECR_STACK"
log "  VPC stack:  $VPC_STACK"
log "  EKS stack:  $EKS_STACK"
log ""
log "  Next step:  ./scripts/02-build-push.sh"
log "══════════════════════════════════════════════════════════"
