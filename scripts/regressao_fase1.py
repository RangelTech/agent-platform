"""Regressão consolidada da Mega Spec 1 (Fase Z).

Roda contra um ambiente já de pé (local ou produção) e verifica, com chamadas
reais, que cada fase entregou o que prometeu. Não mocka nada: se um endpoint
sumir ou uma permissão regredir, o relatório fica vermelho.

Uso:
    python scripts/regressao_fase1.py                 # local (localhost:8090)
    REGRESSAO_BACKEND=https://... python scripts/regressao_fase1.py

Escreve o resultado em docs/regressao-fase1.json.
"""

import json
import os
import sys
import uuid

# timezone.utc (e não datetime.UTC): este script também roda em Python 3.10
# fora do container.
from datetime import datetime, timezone  # noqa: UP017

import httpx

BACKEND = os.environ.get("REGRESSAO_BACKEND", "http://localhost:8090")
MASTER_EMAIL = os.environ.get("HOMOLOG_MASTER_EMAIL", "master@example.com")
MASTER_PASSWORD = os.environ.get("HOMOLOG_MASTER_PASSWORD", "admin123")
RESULT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "regressao-fase1.json")


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class Report:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def record(self, phase: str, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"phase": phase, "check": name, "ok": bool(ok), "detail": detail[:500]})
        suffix = f" ({detail[:120]})" if detail and not ok else ""
        print(f"  [{'ok ' if ok else 'FAIL'}] {phase} — {name}{suffix}")
        return ok

    def run(self, phase: str, name: str, fn) -> bool:
        try:
            ok, detail = fn()
            return self.record(phase, name, ok, detail)
        except Exception as exc:  # noqa: BLE001 — a regressão reporta, não explode
            return self.record(phase, name, False, repr(exc))

    @property
    def passed(self) -> bool:
        return all(c["ok"] for c in self.checks)


def main() -> None:
    report = Report()
    print(f"=== regressão Mega Spec 1 contra {BACKEND} ===")

    with httpx.Client(base_url=BACKEND, timeout=60.0) as client:
        # --- plataforma de pé (Fase 0) ---
        report.run(
            "fase-0",
            "backend responde /api/health",
            lambda: (client.get("/api/health").status_code == 200, ""),
        )

        login = client.post(
            "/api/auth/login", json={"email": MASTER_EMAIL, "password": MASTER_PASSWORD}
        )
        if not report.record("fase-0", "login do master", login.status_code == 200, login.text):
            _save(report)
            sys.exit(1)
        master = auth(login.json()["token"])

        # Tenant descartável para não sujar dados reais.
        suffix = uuid.uuid4().hex[:8]
        tenant = client.post(
            "/api/tenants",
            json={"name": f"Regressão {suffix}", "tenant_key": f"regressao-{suffix}"},
            headers=master,
        )
        tenant_ok = report.record(
            "fase-0", "criação de empresa", tenant.status_code == 201, tenant.text
        )
        if not tenant_ok:
            _save(report)
            sys.exit(1)
        tenant_id = tenant.json()["id"]

        admin_email = f"regressao-{suffix}@example.com"
        profiles = client.get(f"/api/user-profiles?tenant_id={tenant_id}", headers=master)
        admin_profile = next(
            (
                p
                for p in (profiles.json() if profiles.status_code == 200 else [])
                if p.get("tenant_id") == tenant_id and p.get("name") == "Administrador"
            ),
            None,
        )
        created = client.post(
            "/api/users",
            json={
                "email": admin_email,
                "name": "Admin Regressão",
                "password": "senha-forte-123",
                "tenant_id": tenant_id,
                **({"profile_id": admin_profile["id"]} if admin_profile else {}),
            },
            headers=master,
        )
        report.record(
            "fase-0", "criação de usuário admin", created.status_code == 201, created.text
        )
        token = client.post(
            "/api/auth/login", json={"email": admin_email, "password": "senha-forte-123"}
        ).json()["token"]
        admin = auth(token)

        # --- Fase B: catálogo de tools do kernel visível pela plataforma ---
        def tools_check():
            r = client.get("/api/toolkits", headers=admin)
            names = {t.get("name") for t in (r.json() if r.status_code == 200 else [])}
            required = {
                "run_sql_query",
                "execute_sql_write",
                "execute_sql_transaction",
                "generate_chart",
                "generate_pix_charge",
                "check_payment_status",
            }
            missing = required - names
            return not missing, f"faltando: {sorted(missing)}" if missing else f"{len(names)} tools"

        report.run("fase-b", "catálogo de tools do kernel completo", tools_check)

        # --- Fase A: SPA servida e rota de dashboard existe ---
        def spa_check():
            r = client.get("/")
            return r.status_code == 200 and "<div id=\"root\">" in r.text, f"HTTP {r.status_code}"

        report.run("fase-a", "SPA servida pelo backend", spa_check)

        # --- Fase D: credencial de pagamento write-only e isolada ---
        def payment_check():
            secret = f"APP_USR-{uuid.uuid4().hex}"
            r = client.put(
                "/api/payments/credentials",
                json={"access_token": secret, "sandbox": True},
                headers=admin,
            )
            if r.status_code != 200:
                return False, r.text
            if secret in r.text:
                return False, "token voltou em claro"
            listing = client.get("/api/payments/credentials", headers=admin)
            return (
                listing.status_code == 200 and secret not in listing.text,
                "listagem expôs o token" if secret in listing.text else "",
            )

        report.run("fase-d", "credencial Mercado Pago write-only", payment_check)

        def webhook_check():
            r = client.post(f"/api/payments/webhooks/mercado-pago/{uuid.uuid4().hex}", json={})
            return r.status_code == 404, f"HTTP {r.status_code}"

        report.run("fase-d", "webhook desconhecido é rejeitado", webhook_check)

        # --- Fase E: catálogo curado + ativação isolada ---
        def catalog_check():
            r = client.get("/api/mcp-store/catalog", headers=admin)
            if r.status_code != 200:
                return False, r.text
            slugs = {i["slug"] for i in r.json()}
            return "pagamentos_pix" in slugs, f"slugs: {sorted(slugs)}"

        report.run("fase-e", "catálogo do MCP Store disponível", catalog_check)

        def curation_check():
            r = client.post(
                "/api/mcp-store/catalog",
                json={"slug": f"reg_{suffix}", "name": "X", "server_url": "https://x"},
                headers=admin,
            )
            return r.status_code == 403, f"HTTP {r.status_code}"

        report.run("fase-e", "tenant não publica item no catálogo", curation_check)

        # --- Fase F: canal WhatsApp ---
        def whatsapp_check():
            integration = client.post(
                "/api/integrations",
                json={"name": f"wa-{suffix}", "channel": "whatsapp"},
                headers=admin,
            )
            if integration.status_code != 201:
                return False, integration.text
            integration_id = integration.json()["id"]
            saved = client.put(
                f"/api/integrations/{integration_id}/whatsapp",
                json={"instance_id": "regressao", "token": f"tok-{suffix}"},
                headers=admin,
            )
            if saved.status_code != 200 or f"tok-{suffix}" in saved.text:
                return False, saved.text
            hook = client.post(f"/webhooks/whatsapp/{integration_id}", json={"ping": True})
            return hook.status_code == 200, f"webhook HTTP {hook.status_code}"

        report.run("fase-f", "conexão W-API e webhook tolerante", whatsapp_check)

        # --- transversal: isolamento entre empresas ---
        def isolation_check():
            other = client.post(
                "/api/tenants",
                json={"name": f"Outra {suffix}", "tenant_key": f"outra-{suffix}"},
                headers=master,
            ).json()
            client.put(
                "/api/payments/credentials",
                json={"access_token": "APP_USR-de-outra-empresa", "tenant_id": other["id"]},
                headers=master,
            )
            visible = client.get("/api/payments/credentials", headers=admin).json()
            return all(c["tenant_id"] != other["id"] for c in visible), ""

        report.run("transversal", "credenciais não vazam entre empresas", isolation_check)

    _save(report)
    print(f"\nRESULTADO: {'APROVADO' if report.passed else 'REPROVADO'}")
    sys.exit(0 if report.passed else 1)


def _save(report: Report) -> None:
    payload = {
        "backend": BACKEND,
        "executed_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "passed": report.passed,
        "checks": report.checks,
    }
    os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
    with open(RESULT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"relatório salvo em {RESULT_PATH}")


if __name__ == "__main__":
    main()
