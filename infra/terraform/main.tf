# ⚠️ Achado real 23/08/2026: este arquivo NÃO é o caminho de deploy real do
# `agent-llm-backend`. O CI (`ci.yml`, job `deploy-cloudrun`) chama
# `infra/deploy-cloudrun.sh` (plain `gcloud run deploy`) em todo push, não
# `terraform apply` -- mudar um valor aqui (ex.: `min_instance_count`) e só
# aqui não tem efeito nenhum em produção, e um push normal reverte qualquer
# ajuste feito manualmente via `gcloud run services update`. Se for alterar
# a config real do serviço, edite `infra/deploy-cloudrun.sh`. Não sei ainda
# se este `.tf` já foi a fonte de verdade antes e ficou desatualizado, ou se
# nunca chegou a ser -- não investiguei a fundo, só registrei o achado.
terraform {
  required_version = ">= 1.9.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  backend "gcs" {
    bucket = "rangel-tech-tfstate"
    prefix = "agent-llm-backend"
  }
}

provider "google" {
  project = var.project
  region  = var.region
}

variable "project" {
  type    = string
  default = "rangel-tech"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "image" {
  description = "Full image ref, tagged with the commit SHA being deployed."
  type        = string
}

variable "database_url" {
  type      = string
  sensitive = true
}

variable "encryption_key" {
  type      = string
  sensitive = true
}

variable "kernel_internal_token" {
  type      = string
  sensitive = true
}

variable "bridge_admin_token" {
  type      = string
  sensitive = true
}

variable "s3_access_key_id" {
  type      = string
  sensitive = true
}

variable "s3_secret_access_key" {
  type      = string
  sensitive = true
}

resource "google_cloud_run_v2_service" "agent_llm_backend" {
  name     = "agent-llm-backend"
  project  = var.project
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    containers {
      image = var.image

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      env {
        name  = "KERNEL_URL"
        value = "https://kernel-llm-pujq3pjmca-uc.a.run.app"
      }
      env {
        name  = "TOOL_RUNNER_URL"
        value = "https://custom-tool-runner-pujq3pjmca-uc.a.run.app"
      }
      env {
        name  = "PUBLIC_BASE_URL"
        value = "https://ia.rangeltech.net"
      }
      env {
        name  = "BRIDGE_URL"
        value = "https://bridge.rangeltech.net"
      }
      env {
        name  = "STORAGE_BACKEND"
        value = "s3"
      }
      env {
        name  = "S3_ENDPOINT_URL"
        value = "https://storage.googleapis.com"
      }
      env {
        name  = "S3_REGION"
        value = "us-east-1"
      }
      env {
        name  = "S3_BUCKET"
        value = "rangel-tech-storage"
      }
      env {
        name  = "S3_PREFIX"
        value = "teste-ia/agent-llm"
      }
      env {
        name  = "S3_PUBLIC_BASE_URL"
        value = "https://storage.googleapis.com/rangel-tech-storage/teste-ia"
      }
      env {
        name  = "AWS_REQUEST_CHECKSUM_CALCULATION"
        value = "when_required"
      }
      env {
        name  = "AWS_RESPONSE_CHECKSUM_VALIDATION"
        value = "when_required"
      }
      env {
        name  = "DATABASE_URL"
        value = var.database_url
      }
      env {
        name  = "ENCRYPTION_KEY"
        value = var.encryption_key
      }
      env {
        name  = "KERNEL_INTERNAL_TOKEN"
        value = var.kernel_internal_token
      }
      env {
        name  = "BRIDGE_ADMIN_TOKEN"
        value = var.bridge_admin_token
      }
      env {
        name  = "S3_ACCESS_KEY_ID"
        value = var.s3_access_key_id
      }
      env {
        name  = "S3_SECRET_ACCESS_KEY"
        value = var.s3_secret_access_key
      }
    }

    scaling {
      # Achado real 23/08/2026 (mega-spec-reestrutura, item B de performance):
      # min=0 media 2 de 6 requisições reais de `GET /` estourando timeout de
      # 15s e uma 3ª levando 6,9s -- cold start real, não hipotético. min=1
      # elimina esse pior caso (confirmado: 8/8 amostras depois ficaram
      # entre 0,54-0,99s, zero timeout) -- **esse é o valor certo pra
      # produção**, documentado na skill `agentllm`.
      #
      # Voltado pra min=0 por decisão do dono (23/08/2026): ainda em dev,
      # contenção de custo é prioridade sobre cold start enquanto não tem
      # cliente real. Lembrar de voltar pra 1 quando entrar em produção.
      min_instance_count = 0
      max_instance_count = 5
    }

    timeout = "300s"
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project
  location = var.region
  name     = google_cloud_run_v2_service.agent_llm_backend.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "url" {
  value = google_cloud_run_v2_service.agent_llm_backend.uri
}
