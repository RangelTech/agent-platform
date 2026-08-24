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
    prefix = "oauth-browser"
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
variable "image" { type = string }

# Navegador remoto pra OAuth de Claude/Codex que só aceita redirect_uri
# fixo/loopback (produto-08, adendo 24/08/2026) -- ver oauth-browser/app.py.
# O token administrativo vive no Secret Manager (não no Infisical, ao
# contrário do padrão do bridge): Cloud Run resolve o valor em runtime com
# a própria service account, o deploy nunca vê o segredo em texto plano.
resource "google_cloud_run_v2_service" "oauth_browser" {
  name     = "oauth-browser"
  project  = var.project
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    containers {
      image = var.image
      resources {
        limits = {
          cpu    = "4"
          memory = "4Gi"
        }
      }
      env {
        name = "OAUTH_BROWSER_ADMIN_TOKEN"
        value_source {
          secret_key_ref {
            secret  = "agent-platform-oauth-browser-admin-token"
            version = "latest"
          }
        }
      }
      env {
        name  = "OAUTH_BROWSER_ALLOWED_ORIGIN"
        value = "https://ia.rangeltech.net"
      }
    }
    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }
    # Uma sessão (um Chromium + uma stream de screencast) monopoliza a
    # instância -- duas sessões concorrentes na mesma instância brigariam
    # pela mesma aba/contexto sem isolamento nenhum.
    max_instance_request_concurrency = 1
    timeout                          = "3600s"
  }
}

# Autenticação é em nível de aplicação (token de sessão de uso único, gerado
# por /sessions), não IAM -- é o navegador do admin do tenant quem conecta
# direto no WebSocket, e IAM do Cloud Run não dá pra checar por request de
# WS vindo de fora da infra Google.
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project
  location = var.region
  name     = google_cloud_run_v2_service.oauth_browser.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "url" {
  value = google_cloud_run_v2_service.oauth_browser.uri
}
