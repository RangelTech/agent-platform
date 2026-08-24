#!/usr/bin/env bash
# Deploy do backend (FastAPI + SPA embutida) pro Cloud Run, projeto rangel-tech.
#
# infra-01 seção 5 (mega-spec-reestrutura): backend migrado da VPS pro Cloud
# Run (min=0, tolera cold start), CUTOVER COMPLETO em 21/08/2026 — VPS não
# roda mais este serviço (compose e workflow de deploy-vps removidos). SPA
# continua embutida no mesmo container (_mount_spa, backend/Dockerfile builda
# o frontend) — mesmo padrão de sempre, sem separação em Cloud Storage/CDN.
#
# DATABASE_URL aponta pro Postgres real da VPS via porta 5433
# (postgres-direct, TLS) — mesmo achado do litellm-router/kernel-llm: a
# porta 5432 (PgBouncer) não faz TLS server-side.
set -euo pipefail

PROJECT=rangel-tech
REGION=us-central1
REPO=us-central1-docker.pkg.dev/$PROJECT/containers

cd "$(dirname "$0")/.."
SHORT_SHA=$(git rev-parse --short HEAD)
GCLOUD_BIN=${GCLOUD_BIN:-gcloud}

if [ -n "$(git status --porcelain --untracked-files=normal -- backend frontend infra)" ]; then
  echo "source tree is dirty; commit source changes before deploy" >&2
  git status --short --untracked-files=normal -- backend frontend infra >&2
  exit 1
fi

"$GCLOUD_BIN" builds submit --project=$PROJECT \
  --config=infra/cloudbuild-backend.yaml \
  --substitutions=SHORT_SHA=$SHORT_SHA .

# BRIDGE_URL real: achado 23/08/2026 (mesma classe do BRIDGE_PUBLIC_URL do
# chatwoot-rt) -- "bridge.rangeltech.net" resolve pro IP antigo da VPS, de
# antes da bridge migrar pro Cloud Run (infra-01); Traefik lá não tem rota
# pra ela (404). Backend usa BRIDGE_URL pra SSO/chamadas de provisionamento
# na bridge -- com a URL morta, esses caminhos falhavam silenciosamente.
BRIDGE_URL=$("$GCLOUD_BIN" run services describe chatwoot-bridge \
  --project=$PROJECT --region=$REGION --format='value(status.url)')

"$GCLOUD_BIN" run deploy agent-llm-backend \
  --project=$PROJECT --region=$REGION \
  --image=$REPO/agent-platform-backend:$SHORT_SHA \
  --set-secrets=DATABASE_URL=agent-platform-database-url:latest,ENCRYPTION_KEY=agent-platform-encryption-key:latest,S3_ACCESS_KEY_ID=gcs-hmac-access-key:latest,S3_SECRET_ACCESS_KEY=gcs-hmac-secret-key:latest,KERNEL_INTERNAL_TOKEN=agent-platform-kernel-internal-token:latest,BRIDGE_ADMIN_TOKEN=agent-platform-bridge-admin-token:latest \
  --set-env-vars="KERNEL_URL=https://kernel-llm-pujq3pjmca-uc.a.run.app,PUBLIC_BASE_URL=https://ia.rangeltech.net,BRIDGE_URL=$BRIDGE_URL,STORAGE_BACKEND=s3,S3_ENDPOINT_URL=https://storage.googleapis.com,S3_REGION=us-east-1,S3_BUCKET=rangel-tech-storage,S3_PREFIX=teste-ia/agent-llm,S3_PUBLIC_BASE_URL=https://storage.googleapis.com/rangel-tech-storage/teste-ia,AWS_REQUEST_CHECKSUM_CALCULATION=when_required,AWS_RESPONSE_CHECKSUM_VALIDATION=when_required" \
  --allow-unauthenticated \
  --memory=1Gi --cpu=1 --min-instances=1 --max-instances=5 \
  --timeout=300
# min-instances=1 por decisão do dono (24/08/2026): entrou em produção,
# cold start real de 15s+ em min=0 (achado 23/08/2026) volta a valer --
# documentado na skill `agentllm` (personal-skills), seção "min_instances
# provados 23/08/2026".
