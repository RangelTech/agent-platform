# Achado real 25/08/2026, manhã: `auth.openai.com` recusa conexão do IP
# compartilhado padrão do Cloud Run (us-central1) -- Claude passou depois da
# troca pra patchright+Chrome real, mas o Codex continua com
# ERR_CONNECTION_REFUSED, sinal de bloqueio de IP/rede, não de fingerprint
# de navegador. Decisão do dono: IP estático dedicado, real, região São
# Paulo -- não é IP falso/spoofado, é um IP público de verdade alocado pelo
# próprio Google, só que isolado (não compartilhado com outros clientes
# Cloud Run) e geolocalizado como esperado pra uma empresa brasileira, em
# vez do IP genérico us-central1 (Iowa) que pode já estar com reputação
# ruim por tráfego de terceiros na mesma faixa compartilhada.
#
# Serviço inteiro precisa migrar pra `southamerica-east1` porque o
# connector do VPC Access precisa estar na MESMA região do Cloud Run.

resource "google_compute_network" "oauth_browser" {
  name                    = "oauth-browser-net"
  project                 = var.project
  auto_create_subnetworks = false
}

# Faixa dedicada só pro VPC Access Connector (exigência do produto: precisa
# ser um /28 próprio, sem sobrepor a subnet acima).
resource "google_compute_subnetwork" "oauth_browser_connector" {
  name          = "oauth-browser-connector-subnet"
  project       = var.project
  region        = "southamerica-east1"
  network       = google_compute_network.oauth_browser.id
  ip_cidr_range = "10.10.1.0/28"
}

resource "google_compute_address" "oauth_browser_nat_ip" {
  name         = "oauth-browser-nat-ip"
  project      = var.project
  region       = "southamerica-east1"
  network_tier = "PREMIUM"
}

resource "google_compute_router" "oauth_browser" {
  name    = "oauth-browser-router"
  project = var.project
  region  = "southamerica-east1"
  network = google_compute_network.oauth_browser.id
}

resource "google_compute_router_nat" "oauth_browser" {
  name    = "oauth-browser-nat"
  project = var.project
  router  = google_compute_router.oauth_browser.name
  region  = "southamerica-east1"

  nat_ip_allocate_option = "MANUAL_ONLY"
  nat_ips                = [google_compute_address.oauth_browser_nat_ip.self_link]

  # Achado real 25/08/2026: o tráfego de saída do Cloud Run passa pela
  # subnet do CONNECTOR (VPC Access Connector), não por uma subnet própria
  # do serviço -- tinha criado uma subnet "oauth-browser-sp-subnet" separada
  # e configurado o NAT nela, que ninguém usava de verdade. Resultado: sem
  # regra de NAT pra faixa que o connector realmente usa, o tráfego saía
  # pra internet sem NAT nenhum -- rota inexistente, timeout (não recusa),
  # em VEZ de sair pelo IP estático. Corrigido: NAT a subnet do connector.
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
  subnetwork {
    name                    = google_compute_subnetwork.oauth_browser_connector.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }
}

resource "google_vpc_access_connector" "oauth_browser" {
  name    = "oauth-browser-conn"
  project = var.project
  region  = "southamerica-east1"
  subnet {
    name = google_compute_subnetwork.oauth_browser_connector.name
  }
  machine_type  = "e2-micro"
  min_instances = 2
  max_instances = 3
}

output "nat_static_ip" {
  value = google_compute_address.oauth_browser_nat_ip.address
}
