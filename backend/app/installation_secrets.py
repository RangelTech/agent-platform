"""Resolvedor de segredo de instalação: banco primeiro, ambiente depois.

O código nunca lê `os.environ` direto para um segredo de instalação. Ele
pergunta aqui, e a ordem de resolução é o que define o custo de trocar de
nuvem: enquanto a variável de ambiente vencer, um valor velho esquecido numa
revisão do Cloud Run continua mandando e ninguém entende por que a chave nova
"não pegou".

O ambiente permanece como saída de emergência — e como único caminho para o
bootstrap (`DATABASE_URL`, `ENCRYPTION_KEY`), que não pode morar no banco que
ele mesmo destranca.
"""

import logging
import os

from app.crypto import decrypt
from app.db import get_connection

logger = logging.getLogger(__name__)

# Segredos que NUNCA podem vir do banco, por definição: são lidos antes de
# existir conexão ou cifragem. Listados para que uma tentativa de cadastrá-los
# na tela seja recusada em vez de criar um valor que nada lê.
SOMENTE_AMBIENTE = {"DATABASE_URL", "ENCRYPTION_KEY", "SECRET_KEY_BASE"}


def resolver(nome: str, padrao: str = "") -> str:
    """Valor do segredo: banco, depois ambiente, depois o padrão."""
    if nome not in SOMENTE_AMBIENTE:
        try:
            with get_connection() as conn:
                linha = conn.execute(
                    """SELECT value_encrypted FROM installation_secrets
                        WHERE name = %s AND is_active""",
                    (nome,),
                ).fetchone()
            if linha:
                return decrypt(linha["value_encrypted"])
        except Exception:  # noqa: BLE001
            # Banco fora do ar não pode derrubar quem só queria uma chave de
            # API: cair para o ambiente é degradar, não falhar.
            logger.exception("falha ao ler o segredo %s do banco", nome)
    return os.environ.get(nome) or padrao
