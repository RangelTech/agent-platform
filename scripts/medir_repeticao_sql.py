"""Mede consulta repetida DENTRO de um turno.

O número que importa não é "quantas consultas": é quantas foram **a mesma**
dentro do mesmo turno. Antes do cache de leitura um único turno chegou a 45
consultas, várias byte a byte idênticas, e nada apontava isso porque cada uma
retornava com sucesso.

Dois cuidados para o número não mentir:

- **Medir a conversa inteira dá o número errado**: o cache vive no turno, então
  a mesma consulta em turnos diferentes é legítima — a resposta pode ter
  mudado. Os turnos são separados por intervalo de tempo, que é o que existe no
  registro de ferramentas.
- **Chamada repetida não é consulta repetida**: quando o cache atende, a
  ferramenta ainda aparece no registro, mas o banco não foi tocado. O que
  interessa é quanto chegou ao banco.

Uso:
    python scripts/medir_repeticao_sql.py [chat_id ...]
"""

import collections
import json
import os
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND = os.environ.get("REGRESSAO_BACKEND", "https://teste-ia-backend-x27vtpiida-uc.a.run.app")
EMAIL = os.environ.get("LICITA_EMAIL", "dono@licita.com")
SENHA = os.environ.get("LICITA_SENHA", "licita-senha-forte-123")


# Dentro de um turno as chamadas saem quase coladas; entre turnos há a espera
# do usuário e a resposta do modelo. 45 s separa os dois casos com folga.
INTERVALO_ENTRE_TURNOS = 45.0


def normalizar(consulta: str) -> str:
    return " ".join(str(consulta or "").split()).lower()


def _instante(valor: str) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def agrupar_por_turno(chamadas: list[dict]) -> list[list[dict]]:
    ordenadas = sorted(chamadas, key=lambda c: _instante(c.get("created_at")))
    turnos: list[list[dict]] = []
    anterior = None
    for chamada in ordenadas:
        agora = _instante(chamada.get("created_at"))
        if anterior is None or agora - anterior > INTERVALO_ENTRE_TURNOS:
            turnos.append([])
        turnos[-1].append(chamada)
        anterior = agora
    return turnos


def main() -> int:
    with httpx.Client(base_url=BACKEND, timeout=300.0) as client:
        token = client.post(
            "/api/auth/login", json={"email": EMAIL, "password": SENHA}
        ).json()["token"]
        cabecalho = {"Authorization": f"Bearer {token}"}

        alvos = sys.argv[1:]
        if not alvos:
            chats = client.get("/api/chats", headers=cabecalho).json()
            chats = chats.get("items", chats) if isinstance(chats, dict) else chats
            alvos = [c["id"] for c in chats[:12]]

        cabecalhos = f"{'chat':<38} {'chamadas':>9} {'no banco':>9} {'do cache':>9}"
        print(cabecalhos)
        print("-" * 76)
        total_sql = total_cache = 0
        for chat_id in alvos:
            chamadas = client.get(f"/api/chats/{chat_id}/tool-calls", headers=cabecalho).json()
            chamadas = (
                chamadas.get("items", chamadas) if isinstance(chamadas, dict) else chamadas
            )
            sql = [c for c in chamadas if c.get("tool") == "run_sql_query"]
            if not sql:
                continue

            servidas_do_cache = sum(
                1 for c in sql if str(c.get("output") or "").lstrip().startswith("AVISO")
            )
            no_banco = len(sql) - servidas_do_cache
            total_sql += len(sql)
            total_cache += servidas_do_cache
            print(f"{chat_id:<38} {len(sql):>9} {no_banco:>9} {servidas_do_cache:>9}")

            # Repetição que o cache NÃO pegou é o que ainda vale investigar.
            for turno in agrupar_por_turno(sql):
                consultas = []
                for c in turno:
                    if str(c.get("output") or "").lstrip().startswith("AVISO"):
                        continue
                    entrada = c.get("input")
                    if isinstance(entrada, str):
                        try:
                            entrada = json.loads(entrada)
                        except ValueError:
                            entrada = {}
                    consultas.append(normalizar((entrada or {}).get("query")))
                comum = collections.Counter(consultas).most_common(1)
                if comum and comum[0][1] > 1:
                    print(
                        f"    ATENÇÃO: {comum[0][1]}x no banco no mesmo turno: "
                        f"{comum[0][0][:70]}"
                    )

        if total_sql:
            print("-" * 76)
            economia = 100 * total_cache / total_sql if total_sql else 0
            print(
                f"{'TOTAL':<38} {total_sql:>9} {total_sql - total_cache:>9} {total_cache:>9}"
                f"   ({economia:.0f}% das chamadas não chegaram ao banco)"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
