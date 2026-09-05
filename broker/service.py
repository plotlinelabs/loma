"""Run-scoped, opaque capabilities for explicit broker operations.

Issuance and revocation are trusted controller APIs, deliberately NOT HTTP routes.
The deployment is the tenant boundary in this single-tenant application. A worker
cannot supply a principal, tenant, provider destination, or credential identifier.
"""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone


class Denied(Exception):
    """Invalid capability, inactive account, or insufficient scope."""


class Broker:
    def __init__(self, db, deployment_id, operations):
        if db is None or not isinstance(deployment_id, str) or not deployment_id.strip():
            raise ValueError("Broker requires a database and deployment identity")
        self.db = db
        self.deployment_id = deployment_id
        self.operations = dict(operations)

    async def initialize(self):
        # TTL is cleanup only. Every admission checks the deadline explicitly.
        await self.db.execution_capabilities.create_index("expires_at", expireAfterSeconds=0)

    async def _active(self, email):
        user = await self.db.users.find_one({"email": email})
        if not user or user.get("status") != "active":
            raise Denied()

    async def issue(self, *, user_email, grants, ttl_seconds=300, max_calls=20):
        """Called ONLY by the trusted controller after authenticating the user.

        Grants must come from controller policy/user approval, never model output.
        Returns (run_id, opaque_capability); store only its hash in MongoDB.
        Each issuance creates a new run, so resumed workers need fresh grants.
        """
        if (not isinstance(user_email, str) or not user_email
                or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 7200
                or type(max_calls) is not int or not 1 <= max_calls <= 1000
                or not isinstance(grants, dict) or not grants or len(grants) > 20):
            raise ValueError("Invalid execution grant")
        await self._active(user_email)
        scopes = []
        for name, resources in grants.items():
            operation = self.operations.get(name)
            if (operation is None or not isinstance(resources, list)
                    or not 1 <= len(resources) <= 100):
                raise ValueError("Invalid operation grant")
            for resource in resources:
                if not operation.valid_resource(resource):
                    raise ValueError("Invalid resource grant")
                scopes.append({"operation": name, "resource": resource})
        token = "loma_run_v1_" + secrets.token_urlsafe(32)
        run_id = secrets.token_hex(16)
        await self.db.execution_capabilities.insert_one({
            "_id": self._digest(token), "deployment_id": self.deployment_id,
            "run_id": run_id, "user_email": user_email, "scopes": scopes,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
            "remaining_calls": max_calls, "revoked": False,
        })
        return run_id, token

    @staticmethod
    def _digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    async def revoke(self, run_id):
        await self.db.execution_capabilities.update_many(
            {"run_id": run_id, "deployment_id": self.deployment_id},
            {"$set": {"revoked": True}},
        )

    async def execute(self, token, operation_name, resource, params=None):
        if (not isinstance(token, str)
                or not re.fullmatch(r"loma_run_v1_[A-Za-z0-9_-]{43}", token)
                or not isinstance(operation_name, str)):
            raise Denied()
        operation = self.operations.get(operation_name)
        if operation is None or not operation.valid_resource(resource):
            raise Denied()
        # Params are only defined for operations that declare support; any
        # attempt to smuggle arguments into a param-less operation is denied.
        if params is not None and not getattr(operation, "accepts_params", False):
            raise Denied()
        # Atomic admission: concurrent workers cannot overspend the call budget.
        # Failed calls consume budget too; no refunds/retries that can evade limits.
        grant = await self.db.execution_capabilities.find_one_and_update({
            "_id": self._digest(token), "deployment_id": self.deployment_id,
            "revoked": False, "expires_at": {"$gt": datetime.now(timezone.utc)},
            "remaining_calls": {"$gt": 0},
            "scopes": {"$elemMatch": {"operation": operation_name, "resource": resource}},
        }, {"$inc": {"remaining_calls": -1}})
        if not grant:
            raise Denied()
        # No cached approval: account removal/status changes apply to every call.
        await self._active(grant["user_email"])
        # Persist admission before I/O. A failed audit write prevents provider I/O.
        # Never store arguments, capabilities, provider responses or exception text.
        await self.db.execution_audit.insert_one({
            "deployment_id": self.deployment_id, "run_id": grant["run_id"],
            "operation": operation_name, "event": "admitted",
            "at": datetime.now(timezone.utc),
        })
        if getattr(operation, "accepts_params", False):
            return await operation.execute(self.db, grant["user_email"], resource, params)
        return await operation.execute(self.db, grant["user_email"], resource)
