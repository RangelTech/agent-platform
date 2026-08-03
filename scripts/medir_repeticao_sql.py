"""Mede consulta repetida DENTRO de um turno.

O número que importa não é "quantas consultas": é quantas foram **a mesma**
dentro do mesmo turno. Antes do cache de leitura um único turno chegou a 45
consultas, várias byte a byte idênticas, e nada apontava isso porque cada uma
retornava com sucesso.

Dois cuidados para o número não mentir:

- **Medir a conversa inteira dá o número errado**: o cache vive no turno, então
  a mesma consulta em turnos diferentes é legítima — a resposta pode ter
  mudado. O turno começa em cada mensagem do usuário, e é isso que separamos.
  Separar por intervalo de tempo não serve: medido no harness, a pergunta
  seguinte entra **1,1 s** depois da resposta anterior, então qualquer corte por
  espera junta a conversa inteira num turno só e acusa repetição que não houve.
- **Chamada repetida não é consulta repetida**: quando o cache atende, a
  ferramenta ainda aparece no registro, mas o banco não foi tocado. O que
  interessa é quanto chegou ao banco.
- **Chamada que falhou também não tocou o banco**: o modelo erra o nome da
  fonte, leva `ERRO: fonte ... não existe` e reescreve a mesma consulta. Contar
  isso como ida ao banco fabrica repetição que não existe — foi o que fez esta
  medição acusar "3x no mesmo turno" onde o cache tinha funcionado.

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


def _origem(chamada: dict) -> str:
    """"cache", "erro" ou "banco" — só a última custou consulta."""
    saida = str(chamada.get("output") or "").lstrip()
    if saida.startswith("AVISO"):
        return "cache"
    if saida.startswith("ERRO") or chamada.get("status") not in ("ok", None):
        return "erro"
    return "banco"


def normalizar(consulta: str) -> str:
    return " ".join(str(consulta or "").split()).lower()


def _instante(valor: str) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def agrupar_por_turno(chamadas: list[dict], inicios: list[float]) -> list[list[dict]]:
    """Cada chamada pertence à última pergunta do usuário feita antes dela.

    `inicios` são os instantes das mensagens do usuário — o começo real de cada
    turno, que é o escopo do cache de leitura.
    """
    ordenadas = sorted(chamadas, key=lambda c: _instante(c.get("created_at")))
    marcos = sorted(inicios)
    turnos: list[list[dict]] = [[] for _ in marcos] or [[]]
    for chamada in ordenadas:
        agora = _instante(chamada.get("created_at"))
        indice = 0
        for i, marco in enumerate(marcos):
            if marco <= agora:
                indice = i
        turnos[indice].append(chamada)
    return [t for t in turnos if t]


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

        cabecalhos = (
            f"{'chat':<38} {'chamadas':>9} {'no banco':>9} {'do cache':>9} {'erro':>6}"
        )
        print(cabecalhos)
        print("-" * 84)
        total_sql = total_cache = total_erro = 0
        for chat_id in alvos:
            chamadas = client.get(f"/api/chats/{chat_id}/tool-calls", headers=cabecalho).json()
            chamadas = (
                chamadas.get("items", chamadas) if isinstance(chamadas, dict) else chamadas
            )
            sql = [c for c in chamadas if c.get("tool") == "run_sql_query"]
            if not sql:
                continue

            servidas_do_cache = sum(1 for c in sql if _origem(c) == "cache")
            falharam = sum(1 for c in sql if _origem(c) == "erro")
            no_banco = len(sql) - servidas_do_cache - falharam
            total_sql += len(sql)
            total_cache += servidas_do_cache
            total_erro += falharam
            print(
                f"{chat_id:<38} {len(sql):>9} {no_banco:>9} {servidas_do_cache:>9} "
                f"{falharam:>6}"
            )

            mensagens = client.get(
                f"/api/chats/{chat_id}/messages", headers=cabecalho
            ).json()
            mensagens = (
                mensagens.get("items", mensagens)
                if isinstance(mensagens, dict)
                else mensagens
            )
            inicios = [
                _instante(m.get("created_at"))
                for m in mensagens
                if m.get("role") == "user"
            ]

            # Repetição que o cache NÃO pegou é o que ainda vale investigar.
            for turno in agrupar_por_turno(sql, inicios):
                consultas = []
                for c in turno:
                    if _origem(c) != "banco":
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
            print("-" * 84)
            poupadas = total_cache + total_erro
            economia = 100 * total_cache / total_sql
            print(
                f"{'TOTAL':<38} {total_sql:>9} {total_sql - poupadas:>9} "
                f"{total_cache:>9} {total_erro:>6}"
            )
            print(f"cache evitou {economia:.0f}% das chamadas de leitura")
    return 0


if __name__ == "__main__":
    sys.exit(main())
