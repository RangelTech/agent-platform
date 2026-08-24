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

# LITELLM_BASE_URL/MASTER_KEY + ROUTER_AUTO_PROVISION_ENABLED: achado real
# 24/08/2026 -- o backend nunca teve essas vars, então nem o self-service de
# contas/combos (rotas em ai_router.py) nem o auto-provisionamento de Team
# num tenant novo (tenants.py, corrigido no mesmo achado pra chamar LiteLLM
# em vez do 9Router decomissionado) funcionavam de verdade em produção.
# Valor existente (não é rotação nova) -- master key já é usada pelo
# litellm-router. Vem do Infisical via $LITELLM_MASTER_KEY (exportado pelo
# `ci.yml` antes de chamar este script) -- decisão do dono (24/08/2026):
# Infisical é a fonte da verdade de todo secret, GCP Secret Manager custa e
# não deve ganhar segredo novo nenhum.
: "${LITELLM_MASTER_KEY:?LITELLM_MASTER_KEY não veio do ambiente (esperado do ci.yml via Infisical)}"

# ANTIGRAVITY_OAUTH_CLIENT_SECRET/GEMINI_CLI_OAUTH_CLIENT_SECRET: produto-08
# (mega-spec-reestrutura) -- client secret público de app "installed" do
# Google (mesmo valor embutido no binário oficial da Antigravity/gemini-cli,
# não é rotação nova), mora no Infisical por regra do dono, não hardcoded
# em `oauth_engine.py` (GitHub push protection já recusou o push com o
# valor cru no código).
: "${ANTIGRAVITY_OAUTH_CLIENT_SECRET:?ANTIGRAVITY_OAUTH_CLIENT_SECRET não veio do ambiente (esperado do ci.yml via Infisical)}"
: "${GEMINI_CLI_OAUTH_CLIENT_SECRET:?GEMINI_CLI_OAUTH_CLIENT_SECRET não veio do ambiente (esperado do ci.yml via Infisical)}"

# OAUTH_BROWSER_*: produto-08 adendo 24/08/2026 -- navegador remoto pra
# login de Claude/Codex (redirect_uri fixo/loopback deles, ver
# oauth-browser/app.py). URL hardcoded como KERNEL_URL/LITELLM_BASE_URL
# acima (serviço próprio, Cloud Run já deployado antes deste). O token
# administrativo vem do GCP Secret Manager, não do Infisical -- desvio
# pontual documentado (produto-08 seção 8): esta sessão não tinha
# credencial Infisical carregada pra criar o segredo lá.
"$GCLOUD_BIN" run deploy agent-llm-backend \
  --project=$PROJECT --region=$REGION \
  --image=$REPO/agent-platform-backend:$SHORT_SHA \
  --set-secrets=DATABASE_URL=agent-platform-database-url:latest,ENCRYPTION_KEY=agent-platform-encryption-key:latest,S3_ACCESS_KEY_ID=gcs-hmac-access-key:latest,S3_SECRET_ACCESS_KEY=gcs-hmac-secret-key:latest,KERNEL_INTERNAL_TOKEN=agent-platform-kernel-internal-token:latest,BRIDGE_ADMIN_TOKEN=agent-platform-bridge-admin-token:latest,OAUTH_BROWSER_ADMIN_TOKEN=agent-platform-oauth-browser-admin-token:latest \
  --set-env-vars="KERNEL_URL=https://kernel-llm-pujq3pjmca-uc.a.run.app,PUBLIC_BASE_URL=https://ia.rangeltech.net,BRIDGE_URL=$BRIDGE_URL,STORAGE_BACKEND=s3,S3_ENDPOINT_URL=https://storage.googleapis.com,S3_REGION=us-east-1,S3_BUCKET=rangel-tech-storage,S3_PREFIX=teste-ia/agent-llm,S3_PUBLIC_BASE_URL=https://storage.googleapis.com/rangel-tech-storage/teste-ia,AWS_REQUEST_CHECKSUM_CALCULATION=when_required,AWS_RESPONSE_CHECKSUM_VALIDATION=when_required,LITELLM_BASE_URL=https://litellm-router-pujq3pjmca-uc.a.run.app,ROUTER_AUTO_PROVISION_ENABLED=true,LITELLM_MASTER_KEY=$LITELLM_MASTER_KEY,ANTIGRAVITY_OAUTH_CLIENT_SECRET=$ANTIGRAVITY_OAUTH_CLIENT_SECRET,GEMINI_CLI_OAUTH_CLIENT_SECRET=$GEMINI_CLI_OAUTH_CLIENT_SECRET,OAUTH_BROWSER_URL=https://oauth-browser-pujq3pjmca-uc.a.run.app" \
  --allow-unauthenticated \
  --memory=1Gi --cpu=1 --min-instances=1 --max-instances=5 \
  --timeout=300
# min-instances=1 por decisão do dono (24/08/2026): entrou em produção,
# cold start real de 15s+ em min=0 (achado 23/08/2026) volta a valer --
# documentado na skill `agentllm` (personal-skills), seção "min_instances
# provados 23/08/2026".
