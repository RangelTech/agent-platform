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
    headers = {}
    try:
        if settings.kernel_audience:
            from app.gcp_auth import id_token_for

            headers["Authorization"] = f"Bearer {await id_token_for(settings.kernel_audience)}"
        elif settings.kernel_internal_token:
            headers["Authorization"] = f"Bearer {settings.kernel_internal_token}"
        async with httpx.AsyncClient(timeout=300.0) as client:
            await client.post(f"{settings.kernel_url}{path}", json=payload, headers=headers)
    except httpx.HTTPError:
        logger.exception("kernel task call failed: %s", path)


def enqueue_kernel_task(path: str, payload: dict) -> None:
    """Schedule POST kernel<path> with the given JSON body."""
    if settings.cloud_tasks_queue:
        from google.cloud import tasks_v2

        client = tasks_v2.CloudTasksClient()
        client.create_task(
            parent=settings.cloud_tasks_queue,
            task={
                "http_request": {
                    "http_method": tasks_v2.HttpMethod.POST,
                    "url": f"{settings.kernel_url}{path}",
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps(payload).encode(),
                    "oidc_token": {
                        "service_account_email": settings.tasks_service_account,
                        "audience": settings.kernel_audience or settings.kernel_url,
                    },
                }
            },
        )
    else:
        # Dev: fire and forget on the running loop.
        asyncio.get_running_loop().create_task(_call_kernel(path, payload))
