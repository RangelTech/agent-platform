"""Contrato kernel de skills -> backend: toda rota que o kernel chama precisa existir aqui.

A lista abaixo espelha `HttpBackend` do repo kernel-harness (harness/backend.py). Se o kernel
passar a chamar outra rota, acrescente-a aqui e implemente-a: foi a ausência dessa conferência
que deixou o Pix (cobranças), os artefatos e a memória sem rota no primeiro deploy.
"""

from app.main import app

KERNEL_CALLS = [
    ("POST", "/internal/runs/{run_id}/usage"),
    ("POST", "/internal/runs/{run_id}/messages"),
    ("POST", "/internal/runs/{run_id}/skills"),
    ("POST", "/internal/runs/{run_id}/memory-proposals"),
    ("GET", "/internal/skills/user/{tenant_id}/{user_id}"),
    ("POST", "/internal/approvals"),
    ("POST", "/internal/runs/{run_id}/artifacts/put"),
    ("POST", "/internal/runs/{run_id}/payments"),
    ("GET", "/internal/payments/charges"),
    ("POST", "/internal/runs/{run_id}/schedules"),
    ("POST", "/internal/search/docs"),
    ("POST", "/internal/memories/recall"),
]


def _registered() -> set[tuple[str, str]]:
    out = set()
    for route in app.routes:
        for method in getattr(route, "methods", None) or ():
            out.add((method, route.path))
    return out


def test_every_route_the_kernel_calls_exists():
    have = _registered()
    missing = [call for call in KERNEL_CALLS if call not in have]
    assert not missing, f"rotas internas ausentes: {missing}"
