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
    prefix = "custom-tool-runner"
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
variable "database_url" {
  type      = string
  sensitive = true
}
variable "encryption_key" {
  type      = string
  sensitive = true
}

resource "google_cloud_run_v2_service" "runner" {
  name     = "custom-tool-runner"
  project  = var.project
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    containers {
      image = var.image
      resources {
        limits = {
          cpu    = "8"
          memory = "32Gi"
        }
      }
      env {
        name  = "DATABASE_URL"
        value = var.database_url
      }
      env {
        name  = "ENCRYPTION_KEY"
        value = var.encryption_key
      }
    }
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    max_instance_request_concurrency = 1
    timeout                          = "3600s"
  }
}

# The MCP client authenticates every call using a tenant-specific opaque
# bearer token. Public invocation is required so the private kernel service
# can reach it through standard streamable HTTP.
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project
  location = var.region
  name     = google_cloud_run_v2_service.runner.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "url" {
  value = google_cloud_run_v2_service.runner.uri
}
