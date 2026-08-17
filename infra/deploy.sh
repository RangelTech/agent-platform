#!/usr/bin/env bash
# Deploy the agent-platform KERNEL to Cloud Run (project eduk-prd-lake).
#
# Backend + frontend no longer deploy from here (agent-llm mega spec,
# infra-04): they moved to the VPS (rangeltech.net) via
# vps_rt_infra/compose/docker-compose.yml + the agent-platform prod compose
# (infra/docker-compose.prod.yml), applied by the vps_rt_infra Terraform/CI
# pipeline, not by this script.
#
# The kernel stays here on purpose (infra-01): it's the one piece that keeps
# using GPU/model-adjacent Cloud Run autoscaling. It used to be private
# (--no-allow-unauthenticated, IAM invoker restricted to the backend's own
# service account) because the backend called it from inside the same GCP
# project. Now the backend calls it from the VPS, outside GCP, with no
# metadata server to mint an OIDC token — so the kernel is now public
# (--allow-unauthenticated) and protected instead by a shared secret
# (INTERNAL_TOKEN / KERNEL_INTERNAL_TOKEN, see kernel/app/runs.py
# require_internal_auth and backend/app/config.py). This is a deliberate
# trade: simpler than wiring a GCP service account key onto the VPS, at the
# cost of losing IAM-level access control on this endpoint.
#
# Usage: ./infra/deploy.sh kernel
set -euo pipefail

PROJECT=eduk-prd-lake
REGION=us-central1
REPO=us-central1-docker.pkg.dev/$PROJECT/cloud-run-source-deploy
RUNTIME_SA=devlake@eduk-prd-lake.iam.gserviceaccount.com

target=${1:-all}
cd "$(dirname "$0")/.."
SHORT_SHA=$(git rev-parse --short HEAD)
GCLOUD_BIN=${GCLOUD_BIN:-gcloud}

# The image tag is the current commit SHA. Do not let an uncommitted source
# change be deployed under a tag that cannot reproduce it later. Documentation
# and QA evidence may still be dirty while a manual production deploy happens.
# `git diff` só enxerga arquivo rastreado. Um arquivo NOVO em kernel/ ou
# backend/ não aparece nele, e o Cloud Build envia o diretório inteiro — a
# imagem sairia com código que o commit do nome dela não contém, que é
# exatamente o que esta guarda promete impedir. `git status --porcelain` pega
# rastreado, staged e novo de uma vez.
SOURCE_DIRS=(backend kernel frontend infra .github)
if [ -n "$(git status --porcelain --untracked-files=normal -- "${SOURCE_DIRS[@]}")" ]; then
  echo "source tree is dirty; commit source changes before deploy" >&2
  git status --short --untracked-files=normal -- "${SOURCE_DIRS[@]}" >&2
  exit 1
fi

build() {
  local service=$1
  "$GCLOUD_BIN" builds submit --project=$PROJECT \
    --config=infra/cloudbuild-$service.yaml \
    --substitutions=SHORT_SHA=$SHORT_SHA .
}

deploy_kernel() {
  build kernel
  "$GCLOUD_BIN" run deploy teste-ia-kernel \
    --project=$PROJECT --region=$REGION \
    --image=$REPO/teste_ia-kernel:$SHORT_SHA \
    --service-account=$RUNTIME_SA \
    --set-secrets=DATABASE_URL=teste-ia-database-url:latest,SERPER_API_KEY=teste-ia-serper-key:latest,S3_ACCESS_KEY_ID=teste-ia-s3-access-key:latest,S3_SECRET_ACCESS_KEY=teste-ia-s3-secret-key:latest,INTERNAL_TOKEN=teste-ia-kernel-internal-token:latest \
    --set-env-vars="ENABLE_STUB_CONTROL=false,STORAGE_BACKEND=s3,S3_BUCKET=teste-ia,S3_ENDPOINT_URL=https://storage.rangeltech.net,S3_PUBLIC_BASE_URL=https://storage.rangeltech.net/teste-ia,S3_REGION=us-east-1,S3_PREFIX=agent-llm" \
    --allow-unauthenticated \
    --memory=1Gi --cpu=1 --min-instances=0 --max-instances=3 \
    --timeout=600
  # NOTA (infra-04): a proteção agora é o shared secret INTERNAL_TOKEN, não
  # mais IAM. O secret `teste-ia-kernel-internal-token` precisa existir no
  # Secret Manager ANTES desta primeira execução pós-migração
  # (`gcloud secrets create teste-ia-kernel-internal-token --data-file=-`
  # com o mesmo valor que vai em KERNEL_INTERNAL_TOKEN no compose da VPS).
  # Sem esse secret criado, o deploy falha; se INTERNAL_TOKEN ficar vazio por
  # engano, require_internal_auth() no kernel não bloqueia NADA — ver
  # kernel/app/runs.py, é um fail-open por padrão (modo dev), não fail-closed.
}

case $target in
  kernel) deploy_kernel ;;
  *) echo "usage: $0 kernel   (backend/frontend agora fazem deploy via vps_rt_infra, ver infra-04 da mega spec)" >&2; exit 1 ;;
esac
