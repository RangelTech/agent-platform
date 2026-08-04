"""Recusa rodar a suíte contra um banco que não seja descartável.

A suíte apaga dados: `TRUNCATE tenants, users, user_profiles, sessions CASCADE`
roda entre testes, e o conftest do kernel aplica migrações no banco apontado.
Nada disso pergunta onde está apontando.

O acidente não é hipotético. Diagnosticar produção exige exportar o
`DATABASE_URL` real no shell — foi feito nesta trilha para conferir a migração
0023 — e a suíte é rodada no mesmo shell minutos depois. Entre as duas coisas
não havia nada. Agora há.

O critério é o host: banco de teste roda na máquina de quem testa ou no serviço
do CI. Qualquer host remoto é tratado como produção até que alguém diga o
contrário em voz alta, com `ALLOW_DESTRUCTIVE_TESTS=1`.
"""

import os
from urllib.parse import urlparse

HOSTS_DESCARTAVEIS = {"localhost", "127.0.0.1", "::1", "postgres", "db", ""}
ESCAPE = "ALLOW_DESTRUCTIVE_TESTS"


def exigir_banco_descartavel(dsn: str) -> None:
    """Levanta se o DSN não parece ser de teste. Chamado antes de qualquer
    fixture que escreva no banco."""
    if os.environ.get(ESCAPE) == "1":
        return
    host = (urlparse(dsn).hostname or "").lower()
    if host in HOSTS_DESCARTAVEIS:
        return
    raise RuntimeError(
        f"a suíte apaga dados e o DATABASE_URL aponta para '{host}', que não é "
        "um banco local de teste. Se isto é mesmo um banco descartável, rode com "
        f"{ESCAPE}=1. Se é produção, você acabou de evitar apagar os tenants."
    )
