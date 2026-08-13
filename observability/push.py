"""Web push notifications for tasks-board attention.

Subscriptions live in the `push_subscriptions` collection — one doc per
browser, unique by endpoint (see api/push_routes.py).

Pushes fire when a board task's conversation leaves "running" (the user's
turn). Callers hook this in fire-and-forget style; every failure is swallowed
into logs so push can never break a chat turn.
"""

import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)

# Statuses that mean the agent stopped mid-flight rather than finishing.
_STATUS_TITLES = {
    "completed": "Task needs your input",
    "error": "Task hit an error",
    "interrupted": "Task was interrupted",
}


def _vapid_config() -> tuple[str, str] | None:
    private_key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    subject = os.environ.get("VAPID_SUBJECT", "").strip()
    if not private_key:
        return None
    return private_key, subject or "mailto:admin@localhost"


def vapid_public_key() -> str | None:
    """Derive the base64url public key for PushManager.subscribe()."""
    config = _vapid_config()
    if not config:
        return None
    try:
        from py_vapid import Vapid
        from py_vapid.utils import b64urlencode
        from cryptography.hazmat.primitives import serialization

        vapid = Vapid.from_string(private_key=config[0])
        raw = vapid.public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        return b64urlencode(raw)
    except Exception as e:
        logger.warning("Push: failed to derive VAPID public key: %s", e)
        return None


def _send_one(subscription: dict, payload: str, private_key: str, subject: str):
    """Blocking single-subscription send — run via asyncio.to_thread."""
    from pywebpush import webpush

    webpush(
        subscription_info=subscription,
        data=payload,
        vapid_private_key=private_key,
        vapid_claims={"sub": subject},
    )


async def send_task_needs_input_push(db, conversation_id: str):
    """Notify a board task's owner that the agent stopped and awaits them."""
    try:
        config = _vapid_config()
        if not config or db is None:
            return
        private_key, subject = config

        conversation = await db.conversations.find_one(
            {"conversation_id": conversation_id},
            {"task_status": 1, "status": 1, "title": 1, "prompt": 1, "metadata.user_name": 1},
        )
        if not conversation or conversation.get("task_status") != "active":
            return
        owner = (conversation.get("metadata") or {}).get("user_name")
        if not owner:
            return

        subscriptions = await db.push_subscriptions.find(
            {"user_email": owner}).to_list(50)
        if not subscriptions:
            return

        status = conversation.get("status") or "completed"
        base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
        payload = json.dumps({
            "title": _STATUS_TITLES.get(status, _STATUS_TITLES["completed"]),
            "body": (conversation.get("title") or conversation.get("prompt") or "")[:120],
            "tag": conversation_id,
            "url": f"{base_url}/chat?continue={conversation_id}",
        })

        from pywebpush import WebPushException

        expired: list[str] = []
        for entry in subscriptions:
            subscription = entry.get("subscription") or {}
            try:
                await asyncio.to_thread(_send_one, subscription, payload, private_key, subject)
            except WebPushException as e:
                status_code = getattr(e.response, "status_code", None)
                if status_code in (404, 410):
                    expired.append(subscription.get("endpoint", ""))
                else:
                    logger.warning("Push: send failed for %s: %s", owner, e)
            except Exception as e:
                logger.warning("Push: send failed for %s: %s", owner, e)

        if expired:
            await db.push_subscriptions.delete_many(
                {"subscription.endpoint": {"$in": expired}},
            )
    except Exception as e:
        logger.warning("Push: notification for %s failed: %s", conversation_id, e)


def fire_task_push(db, conversation_id: str):
    """Fire-and-forget wrapper safe to call from status-transition paths."""
    try:
        asyncio.create_task(send_task_needs_input_push(db, conversation_id))
    except RuntimeError:
        # No running loop (e.g. sync shutdown path) — skip.
        pass


async def send_user_push(db, user_email: str, *, title: str, body: str = "",
                         url: str = "", tag: str = ""):
    """Send a web push to every browser a user has subscribed.

    Generic sibling of send_task_needs_input_push — used by the notification
    inbox so a new inbox notification also reaches the user's lock screen.
    All failures are swallowed into logs.
    """
    try:
        config = _vapid_config()
        if not config or db is None or not user_email:
            return
        private_key, subject = config

        subscriptions = await db.push_subscriptions.find(
            {"user_email": user_email}).to_list(50)
        if not subscriptions:
            return

        payload = json.dumps({
            "title": title,
            "body": (body or "")[:120],
            "tag": tag or title,
            "url": url,
        })

        from pywebpush import WebPushException

        expired: list[str] = []
        for entry in subscriptions:
            subscription = entry.get("subscription") or {}
            try:
                await asyncio.to_thread(_send_one, subscription, payload, private_key, subject)
            except WebPushException as e:
                status_code = getattr(e.response, "status_code", None)
                if status_code in (404, 410):
                    expired.append(subscription.get("endpoint", ""))
                else:
                    logger.warning("Push: send failed for %s: %s", user_email, e)
            except Exception as e:
                logger.warning("Push: send failed for %s: %s", user_email, e)

        if expired:
            await db.push_subscriptions.delete_many(
                {"subscription.endpoint": {"$in": expired}},
            )
    except Exception as e:
        logger.warning("Push: user push for %s failed: %s", user_email, e)


def fire_user_push(db, user_email: str, *, title: str, body: str = "",
                   url: str = "", tag: str = ""):
    """Fire-and-forget wrapper around send_user_push."""
    try:
        asyncio.create_task(
            send_user_push(db, user_email, title=title, body=body, url=url, tag=tag)
        )
    except RuntimeError:
        # No running loop (e.g. sync shutdown path) — skip.
        pass
