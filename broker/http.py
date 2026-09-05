"""Dedicated worker-facing app. Do not mount on the public backend router.

Deploy behind a private TLS/mTLS gateway with per-worker network admission and
rate limits. No mint/revoke routes and no X-User-Email or browser authentication.
"""
import asyncio
import json

from aiohttp import web

from broker.service import Denied
from utils.secret_redaction import redact_secrets


def create_app(broker):
    async def invoke(request):
        try:
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                raise Denied()
            token = auth[7:]
            body = await request.json()
            if (not isinstance(body, dict)
                    or not {"operation", "resource"} <= set(body)
                    or set(body) - {"operation", "resource", "params"}):
                return web.json_response({"error": "Invalid request"}, status=400)
            result = await asyncio.wait_for(broker.execute(
                token, body["operation"], body["resource"], body.get("params"),
            ), timeout=240)
            # Capabilities must not reappear in provider-generated output.
            encoded = json.dumps(redact_secrets(result), allow_nan=False).replace(token, "[REDACTED]")
            return web.Response(text=encoded, content_type="application/json",
                                headers={"Cache-Control": "no-store"})
        except Denied:
            return web.json_response({"error": "Access denied"}, status=403)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "Invalid request"}, status=400)
        except web.HTTPRequestEntityTooLarge:
            return web.json_response({"error": "Request too large"}, status=413)
        except Exception:
            # Never relay/log exception strings: they may include URLs or tokens.
            return web.json_response({"error": "Broker unavailable"}, status=503)

    # Sized for tool invocations that upload small workspace files inline.
    app = web.Application(client_max_size=2 * 1024 * 1024)
    app.router.add_post("/v1/invoke", invoke)
    return app
