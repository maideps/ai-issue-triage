"""GitHub webhook receiver for live mode (feat-016)."""

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from issue_triage.domain.publishing import valid_signature

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/github")
async def github_webhook(request: Request) -> dict[str, Any]:
    secret = request.app.state.settings.webhook_secret
    if secret is None:
        raise HTTPException(503, "webhooks are not configured")
    body = await request.body()
    if not valid_signature(
        secret.get_secret_value(), body, request.headers.get("X-Hub-Signature-256")
    ):
        raise HTTPException(401, "invalid signature")
    provider = request.app.state.live_provider
    if provider is None:
        raise HTTPException(503, "live mode is not configured")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise HTTPException(400, "body is not JSON") from error
    event = request.headers.get("X-GitHub-Event", "")
    result = await run_in_threadpool(provider().handle_webhook, event, payload)
    return {"result": result}
