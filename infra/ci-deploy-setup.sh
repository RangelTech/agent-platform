#!/usr/bin/env bash
# Prepara o que o job `deploy` do CI precisa para autenticar no GCP.
#
# Já foi executado uma vez em eduk-prd-lake; isto existe para que a
# configuração seja legível e reproduzível — e para poder ser refeita se
# alguém apagar o pool sem saber o que era.
#
# A escolha é Workload Identity Federation, não chave de service account em
# secret do GitHub: chave é credencial de vida longa que vaza em log, fica em
# fork e ninguém rotaciona. Aqui o GitHub troca um token OIDC de curta duração
# por uma identidade, e a condição de atributo amarra essa troca a ESTE
# repositório — um fork apresentando o mesmo provedor é recusado.
#
# Uso: ./infra/ci-deploy-setup.sh
set -euo pipefail

PROJECT=eduk-prd-lake
PROJECT_NUMBER=188258617713
REPO=LucasRangelSSouza/agent-platform
POOL=github-actions
PROVIDER=github
DEPLOYER=github-deployer@$PROJECT.iam.gserviceaccount.com
RUNTIME_SA=devlake@$PROJECT.iam.gserviceaccount.com

gcloud iam workload-identity-pools create $POOL \
  --project=$PROJECT --location=global --display-name="GitHub Actions" || true

gcloud iam workload-identity-pools providers create-oidc $PROVIDER \
  --project=$PROJECT --location=global --workload-identity-pool=$POOL \
  --display-name="GitHub OIDC" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --attribute-condition="assertion.repository=='$REPO'" \
  --issuer-uri="https://token.actions.githubusercontent.com" || true

gcloud iam service-accounts create github-deployer \
  --project=$PROJECT --display-name="GitHub Actions deployer" || true

# Só o que o deploy usa: construir a imagem, publicá-la e atualizar o serviço.
for role in roles/run.admin roles/cloudbuild.builds.editor \
            roles/artifactregistry.writer roles/storage.admin roles/logging.viewer; do
  gcloud projects add-iam-policy-binding $PROJECT \
    --member=serviceAccount:$DEPLOYER --role=$role --condition=None
done

# O serviço roda como devlake; para implantar "como" ela, o deployer precisa
# poder usá-la. Sem isto o deploy falha em iam.serviceAccounts.actAs.
gcloud iam service-accounts add-iam-policy-binding $RUNTIME_SA \
  --project=$PROJECT --member=serviceAccount:$DEPLOYER \
  --role=roles/iam.serviceAccountUser

# Quem, vindo do GitHub, pode assumir o deployer: qualquer workflow deste
# repositório — e nada além dele.
gcloud iam service-accounts add-iam-policy-binding $DEPLOYER \
  --project=$PROJECT --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.repository/$REPO"

echo "pronto — o job deploy do ci.yml já pode autenticar"
