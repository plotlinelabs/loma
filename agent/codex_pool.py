"""Account pool for pre-warmed Codex (ChatGPT subscription) workers.

Mirrors agent/pool.py one-to-one for the Codex runtime:

- Each team member connects their own ChatGPT/Codex subscription from the
  Integrations page; credentials live in per-user ``CODEX_HOME`` dirs under
  ``CODEX_USERS_DIR`` (``auth.json`` per account).
- A bounded pool (default 3, ``CODEX_POOL_SIZE``) of pre-warmed
  ``codex app-server`` workers is kept hot; accounts are assigned round-robin,
  capped at 3 workers per account.
- Workers are single-use: discarded after each conversation (no context
  leakage), with a replacement warmed in the background.
- Rate-limited accounts go on cooldown (default 30 min — ChatGPT plans use
  5-hour/weekly usage windows, so this is longer than the Claude pool's 5 min;
  an adaptive override from Codex rate-limit events takes precedence).
  Auth errors use a 1-hour cooldown; the account is re-admitted early when
  ``auth.json``'s mtime changes (token refresh or re-login).
"""

import asyncio
import logging
import os
import time
from pathlib import Path

from agent.codex_runtime import CodexWorker, read_codex_auth
from agent.pool import ClientPool
from agent.prompt import build_pooled_system_prompt

logger = logging.getLogger(__name__)

# Module-level pool singleton
_codex_pool: "CodexClientPool | None" = None

_DEFAULTS = {
    "CODEX_POOL_SIZE": 3,
    "CODEX_CONNECT_TIMEOUT": 90,
    "CODEX_QUEUE_TIMEOUT": 600,
}


def _env_int(key: str) -> int:
    return int(os.environ.get(key, str(_DEFAULTS[key])))


WARM_RETRIES = 3
WARM_RETRY_DELAY = 5
WARM_RECOVERY_DELAY = 30

# ChatGPT plans meter usage in 5-hour + weekly windows, so exhausted accounts
# rarely self-heal in 5 minutes. Default cooldown is 30 min; adaptive
# cooldowns from Codex rate-limit events override this when available.
CODEX_ACCOUNT_COOLDOWN_SECONDS = int(os.environ.get("CODEX_ACCOUNT_COOLDOWN_SECONDS", "1800"))
CODEX_AUTH_COOLDOWN_SECONDS = 3600


def _get_codex_users_dir() -> Path:
    return Path(os.environ.get("CODEX_USERS_DIR", "/opt/codex-users"))


def codex_pool_enabled() -> bool:
    return os.environ.get("CODEX_POOL_ENABLED", "").lower() in {"1", "true", "yes"}


def get_codex_pool() -> "CodexClientPool":
    """Get the global Codex pool. Raises if not initialized."""
    if _codex_pool is None:
        raise RuntimeError("Codex pool not initialized. Set CODEX_POOL_ENABLED=1 and restart.")
    return _codex_pool


async def init_codex_pool(config: dict, pool_size: int | None = None):
    """Initialize the global Codex pool and start background warmup."""
    global _codex_pool
    size = pool_size or _env_int("CODEX_POOL_SIZE")
    _codex_pool = CodexClientPool(pool_size=size)
    _codex_pool.set_config(config)
    asyncio.create_task(_codex_pool.warmup())
    logger.info("Codex pool created (size=%d), warming in background...", size)


async def shutdown_codex_pool():
    global _codex_pool
    if _codex_pool is not None:
        await _codex_pool.shutdown()
        _codex_pool = None


class CodexClientPool:
    """Bounded pool of pre-warmed CodexWorker instances (round-robin accounts)."""

    def __init__(self, pool_size: int = 3):
        self._pool_size = pool_size
        self._available: asyncio.Queue[CodexWorker] = asyncio.Queue()
        self._config: dict | None = None
        self._closed = False
        self._warming = 0
        self._in_use = 0
        self._queue_depth = 0

        self._accounts: list[dict] = []
        self._rr_index: int = 0
        self._account_cooldowns: dict[str, float] = {}
        # email -> auth.json mtime at the moment of the auth failure; the
        # account is re-admitted early once the file is rewritten (refresh or
        # re-login).
        self._auth_failed_mtimes: dict[str, float] = {}
        self._available_models: list[dict] = []

    def set_config(self, config: dict):
        self._config = config

    def _mcp_servers(self) -> dict:
        return (self._config or {}).get("mcp_servers", {})

    async def reload_config(self, config: dict):
        """Drain idle workers after an MCP config change and re-warm."""
        old_servers = set(self._mcp_servers().keys()) if self._config else set()
        self._config = config
        new_servers = set(config.get("mcp_servers", {}).keys())
        if old_servers == new_servers:
            logger.info("Codex pool reload: MCP servers unchanged, skipping")
            return
        drained = await self._drain_idle()
        logger.info("Codex pool reload: drained %d idle workers, re-warming...", drained)
        asyncio.create_task(self.warmup())

    async def reload_prompt(self):
        """Drain idle workers so replacements pick up the latest system prompt."""
        drained = await self._drain_idle()
        logger.info("Codex prompt reload: drained %d idle workers", drained)
        if drained > 0:
            asyncio.create_task(self.warmup())

    async def _drain_idle(self) -> int:
        drained = 0
        while not self._available.empty():
            try:
                worker = self._available.get_nowait()
                await self.safe_disconnect(worker)
                drained += 1
            except asyncio.QueueEmpty:
                break
        return drained

    # ── Account scanning ──────────────────────────────────────────────

    def _scan_accounts(self, disabled_emails: set[str] | None = None):
        """Scan CODEX_USERS_DIR for accounts with a valid auth.json.

        Excludes dirs whose owner email is in disabled_emails (admin
        kill-switch: users.codex_pool_enabled == false in MongoDB).
        """
        users_dir = _get_codex_users_dir()
        accounts: list[dict] = []
        disabled = disabled_emails or set()

        if not users_dir.exists():
            logger.info("No CODEX_USERS_DIR at %s — Codex pool will be empty", users_dir)
            self._accounts = accounts
            return

        for entry in users_dir.iterdir():
            if not entry.is_dir():
                continue
            auth = read_codex_auth(entry)
            if auth is None:
                continue
            if entry.name in disabled:
                logger.info("Skipping Codex account %s (pool disabled by admin)", entry.name)
                continue
            accounts.append({
                "email": entry.name,
                "codex_email": auth.get("email"),
                "plan": auth.get("plan"),
                "config_dir": str(entry),
            })

        self._accounts = accounts
        valid_emails = {a["email"] for a in accounts}
        self._account_cooldowns = {
            k: v for k, v in self._account_cooldowns.items() if k in valid_emails
        }
        self._auth_failed_mtimes = {
            k: v for k, v in self._auth_failed_mtimes.items() if k in valid_emails
        }
        logger.info("Codex account scan: %d accounts found (disabled=%d): %s",
                    len(accounts), len(disabled), [a["email"] for a in accounts])

    def _auth_mtime(self, email: str) -> float | None:
        auth_path = _get_codex_users_dir() / email / "auth.json"
        try:
            return auth_path.stat().st_mtime
        except OSError:
            return None

    def _next_account(self) -> dict | None:
        """Round-robin over accounts, skipping cooldowns.

        Auth-failed accounts are re-admitted early if auth.json was rewritten
        since the failure (the Codex CLI refreshes tokens in place).
        """
        if not self._accounts:
            return None

        now = time.time()
        for email, failed_mtime in list(self._auth_failed_mtimes.items()):
            current = self._auth_mtime(email)
            if current is not None and current != failed_mtime:
                logger.info("Codex account %s auth.json changed — re-admitting to pool", email)
                self._auth_failed_mtimes.pop(email, None)
                self._account_cooldowns.pop(email, None)

        self._account_cooldowns = {
            k: v for k, v in self._account_cooldowns.items() if v > now
        }

        n = len(self._accounts)
        for _ in range(n):
            account = self._accounts[self._rr_index % n]
            self._rr_index = (self._rr_index + 1) % n
            if account["email"] not in self._account_cooldowns:
                return account

        logger.warning("All %d Codex accounts are on cooldown", n)
        return None

    def mark_account_exhausted(self, email: str, auth_error: bool = False,
                               cooldown_override: int | None = None):
        """Cooldown an account after a usage-limit, billing, or auth error."""
        if not email:
            return
        if auth_error:
            cooldown = CODEX_AUTH_COOLDOWN_SECONDS
            mtime = self._auth_mtime(email)
            if mtime is not None:
                self._auth_failed_mtimes[email] = mtime
        else:
            cooldown = cooldown_override or CODEX_ACCOUNT_COOLDOWN_SECONDS
        self._account_cooldowns[email] = time.time() + cooldown
        logger.warning("Codex account %s marked exhausted, cooldown for %ds%s",
                       email, cooldown, " (auth error)" if auth_error else "")

    async def _get_disabled_emails(self) -> set[str]:
        """Query MongoDB for users where codex_pool_enabled is explicitly false."""
        try:
            from observability.db import get_db
            db = get_db()
            if db is None:
                return set()
            cursor = db.users.find({"codex_pool_enabled": False}, {"email": 1})
            docs = await cursor.to_list(200)
            return {doc["email"] for doc in docs if "email" in doc}
        except Exception as e:
            logger.warning("Failed to query disabled Codex pool accounts: %s", e)
            return set()

    def refresh_accounts(self):
        """Re-scan accounts. Called when a user connects/disconnects Codex."""
        asyncio.create_task(self._async_refresh_accounts())

    async def _async_refresh_accounts(self):
        disabled = await self._get_disabled_emails()
        self._scan_accounts(disabled_emails=disabled)
        current = self._available.qsize() + self._warming + self._in_use
        if not self._closed and self._accounts and current < self._pool_size:
            deficit = self._pool_size - current
            logger.info("Codex account refresh: warming %d additional workers", deficit)
            for _ in range(deficit):
                asyncio.create_task(self._warm_one())

    # ── Pool warmup ───────────────────────────────────────────────────

    async def warmup(self):
        """Pre-warm the pool with workers (run as background task)."""
        disabled = await self._get_disabled_emails()
        self._scan_accounts(disabled_emails=disabled)
        if not self._accounts:
            logger.info("No Codex accounts connected — pool will be empty until users log in")
            return

        target = min(self._pool_size, len(self._accounts) * 3)  # cap at 3 per account
        logger.info("Starting Codex pool warmup (%d workers across %d accounts)...",
                    target, len(self._accounts))
        tasks = []
        for _ in range(target):
            account = self._next_account()
            if account is None:
                break
            tasks.append(self._create_worker(account))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        success = 0
        for result in results:
            if isinstance(result, Exception):
                logger.error("Failed to warm Codex worker: %s", result)
            else:
                await self._available.put(result)
                success += 1
        logger.info("Codex pool warmup complete: %d/%d workers ready", success, len(tasks))

        failures = len(tasks) - success
        if failures > 0:
            for _ in range(failures):
                asyncio.create_task(self._warm_one())

    async def _create_worker(self, account: dict, model_override: str | None = None) -> CodexWorker:
        """Create and connect a new CodexWorker for a specific account."""
        from agent.codex_runtime import CodexAuthError, CodexRateLimitError

        self._warming += 1
        worker = None
        try:
            worker = CodexWorker(account, model=model_override)
            await asyncio.wait_for(
                worker.connect(
                    mcp_servers=self._mcp_servers(),
                    system_prompt=build_pooled_system_prompt(),
                ),
                timeout=_env_int("CODEX_CONNECT_TIMEOUT"),
            )
            if model_override:
                worker._pool_ephemeral = True
            if worker.available_models:
                self._available_models = worker.available_models
            return worker
        except (asyncio.TimeoutError, Exception) as e:
            if isinstance(e, asyncio.TimeoutError):
                logger.error("Codex worker connect timed out after %ds for account %s",
                             _env_int("CODEX_CONNECT_TIMEOUT"), account["email"])
            elif isinstance(e, CodexAuthError):
                self.mark_account_exhausted(account["email"], auth_error=True)
            elif isinstance(e, CodexRateLimitError):
                self.mark_account_exhausted(account["email"],
                                            cooldown_override=e.resets_in_seconds)
            if worker is not None:
                await self.safe_disconnect(worker)
            raise
        finally:
            self._warming -= 1

    async def acquire(self, model: str | None = None) -> CodexWorker:
        """Get a warm worker from the pool (bounded; queues when all busy).

        Non-default model selections get an ephemeral one-off worker, same as
        the Claude pool's model-override path.
        """
        from agent.codex_runtime import default_codex_model

        if self._closed:
            raise RuntimeError("Codex pool is closed")
        if not self._accounts:
            raise RuntimeError(
                "No Codex accounts connected. Ask a team member to log in via Integrations."
            )

        requested_model = model or default_codex_model()
        if requested_model != default_codex_model():
            account = self._next_account()
            if account is None:
                raise RuntimeError("No Codex accounts are currently available for the selected model.")
            self._in_use += 1
            logger.info("Creating one-off Codex worker for model=%s (account=%s)",
                        requested_model, account["email"])
            try:
                return await self._create_worker(account, model_override=requested_model)
            except Exception:
                self._in_use = max(0, self._in_use - 1)
                raise

        try:
            worker = self._available.get_nowait()
            self._in_use += 1
            logger.info("Acquired warm Codex worker (account=%s, available=%d, in_use=%d)",
                        worker.account.get("email"), self._available.qsize(), self._in_use)
            return worker
        except asyncio.QueueEmpty:
            pass

        self._queue_depth += 1
        logger.info("Codex pool empty — request queued (queue_depth=%d, warming=%d, in_use=%d)",
                    self._queue_depth, self._warming, self._in_use)
        try:
            worker = await asyncio.wait_for(
                self._available.get(), timeout=_env_int("CODEX_QUEUE_TIMEOUT")
            )
            self._in_use += 1
            return worker
        except asyncio.TimeoutError:
            logger.error("Codex queue timeout after %ds (warming=%d, in_use=%d)",
                         _env_int("CODEX_QUEUE_TIMEOUT"), self._warming, self._in_use)
            raise
        finally:
            self._queue_depth -= 1

    async def release(self, worker: CodexWorker):
        """Discard a used worker and warm a replacement (single-use workers)."""
        self._in_use = max(0, self._in_use - 1)
        if getattr(worker, "_pool_ephemeral", False):
            asyncio.create_task(self.safe_disconnect(worker))
            return
        asyncio.create_task(self._disconnect_then_warm(worker))

    async def _disconnect_then_warm(self, worker: CodexWorker):
        await self.safe_disconnect(worker)
        if not self._closed and self._accounts and (self._available.qsize() + self._warming) < self._pool_size:
            await self._warm_one()

    async def safe_disconnect(self, worker: CodexWorker):
        """Disconnect a worker; force-kill its process tree if it hangs."""
        pid = worker.pid
        try:
            await asyncio.wait_for(worker.disconnect(), timeout=30)
        except (Exception, asyncio.TimeoutError) as e:
            logger.warning("Codex worker disconnect failed/timed out: %s — force-killing", e)
        if pid:
            # Reuse the Claude pool's process-tree killer (kills MCP children too)
            ClientPool._kill_process_tree(pid)

    async def _warm_one(self):
        """Warm a single replacement worker in the background with retries."""
        if self._closed:
            return
        if (self._available.qsize() + self._warming) >= self._pool_size:
            return
        account = self._next_account()
        if account is None:
            logger.warning("Cannot warm Codex replacement — no accounts available")
            return
        for attempt in range(1, WARM_RETRIES + 1):
            if self._closed:
                return
            try:
                worker = await self._create_worker(account)
                if not self._closed:
                    await self._available.put(worker)
                    logger.info("Replacement Codex worker warmed (account=%s, available=%d)",
                                account["email"], self._available.qsize())
                else:
                    await self.safe_disconnect(worker)
                return
            except Exception as e:
                logger.error("Codex warm attempt %d/%d failed for account %s: %s",
                             attempt, WARM_RETRIES, account["email"], e)
                if attempt < WARM_RETRIES:
                    await asyncio.sleep(WARM_RETRY_DELAY * attempt)

        logger.error("All %d Codex warm attempts failed for account %s", WARM_RETRIES, account["email"])
        if not self._closed and (self._available.qsize() + self._warming + self._in_use) < self._pool_size:
            await asyncio.sleep(WARM_RECOVERY_DELAY)
            if not self._closed:
                asyncio.create_task(self._warm_one())

    # ── Status & lifecycle ────────────────────────────────────────────

    def _account_distribution(self) -> dict[str, int]:
        dist: dict[str, int] = {}
        for worker in list(self._available._queue):  # type: ignore[attr-defined]
            email = worker.account.get("email", "unknown")
            dist[email] = dist.get(email, 0) + 1
        return dist

    def status(self) -> dict:
        """Return pool status for the /api/pool-status endpoint."""
        now = time.time()
        return {
            "enabled": True,
            "pool_size": self._pool_size,
            "available": self._available.qsize(),
            "in_use": self._in_use,
            "warming": self._warming,
            "queue_depth": self._queue_depth,
            "accounts": [a["email"] for a in self._accounts],
            "accounts_on_cooldown": [
                email for email, expires in self._account_cooldowns.items() if expires > now
            ],
            "accounts_auth_failed": sorted(self._auth_failed_mtimes.keys()),
            "account_distribution": self._account_distribution(),
            "models": self._available_models,
        }

    @property
    def available_count(self) -> int:
        return self._available.qsize()

    async def shutdown(self):
        self._closed = True
        while not self._available.empty():
            try:
                worker = self._available.get_nowait()
                await self.safe_disconnect(worker)
            except asyncio.QueueEmpty:
                break
        logger.info("Codex pool shut down")
