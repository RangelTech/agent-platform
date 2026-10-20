"""Credenciais do tenant expostas ao agente como variáveis de ambiente nomeadas.

O terminal do agente recebe `credentials.secrets` (nome -> valor). Além dos segredos cadastrados,
as outras credenciais viram segredos nomeados derivados, para a skill usar sem ferramenta nova:

- SSH:        SSH_<NOME> (chave privada), SSH_<NOME>_HOST, _PORT, _USER
- GCP:        GCP_<NOME>_JSON (JSON da service account)
- Banco:      DB_<NOME>_URL (URL de conexão com a senha)
- E-mail:     EMAIL_<NOME>_ADDRESS/_USER/_PASSWORD/_SMTP_HOST/_SMTP_PORT/_IMAP_HOST/_IMAP_PORT
- Google/MS:  GOOGLE_<NOME>_TOKEN, MICROSOFT_<NOME>_TOKEN (token de acesso renovado a cada execução)

Os valores nunca vão para log nem para resposta: o kernel mascara tudo o que está em `secrets`.
"""

import re
from urllib.parse import quote

_URL_SCHEME = {
    "postgresql": "postgresql",
    "mysql": "mysql",
    "mongodb": "mongodb",
    "sqlserver": "mssql",
    "oracle": "oracle",
}


def slug(text: str, fallback: str = "CONTA") -> str:
    """Trecho de nome de variável de ambiente: maiúsculas, dígitos e sublinhado."""
    out = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_").upper()
    return out or fallback


def database_url(ds: dict) -> str | None:
    """URL de conexão do banco, ou None quando o tipo não tem URL (ex.: BigQuery)."""
    kind, cfg, secret = ds.get("kind"), ds.get("config") or {}, ds.get("secret")
    if kind == "sqlite":
        return f"sqlite:///{cfg.get('path')}" if cfg.get("path") else None
    if kind == "firebird":
        return None
    scheme = _URL_SCHEME.get(kind or "")
    if not scheme or not cfg.get("host"):
        return None
    auth = ""
    if cfg.get("user"):
        auth = quote(str(cfg["user"]), safe="")
        if secret:
            auth += ":" + quote(str(secret), safe="")
        auth += "@"
    port = f":{int(cfg['port'])}" if cfg.get("port") else ""
    path = f"/{quote(str(cfg['database']), safe='')}" if cfg.get("database") else ""
    url = f"{scheme}://{auth}{cfg['host']}{port}{path}"
    if cfg.get("ssl"):
        url += "?sslmode=require" if kind == "postgresql" else "?ssl=true"
    return url


EMAIL_SUFFIXES = (
    "ADDRESS", "USER", "PASSWORD", "SMTP_HOST", "SMTP_PORT", "IMAP_HOST", "IMAP_PORT",
)  # fmt: skip


def secret_vars(kind: str, name: str) -> list[str]:
    """Variáveis que um segredo cadastrado entrega ao agente."""
    if kind == "ssh":
        return [name, f"{name}_HOST", f"{name}_PORT", f"{name}_USER"]
    return [name]


def database_var(name: str) -> str:
    return f"DB_{slug(name, 'BANCO')}_URL"


def email_vars(label: str) -> list[str]:
    base = f"EMAIL_{slug(label, 'EMAIL')}"
    return [f"{base}_{suffix}" for suffix in EMAIL_SUFFIXES]


def oauth_var(prefix: str, label: str | None, email: str | None) -> str:
    return f"{prefix}_{slug(label or email, 'CONTA')}_TOKEN"


def derive(
    secret_rows: list[dict],
    datasources: list[dict],
    email_accounts: list[dict],
    google_accounts: list[dict],
    microsoft_accounts: list[dict],
    decrypt,
) -> dict[str, str]:
    """Segredos nomeados (nome -> valor) vindos de todos os tipos de credencial."""
    out: dict[str, str] = {}
    for r in secret_rows:
        out[r["name"]] = decrypt(r["value_encrypted"])
        if r.get("kind") == "ssh":
            meta = r.get("meta") or {}
            _key, host, port, user = secret_vars("ssh", r["name"])
            out[host] = str(meta.get("host") or "")
            out[port] = str(meta.get("port") or 22)
            out[user] = str(meta.get("user") or "")
    for ds in datasources:
        url = database_url(ds)
        if url:
            out[database_var(ds["name"])] = url
    for acc in email_accounts:
        values = (
            acc.get("email_address"),
            acc.get("username"),
            acc.get("password"),
            acc.get("smtp_host"),
            acc.get("smtp_port"),
            acc.get("imap_host"),
            acc.get("imap_port"),
        )
        for var, value in zip(email_vars(acc.get("label")), values, strict=True):
            out[var] = str(value or "")
    for prefix, accounts in (("GOOGLE", google_accounts), ("MICROSOFT", microsoft_accounts)):
        for acc in accounts:
            if acc.get("access_token"):
                var = oauth_var(prefix, acc.get("label"), acc.get("email_address"))
                out[var] = str(acc["access_token"])
    return {k: v for k, v in out.items() if v != ""}
