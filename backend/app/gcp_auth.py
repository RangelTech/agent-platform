"""ID-token acquisition for service-to-service calls on Cloud Run.

When KERNEL_AUDIENCE is set the backend fetches an OIDC identity token from
the metadata server and sends it as the Bearer to the kernel, which runs with
--no-allow-unauthenticated. Off-GCP (dev) this module is simply unused.
"""

import time

import httpx

_METADATA_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/identity"
)

_cache: dict[str, tuple[str, float]] = {}


async def id_token_for(audience: str) -> str:
    """Return a cached ID token for `audience`, refreshing near expiry.
    Tokens last 1h; refresh after 50min."""
    cached = _cache.get(audience)
    now = time.monotonic()
    if cached and now - cached[1] < 3000:
        return cached[0]

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            _METADATA_URL,
            params={"audience": audience},
            headers={"Metadata-Flavor": "Google"},
        )
        response.raise_for_status()
        token = response.text

    _cache[audience] = (token, now)
    return token
