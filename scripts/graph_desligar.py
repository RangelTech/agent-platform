"""Desliga (ou reativa) os agentes `graph` de uma empresa, com backup. Nada é apagado.

Uso (dentro do container do motor, ou com DATABASE_URL no ambiente e backend/ no PYTHONPATH):

    python scripts/graph_desligar.py --empresa hamburgueria-demo --simular
    python scripts/graph_desligar.py --empresa hamburgueria-demo --backup-dir /srv/backups/graph
    python scripts/graph_desligar.py --empresa hamburgueria-demo --reativar

Recusa (código 3) se houver caixa de entrada ou rotina ativa apontando para template
`structured`, a menos que se passe --forcar. Veja docs/runbooks/desligar-graph.md.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.graph_shutdown import Bloqueio, desligar, reativar  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--empresa", required=True, help="tenant_key da empresa")
    ap.add_argument("--backup-dir", default="./graph-backups")
    ap.add_argument(
        "--simular", action="store_true", help="faz o backup e verifica, sem ligar a quarentena"
    )
    ap.add_argument(
        "--forcar", action="store_true", help="ignora caixas/rotinas apontando para graph"
    )
    ap.add_argument("--reativar", action="store_true")
    args = ap.parse_args()

    try:
        if args.reativar:
            out = reativar(args.empresa)
        else:
            out = desligar(
                args.empresa, Path(args.backup_dir), forcar=args.forcar, simular=args.simular
            )
    except Bloqueio as exc:
        print(f"RECUSADO: {exc}", file=sys.stderr)
        return 3
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
