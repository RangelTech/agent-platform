"""Async work dispatch: Cloud Tasks in production, fire-and-forget HTTP call
to the kernel in dev. Used where BackgroundTasks isn't available (e.g. inside
streaming generators)."""

import asyncio
import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def _call_kernel(path: str, payload: dict) -> None:
    from app.kernel_router import kernel_headers, target_for_kind

    try:
        # Extração de memórias e afins só existem no fluxo estruturado.
        target = target_for_kind("graph")
        headers = await kernel_headers(target)
        async with httpx.AsyncClient(timeout=300.0) as client:
            await client.post(f"{target.url}{path}", json=payload, headers=headers)
    except httpx.HTTPError:
        logger.exception("kernel task call failed: %s", path)


def enqueue_kernel_task(path: str, payload: dict) -> None:
    """Schedule POST kernel<path> with the given JSON body."""
    from app.kernel_router import target_for_kind

    target = target_for_kind("graph")
    if settings.cloud_tasks_queue:
        from google.cloud import tasks_v2

        client = tasks_v2.CloudTasksClient()
        client.create_task(
            parent=settings.cloud_tasks_queue,
            task={
                "http_request": {
                    "http_method": tasks_v2.HttpMethod.POST,
                    "url": f"{target.url}{path}",
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps(payload).encode(),
                    "oidc_token": {
                        "service_account_email": settings.tasks_service_account,
                        "audience": target.audience or target.url,
                    },
                }
            },
        )
    else:
        # Dev: fire and forget on the running loop.
        asyncio.get_running_loop().create_task(_call_kernel(path, payload))
