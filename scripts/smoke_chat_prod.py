"""Smoke de conversa real em produção.

Manda UMA mensagem de chat de verdade, consumindo o SSE, para provar que o
caminho backend -> kernel -> LangGraph -> banco está inteiro. É o teste que
pega quebra de checkpointer/banco que uma checagem de rota HTTP não pega.

Uso:
    REGRESSAO_BACKEND=https://... python scripts/smoke_chat_prod.py
"""

import json
import os
import sys

import httpx

BACKEND = os.environ.get("REGRESSAO_BACKEND", "http://localhost:8090")
MASTER_EMAIL = os.environ.get("HOMOLOG_MASTER_EMAIL", "master@example.com")
MASTER_PASSWORD = os.environ.get("HOMOLOG_MASTER_PASSWORD", "admin123")
PASSWORD = os.environ.get("SMOKE_PASSWORD", "smoke-senha-forte-123")


def main() -> None:
    with httpx.Client(base_url=BACKEND, timeout=180.0) as client:
        master = client.post(
            "/api/auth/login", json={"email": MASTER_EMAIL, "password": MASTER_PASSWORD}
        ).json()["token"]
        mh = {"Authorization": f"Bearer {master}"}

        tenants = client.get("/api/tenants", headers=mh).json()
        tenant = next(t for t in tenants if t.get("is_active", True))
        users = client.get("/api/users", headers=mh).json()
        user = next(u for u in users if u.get("tenant_id") == tenant["id"])

        # Reaproveita um usuário real do tenant, apenas ajustando a senha.
        client.put(f"/api/users/{user['id']}", json={"password": PASSWORD}, headers=mh)
        token = client.post(
            "/api/auth/login", json={"email": user["email"], "password": PASSWORD}
        ).json()["token"]

        events, chat_id, error = [], None, None
        with client.stream(
            "POST",
            "/api/chat/send",
            json={"message": "Responda apenas: smoke ok."},
            headers={"Authorization": f"Bearer {token}"},
        ) as response:
            response.raise_for_status()
            event = None
            for line in response.iter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    payload = json.loads(line[6:])
                    events.append(event)
                    if event == "chat":
                        chat_id = payload.get("chat_id")
                    if event == "error":
                        error = payload.get("detail")

        reply_arrived = "done" in events
        print(f"tenant={tenant['name']} usuario={user['email']}")
        print(f"eventos={sorted(set(events))} chat_id={chat_id} erro={error}")
        ok = reply_arrived and error is None
        print("RESULTADO:", "APROVADO" if ok else "REPROVADO")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
