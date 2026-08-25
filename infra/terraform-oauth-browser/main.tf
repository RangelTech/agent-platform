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
  type = string
  # Achado real 25/08/2026: tentei mover pra southamerica-east1 com IP
  # estático dedicado (Cloud NAT) achando que resolveria o bloqueio do
  # Codex -- não resolveu (auth.openai.com bloqueia QUALQUER IP do Google
  # Cloud, não só o us-central1 compartilhado, ver produto-08 seção 9b).
  # Revertido pra us-central1 (decisão do dono: não pagar por infra que não
  # resolveu o problema) -- o Codex agora usa o proxy da VPS (app.py),
  # não depende mais de qual região o Cloud Run roda.
  default = "us-central1"
}
variable "image" { type = string }
variable "admin_token" {
  type      = string
  sensitive = true
}

# Produto-08 seção 9b: `auth.openai.com` recusa conexão de QUALQUER IP do
# Google Cloud (ASN de datacenter), independente de região -- confirmado
# até com o IP estático dedicado de São Paulo. A VPS (Contabo,
# 66.94.101.153, mesma máquina do chatwoot-worker) tem ASN diferente e
# recebe o desafio Cloudflare normal (mesmo tipo que o Claude já passa) em
# vez de bloqueio de borda -- usado como proxy SOCKS5 só pro provider
# "codex", não pros demais (que já funcionam direto). Proxy roda em
# container Docker na VPS (`serjs/go-socks5-proxy`), firewalled via
# DOCKER-USER pro IP estático do oauth-browser só (34.39.145.254).
variable "vps_socks_proxy_password" {
  type      = string
  sensitive = true
}

# Navegador remoto pra OAuth de Claude/Codex que só aceita redirect_uri
# fixo/loopback (produto-08, adendo 24/08/2026) -- ver oauth-browser/app.py.
# Token administrativo vem do Infisical (mesmo padrão do custom-tool-runner
# -- fonte da verdade de todo segredo, decisão do dono). Corrigido em
# 24/08/2026: nasceu no GCP Secret Manager por um desvio pontual (esta
# sessão não tinha credencial Infisical carregada na hora), migrado depois.
resource "google_cloud_run_v2_service" "oauth_browser" {
  name                = "oauth-browser"
  project             = var.project
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

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
        name  = "OAUTH_BROWSER_ADMIN_TOKEN"
        value = var.admin_token
      }
      env {
        name  = "OAUTH_BROWSER_ALLOWED_ORIGIN"
        value = "https://ia.rangeltech.net"
      }
      env {
        name  = "VPS_SOCKS_PROXY_HOST"
        value = "66.94.101.153"
      }
      env {
        name  = "VPS_SOCKS_PROXY_PORT"
        value = "18080"
      }
      env {
        name  = "VPS_SOCKS_PROXY_USER"
        value = "oauthbrowser"
      }
      env {
        name  = "VPS_SOCKS_PROXY_PASSWORD"
        value = var.vps_socks_proxy_password
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
