#!/usr/bin/env python3
"""Migra o 9Router antigo (`ninerouter`) para o tenant `platform` do gateway (G-10).

Roda na VPS, como o usuario deploy (precisa de docker e de leitura do sqlite antigo).
Idempotente: pode rodar de novo sem duplicar.

Passos:
  1. backup do sqlite do gateway (VACUUM INTO em db/backups);
  2. tenant `platform` (POST /api/tenants, idempotente);
  3. copia as conexoes OAuth do ninerouter para o tenant (INSERT OR IGNORE pelo id);
  4. cria os combos com os MESMOS nomes dos aliases de hoje;
  5. verifica: chave restrita a cada combo -> POST /v1/chat/completions (200 esperado);
     a chave de teste e apagada no fim.

Tudo que toca o gateway roda DENTRO do container (`docker exec -u 1000 llm-gateway node`),
entao o MT_ADMIN_TOKEN nunca sai dele. Tokens OAuth ficam em texto claro no sqlite
(decisao aceita). Os refresh tokens sao de uso unico: depois do corte, nao use mais o
ninerouter antigo com as mesmas contas.

Uso: python3 gateway_migrar_platform.py [--sem-verificar] [--origem /opt/platform/data/ninerouter/db/data.sqlite]
"""

import argparse
import json
import sqlite3
import subprocess
import sys

TENANT = "platform"
COMBOS = {
    "ninerouter-gpt": "cx/gpt-6-luna",
    "ninerouter-gpt-sol": "cx/gpt-6-sol",
    "ninerouter-sonnet": "cc/claude-sonnet-5-5",
    "ninerouter-haiku": "cc/claude-haiku-4-5-20251001",
    "ninerouter-opus": "cc/claude-opus-5-5",
}
# Estado transitorio que nao deve ser herdado do ninerouter.
TRANSIENT = ("lastError", "lastErrorAt", "errorCode", "rateLimitedUntil", "backoffLevel")

NODE = r"""
const D = require("better-sqlite3");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const base = "http://127.0.0.1:20128";
const H = { "x-admin-token": process.env.MT_ADMIN_TOKEN, "content-type": "application/json" };
const T = { ...H, "x-tenant-id": input.tenant };
const call = async (method, path, headers, body) => {
  const r = await fetch(base + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const text = await r.text();
  let json; try { json = JSON.parse(text); } catch { json = { raw: text.slice(0, 200) }; }
  return { status: r.status, json };
};
const out = { steps: [] };
const log = (s, d) => { out.steps.push({ s, ...d }); };

const file = "/app/data/db/data.sqlite";
const db = new D(file);
const ts = new Date().toISOString().replace(/[:.]/g, "-");
require("fs").mkdirSync("/app/data/db/backups", { recursive: true });
const bak = `/app/data/db/backups/pre-platform-${ts}.sqlite`;
db.exec(`VACUUM INTO '${bak}'`);
log("backup", { file: bak });

let r = await call("POST", "/api/tenants", T, { id: input.tenant, name: "Plataforma (templates sem tenant proprio)" });
log("tenant", { status: r.status, existed: r.json.existed ?? false });

const ins = db.prepare(`INSERT OR IGNORE INTO providerConnections
  (id, provider, authType, name, email, priority, isActive, tenantId, data, createdAt, updatedAt)
  VALUES (@id,@provider,@authType,@name,@email,@priority,@isActive,@tenantId,@data,@createdAt,@updatedAt)`);
let added = 0;
for (const c of input.connections) added += ins.run({ ...c, tenantId: input.tenant }).changes;
log("connections", { added, total: input.connections.length });

const existing = (await call("GET", "/api/combos", T)).json.combos || [];
for (const [name, model] of Object.entries(input.combos)) {
  if (existing.some(c => c.name === name)) { log("combo", { name, status: "ja existia" }); continue; }
  r = await call("POST", "/api/combos", T, { name, models: [model], strategy: { fallbackStrategy: "fallback" } });
  log("combo", { name, status: r.status, error: r.json.error });
}

if (input.verify) {
  for (const name of Object.keys(input.combos)) {
    const k = await call("POST", "/api/keys", T, { name: `verif-${name}`, allowedCombos: [name] });
    if (k.status !== 201) { log("verify", { name, status: "key " + k.status, error: k.json.error }); continue; }
    const c = await call("POST", "/v1/chat/completions", { authorization: "Bearer " + k.json.key, "content-type": "application/json" },
      { model: name, max_tokens: 16, messages: [{ role: "user", content: "responda apenas: ok" }] });
    const reply = c.json?.choices?.[0]?.message?.content;
    log("verify", { name, status: c.status, reply: typeof reply === "string" ? reply.slice(0, 40) : undefined, error: c.json?.error?.message || c.json?.error });
    await call("DELETE", `/api/keys/${k.json.id}`, T);
  }
}
console.log(JSON.stringify(out));
"""


def ler_origem(path: str) -> list[dict]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = []
    for r in con.execute("SELECT * FROM providerConnections WHERE provider IN ('codex','claude')"):
        row = dict(r)
        data = json.loads(row["data"] or "{}")
        for k in list(data):
            if k in TRANSIENT or k.startswith("modelLock_"):
                data.pop(k)
        data["testStatus"] = "active"
        row["data"] = json.dumps(data)
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--origem", default="/opt/platform/data/ninerouter/db/data.sqlite")
    ap.add_argument("--sem-verificar", action="store_true")
    args = ap.parse_args()

    conns = ler_origem(args.origem)
    if not conns:
        print("nenhuma conexao codex/claude na origem", file=sys.stderr)
        return 2
    payload = {"tenant": TENANT, "connections": conns, "combos": COMBOS, "verify": not args.sem_verificar}
    script = "(async()=>{" + NODE + "})().catch(e=>{console.error(e);process.exit(1)})"
    proc = subprocess.run(
        ["docker", "exec", "-i", "-u", "1000", "llm-gateway", "node", "-e", script],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        print(proc.stderr[-1500:], file=sys.stderr)
        return proc.returncode
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    ok = True
    for step in result["steps"]:
        print(json.dumps(step, ensure_ascii=False))
        if step["s"] == "verify" and step.get("status") != 200:
            ok = False
        if step["s"] == "combo" and step.get("status") not in ("ja existia", 201):
            ok = False
    print("RESULTADO:", "OK" if ok else "FALHOU")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
