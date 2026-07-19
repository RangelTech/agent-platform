#!/usr/bin/env bash
# Deploy agent-platform to Cloud Run (project eduk-prd-lake).
# Usage: ./infra/deploy.sh [kernel|backend|all]
set -euo pipefail

PROJECT=eduk-prd-lake
REGION=us-central1
REPO=us-central1-docker.pkg.dev/$PROJECT/cloud-run-source-deploy
SQL_CONNECTION=eduk-prd:us-central1:shared-eduk
RUNTIME_SA=devlake@eduk-prd-lake.iam.gserviceaccount.com
SHORT_SHA=$(git rev-parse --short HEAD)

target=${1:-all}
cd "$(dirname "$0")/.."

build() {
  local service=$1
  gcloud builds submit --project=$PROJECT \
    --config=infra/cloudbuild-$service.yaml \
    --substitutions=SHORT_SHA=$SHORT_SHA .
}

deploy_kernel() {
  build kernel
  gcloud run deploy teste-ia-kernel \
    --project=$PROJECT --region=$REGION \
    --image=$REPO/teste_ia-kernel:$SHORT_SHA \
    --service-account=$RUNTIME_SA \
    --add-cloudsql-instances=$SQL_CONNECTION \
    --set-secrets=DATABASE_URL=teste-ia-database-url:latest \
    --set-env-vars="ENABLE_STUB_CONTROL=false,GCS_BUCKET=eduk-prd-lake,GCS_PREFIX=agent llm" \
    --no-allow-unauthenticated \
    --memory=1Gi --cpu=1 --min-instances=0 --max-instances=3 \
    --timeout=600
  # Only the backend may invoke the kernel.
  gcloud run services add-iam-policy-binding teste-ia-kernel \
    --project=$PROJECT --region=$REGION \
    --member=serviceAccount:$RUNTIME_SA --role=roles/run.invoker
}

deploy_backend() {
  build backend
  local kernel_url
  kernel_url=$(gcloud run services describe teste-ia-kernel \
    --project=$PROJECT --region=$REGION --format='value(status.url)')
  gcloud run deploy teste-ia-backend \
    --project=$PROJECT --region=$REGION \
    --image=$REPO/teste_ia-backend:$SHORT_SHA \
    --service-account=$RUNTIME_SA \
    --add-cloudsql-instances=$SQL_CONNECTION \
    --set-secrets=DATABASE_URL=teste-ia-database-url:latest,ENCRYPTION_KEY=teste-ia-encryption-key:latest \
    --set-env-vars=KERNEL_URL=$kernel_url,KERNEL_AUDIENCE=$kernel_url \
    --allow-unauthenticated \
    --memory=512Mi --cpu=1 --min-instances=0 --max-instances=5 \
    --timeout=600
}

case $target in
  kernel) deploy_kernel ;;
  backend) deploy_backend ;;
  all) deploy_kernel; deploy_backend ;;
  *) echo "usage: $0 [kernel|backend|all]" >&2; exit 1 ;;
esac
