"""
Client pool for pre-warming Claude SDK clients.

Each ClaudeSDKClient wraps a Claude Code CLI subprocess with MCP servers
already initialized (~30s cold start). The pool pre-warms N clients at
server startup so most queries get an instant start.

After a conversation finishes, the client is discarded (to prevent context
leakage between conversations) and a replacement warms in the background.

The pool is **bounded**: at most pool_size clients exist at any time.
If all clients are in use, new requests queue and wait for one to be released
instead of creating on-demand clients that could exceed memory limits.

Accounts are assigned to clients in round-robin from all connected Claude
accounts (users who logged in via the integrations page). If an account
hits a rate limit, it's put on cooldown and skipped for future warm cycles.
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

from agent.prompt import build_pooled_system_prompt

logger = logging.getLogger(__name__)

# Module-level pool singleton
_pool: "ClientPool | None" = None

# Defaults — env vars are read lazily via _env_int() so that load_dotenv()
# in app.py has a chance to populate os.environ before we read them.
_DEFAULTS = {
    "AGENT_POOL_SIZE": 3,
    "AGENT_CONNECT_TIMEOUT": 90,
    "AGENT_QUEUE_TIMEOUT": 600,  # 10 min — lets webhooks patiently wait for a slot
}


def _env_int(key: str) -> int:
    """Read an env var at call time (not import time) with a default."""
    return int(os.environ.get(key, str(_DEFAULTS[key])))

# Max retries when warming a replacement client
WARM_RETRIES = 3
WARM_RETRY_DELAY = 5  # seconds between retries
WARM_RECOVERY_DELAY = 30  # seconds before retrying the whole warm cycle after total failure

# Account cooldown when rate-limited
ACCOUNT_COOLDOWN_SECONDS = 300  # 5 minutes


def _get_claude_users_dir() -> Path:
    return Path(os.environ.get("CLAUDE_USERS_DIR", "/opt/claude-users"))


def get_pool() -> "ClientPool":
    """Get the global client pool. Raises if not initialized."""
    if _pool is None:
        raise RuntimeError("Client pool not initialized. Call init_pool() first.")
    return _pool


async def init_pool(config: dict, pool_size: int | None = None):
    """Initialize the global client pool and start background warmup."""
    global _pool
    size = pool_size or _env_int("AGENT_POOL_SIZE")
    _pool = ClientPool(pool_size=size)
    _pool.set_config(config)
    # Warm in background so server startup isn't blocked
    asyncio.create_task(_pool.warmup())
    logger.info("Client pool created (size=%d), warming in background...", size)


async def shutdown_pool():
    """Shut down the global client pool."""
    global _pool
    if _pool is not None:
        await _pool.shutdown()
        _pool = None


class ClientPool:
    """Bounded pool of pre-warmed ClaudeSDKClient instances.

    At most pool_size clients exist at any time. Requests that arrive
    when all clients are busy wait in a queue until one is released.

    Clients are assigned accounts in round-robin from all connected
    Claude accounts. Each client carries a `_pool_account` dict with
    the email and config_dir of the account it was created with.
    """

    def __init__(self, pool_size: int = 3):
        self._pool_size = pool_size
        self._available: asyncio.Queue[ClaudeSDKClient] = asyncio.Queue()
        self._config: dict | None = None
        self._closed = False
        self._warming = 0  # number of clients currently being warmed
        self._in_use = 0  # number of clients checked out for active conversations
        self._queue_depth = 0  # number of requests waiting for a free client

        # Round-robin account management
        self._accounts: list[dict] = []  # [{"email": "x@y.com", "config_dir": "/path/to/x@y.com"}, ...]
        self._rr_index: int = 0  # next account index for round-robin
        self._account_cooldowns: dict[str, float] = {}  # email -> cooldown expiry timestamp

    def set_config(self, config: dict):
        self._config = config

    async def reload_config(self, config: dict):
        """Reload the pool with new config (e.g. after integration connect/disconnect).

        Drains all idle clients and re-warms them with the updated MCP server config.
        In-use clients finish naturally and are discarded on release.
        """
        old_servers = set(self._config.get("mcp_servers", {}).keys()) if self._config else set()
        self._config = config
        new_servers = set(config.get("mcp_servers", {}).keys())

        if old_servers == new_servers:
            logger.info("Pool reload: MCP servers unchanged, skipping")
            return

        logger.info(
            "Pool reload: MCP servers changed (added=%s, removed=%s), draining idle clients...",
            new_servers - old_servers, old_servers - new_servers,
        )

        # Drain idle clients (they have stale MCP configs)
        drained = 0
        while not self._available.empty():
            try:
                client = self._available.get_nowait()
                await self.safe_disconnect(client)
                drained += 1
            except asyncio.QueueEmpty:
                break

        logger.info("Pool reload: drained %d idle clients, re-warming in background...", drained)
        asyncio.create_task(self.warmup())

    async def reload_prompt(self):
        """Drain idle clients so replacements pick up the latest system prompt."""
        drained = 0
        while not self._available.empty():
            try:
                client = self._available.get_nowait()
                await self.safe_disconnect(client)
                drained += 1
            except asyncio.QueueEmpty:
                break

        logger.info("Prompt reload: drained %d idle clients, re-warming in background...", drained)
        if drained > 0:
            asyncio.create_task(self.warmup())

    # ── Account scanning ──────────────────────────────────────────────

    def _scan_accounts(self, disabled_emails: set[str] | None = None):
        """Scan CLAUDE_USERS_DIR for accounts with valid OAuth credentials.

        Only includes accounts where:
        - .claude.json has oauthAccount.emailAddress
        - email is NOT in disabled_emails (users with claude_pool_enabled=false)
        """
        users_dir = _get_claude_users_dir()
        accounts: list[dict] = []
        disabled = disabled_emails or set()

        if not users_dir.exists():
            logger.info("No CLAUDE_USERS_DIR at %s — pool will be empty", users_dir)
            self._accounts = accounts
            return

        for entry in users_dir.iterdir():
            if not entry.is_dir():
                continue
            config_file = entry / ".claude.json"
            if not config_file.exists():
                continue
            try:
                data = json.loads(config_file.read_text())
                oauth_email = data.get("oauthAccount", {}).get("emailAddress")
                if oauth_email:
                    if entry.name in disabled:
                        logger.info("Skipping account %s (pool disabled by admin)", entry.name)
                        continue
                    accounts.append({
                        "email": entry.name,
                        "config_dir": str(entry),
                    })
            except Exception:
                continue

        self._accounts = accounts
        # Clean up stale cooldowns for accounts that no longer exist
        valid_emails = {a["email"] for a in accounts}
        self._account_cooldowns = {
            k: v for k, v in self._account_cooldowns.items() if k in valid_emails
        }
        logger.info("Account scan: %d accounts found (disabled=%d): %s",
                     len(accounts), len(disabled), [a["email"] for a in accounts])

    def _next_account(self) -> dict | None:
        """Pick the next account in round-robin, skipping those on cooldown.

        Returns None if no accounts exist or all are on cooldown.
        """
        if not self._accounts:
            return None

        now = time.time()
        # Clean expired cooldowns
        self._account_cooldowns = {
            k: v for k, v in self._account_cooldowns.items() if v > now
        }

        n = len(self._accounts)
        for _ in range(n):
            account = self._accounts[self._rr_index % n]
            self._rr_index = (self._rr_index + 1) % n
            if account["email"] not in self._account_cooldowns:
                return account

        # All on cooldown — return None
        logger.warning("All %d accounts are on cooldown", n)
        return None

    def mark_account_exhausted(self, email: str, auth_error: bool = False):
        """Put an account on cooldown after a rate limit, billing, or auth error.

        Auth errors use a 1-hour cooldown since the account won't self-heal
        (someone needs to re-login). Rate limits use the default 5-minute cooldown.
        """
        cooldown = 3600 if auth_error else ACCOUNT_COOLDOWN_SECONDS
        self._account_cooldowns[email] = time.time() + cooldown
        logger.warning("Account %s marked exhausted, cooldown for %ds%s", email, cooldown, " (auth error)" if auth_error else "")

    async def _get_disabled_emails(self) -> set[str]:
        """Query MongoDB for users where claude_pool_enabled is explicitly false."""
        try:
            from observability.db import get_db
            db = get_db()
            if db is None:
                return set()
            cursor = db.users.find(
                {"claude_pool_enabled": False},
                {"email": 1},
            )
            docs = await cursor.to_list(200)
            disabled = {doc["email"] for doc in docs if "email" in doc}
            if disabled:
                logger.info("Pool-disabled accounts from DB: %s", disabled)
            return disabled
        except Exception as e:
            logger.warning("Failed to query disabled pool accounts: %s", e)
            return set()

    def refresh_accounts(self):
        """Re-scan accounts. Called when a user connects/disconnects Claude.

        Schedules an async task to query MongoDB for disabled accounts.
        """
        asyncio.create_task(self._async_refresh_accounts())

    async def _async_refresh_accounts(self):
        """Async version of refresh_accounts that queries MongoDB."""
        disabled = await self._get_disabled_emails()
        self._scan_accounts(disabled_emails=disabled)
        # If pool has fewer clients than it should, warm more
        current = self._available.qsize() + self._warming + self._in_use
        if not self._closed and self._accounts and current < self._pool_size:
            deficit = self._pool_size - current
            logger.info("Account refresh: warming %d additional clients", deficit)
            for _ in range(deficit):
                asyncio.create_task(self._warm_one())

    # ── Pool warmup ───────────────────────────────────────────────────

    async def warmup(self):
        """Pre-warm the pool with clients (run as background task)."""
        disabled = await self._get_disabled_emails()
        self._scan_accounts(disabled_emails=disabled)
        if not self._accounts:
            logger.info("No accounts connected — pool will be empty until users log in")
            return

        target = min(self._pool_size, len(self._accounts) * 3)  # cap at 3 per account
        logger.info("Starting pool warmup (%d clients across %d accounts)...",
                     target, len(self._accounts))
        tasks = []
        for _ in range(target):
            account = self._next_account()
            if account is None:
                break
            tasks.append(self._create_client(account))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        success = 0
        for result in results:
            if isinstance(result, Exception):
                logger.error("Failed to warm client: %s", result)
            else:
                await self._available.put(result)
                success += 1

        logger.info(
            "Pool warmup complete: %d/%d clients ready", success, len(tasks)
        )

        # Retry any failures
        failures = len(tasks) - success
        if failures > 0:
            logger.info("Retrying %d failed warmups...", failures)
            for _ in range(failures):
                asyncio.create_task(self._warm_one())

    @staticmethod
    def default_model() -> str:
        return os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")

    def _build_options(self, model_override: str | None = None) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions from the stored config."""
        system_prompt = build_pooled_system_prompt()
        model = model_override or self.default_model()
        max_turns = int(os.environ.get("AGENT_MAX_TURNS", "500"))
        mcp_servers = self._config.get("mcp_servers", {})

        allowed_tools = ["Bash", "Read", "Skill", "WebSearch"]
        for server_name in mcp_servers:
            allowed_tools.append(f"mcp__{server_name}")

        return ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=model,
            max_turns=max_turns,
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            permission_mode="acceptEdits",
            setting_sources=["project"],
            cwd=str(Path(__file__).parent.parent),
            max_buffer_size=10 * 1024 * 1024,
        )

    async def _create_client(self, account: dict, model_override: str | None = None) -> ClaudeSDKClient:
        """Create and connect a new ClaudeSDKClient for a specific account."""
        self._warming += 1
        client = None
        try:
            options = self._build_options(model_override=model_override)
            options.env = {"CLAUDE_CONFIG_DIR": account["config_dir"]}
            client = ClaudeSDKClient(options=options)
            await asyncio.wait_for(client.connect(), timeout=_env_int("AGENT_CONNECT_TIMEOUT"))
            # Attach account info for diagnostics and rate-limit tracking
            client._pool_account = account  # type: ignore[attr-defined]
            client._pool_model = options.model  # type: ignore[attr-defined]
            if model_override:
                client._pool_ephemeral = True  # type: ignore[attr-defined]
            logger.info("New client connected (account=%s, model=%s)", account["email"], options.model)
            return client
        except (asyncio.TimeoutError, Exception) as e:
            if isinstance(e, asyncio.TimeoutError):
                logger.error(
                    "Client connect timed out after %ds for account %s",
                    _env_int("AGENT_CONNECT_TIMEOUT"), account["email"],
                )
            # Clean up the partially-initialized client to prevent orphan subprocess leaks
            if client is not None:
                logger.info("Cleaning up failed client subprocess...")
                await self.safe_disconnect(client)
            raise
        finally:
            self._warming -= 1

    async def acquire(self, model: str | None = None) -> ClaudeSDKClient:
        """Get a warm client from the pool.

        The pool is bounded — at most pool_size clients exist. Strategy:
        1. Try to grab a warm client immediately (instant).
        2. If pool is empty, wait for a client to become available (up to AGENT_QUEUE_TIMEOUT).
           This happens when either a warming client finishes or an in-use client is released.

        No on-demand clients are created beyond pool_size to prevent memory exhaustion.
        The caller MUST call release() when done, even if an error occurred.
        """
        if self._closed:
            raise RuntimeError("Pool is closed")

        if not self._accounts:
            raise RuntimeError("No Claude accounts connected. Ask a team member to log in via Integrations.")

        requested_model = model or self.default_model()
        if requested_model != self.default_model():
            account = self._next_account()
            if account is None:
                raise RuntimeError("No Claude accounts are currently available for the selected model.")
            self._in_use += 1
            logger.info(
                "Creating one-off Claude client for selected model=%s (account=%s, in_use=%d)",
                requested_model,
                account["email"],
                self._in_use,
            )
            try:
                return await self._create_client(account, model_override=requested_model)
            except Exception:
                self._in_use = max(0, self._in_use - 1)
                raise

        # 1. Try instant grab
        try:
            client = self._available.get_nowait()
            self._in_use += 1
            logger.info(
                "Acquired warm client (account=%s, available=%d, in_use=%d)",
                getattr(client, '_pool_account', {}).get('email', '?'),
                self._available.qsize(), self._in_use,
            )
            return client
        except asyncio.QueueEmpty:
            pass

        # 2. Wait for a client to become available (warming or released by another conversation)
        self._queue_depth += 1
        logger.info(
            "Pool empty — request queued (queue_depth=%d, warming=%d, in_use=%d). "
            "Waiting up to %ds for a free client...",
            self._queue_depth, self._warming, self._in_use, _env_int("AGENT_QUEUE_TIMEOUT"),
        )
        try:
            client = await asyncio.wait_for(self._available.get(), timeout=_env_int("AGENT_QUEUE_TIMEOUT"))
            self._in_use += 1
            logger.info(
                "Acquired client after queuing (account=%s, available=%d, in_use=%d)",
                getattr(client, '_pool_account', {}).get('email', '?'),
                self._available.qsize(), self._in_use,
            )
            return client
        except asyncio.TimeoutError:
            logger.error(
                "Queue timeout after %ds — no client became available "
                "(warming=%d, in_use=%d, queue_depth=%d)",
                _env_int("AGENT_QUEUE_TIMEOUT"), self._warming, self._in_use, self._queue_depth,
            )
            raise
        finally:
            self._queue_depth -= 1

    async def release(self, client: ClaudeSDKClient):
        """Discard a used client and start warming a replacement.

        Clients are not reused to prevent conversation context leaking
        between independent conversations.

        The old client is fully disconnected BEFORE warming a replacement
        to avoid overlapping memory usage (old + new subprocesses coexisting).
        """
        self._in_use = max(0, self._in_use - 1)
        if getattr(client, "_pool_ephemeral", False):
            asyncio.create_task(self.safe_disconnect(client))
            return
        # Disconnect-then-warm in background (don't block the caller)
        asyncio.create_task(self._disconnect_then_warm(client))

    @staticmethod
    def _kill_process_tree(pid: int):
        """Kill a process and ALL its descendants (children, grandchildren, etc.).

        This prevents orphaned MCP server processes from accumulating after
        a Claude CLI subprocess is terminated.
        """
        try:
            # Collect all descendant PIDs first (bottom-up kill order)
            descendants = []
            pids_to_check = [pid]
            while pids_to_check:
                current = pids_to_check.pop()
                try:
                    result = subprocess.run(
                        ["pgrep", "-P", str(current)],
                        capture_output=True, text=True, timeout=5,
                    )
                    for child_pid in result.stdout.strip().split("\n"):
                        if child_pid.strip():
                            child = int(child_pid.strip())
                            descendants.append(child)
                            pids_to_check.append(child)
                except (subprocess.TimeoutExpired, ValueError):
                    pass

            # Kill children first (deepest first), then the parent
            killed = 0
            for desc_pid in reversed(descendants):
                try:
                    os.kill(desc_pid, signal.SIGKILL)
                    killed += 1
                except ProcessLookupError:
                    pass  # already dead
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
            except ProcessLookupError:
                pass

            if killed > 0:
                logger.info(
                    "Killed process tree: pid=%d + %d descendants (%d total)",
                    pid, len(descendants), killed,
                )
        except Exception as e:
            logger.warning("Process tree kill failed for pid=%d: %s", pid, e)

    async def _disconnect_then_warm(self, client: ClaudeSDKClient):
        """Disconnect old client first, then warm a replacement sequentially."""
        await self.safe_disconnect(client)
        logger.info("Old client disconnected, checking if replacement needed...")
        # Only warm after old client's memory is freed
        if not self._closed and self._accounts and (self._available.qsize() + self._warming) < self._pool_size:
            await self._warm_one()

    async def safe_disconnect(self, client: ClaudeSDKClient):
        """Disconnect a client with a timeout. Force-kill the entire process tree if it hangs."""
        transport = getattr(client, "_transport", None)
        proc = getattr(transport, "_process", None) if transport else None
        pid = getattr(proc, "pid", None) if proc else None

        try:
            await asyncio.wait_for(client.disconnect(), timeout=30)
        except (Exception, asyncio.TimeoutError) as e:
            logger.warning("Client disconnect failed/timed out: %s — force-killing process tree", e)

        if pid:
            self._kill_process_tree(pid)
        else:
            logger.warning("Could not find subprocess PID on client — orphan processes may leak")

    async def _warm_one(self):
        """Warm a single replacement client in the background with retries."""
        if self._closed:
            return
        # Double-check we still need one (another warm task may have finished first)
        if (self._available.qsize() + self._warming) >= self._pool_size:
            return
        account = self._next_account()
        if account is None:
            logger.warning("Cannot warm replacement — no accounts available (all on cooldown or none connected)")
            return
        for attempt in range(1, WARM_RETRIES + 1):
            if self._closed:
                return
            try:
                client = await self._create_client(account)
                if not self._closed:
                    await self._available.put(client)
                    logger.info(
                        "Replacement client warmed (account=%s, available=%d, in_use=%d)",
                        account["email"], self._available.qsize(), self._in_use,
                    )
                else:
                    await self.safe_disconnect(client)
                return  # success
            except Exception as e:
                logger.error(
                    "Warm attempt %d/%d failed for account %s: %s",
                    attempt, WARM_RETRIES, account["email"], e,
                )
                if attempt < WARM_RETRIES:
                    await asyncio.sleep(WARM_RETRY_DELAY * attempt)

        logger.error("All %d warm attempts failed for account %s — pool may be depleted",
                      WARM_RETRIES, account["email"])

        # Schedule another warm cycle if pool is still needed
        if not self._closed and (self._available.qsize() + self._warming + self._in_use) < self._pool_size:
            logger.info(
                "Scheduling recovery warm in %ds (available=%d, warming=%d, in_use=%d, queue_depth=%d)",
                WARM_RECOVERY_DELAY, self._available.qsize(), self._warming, self._in_use, self._queue_depth,
            )
            await asyncio.sleep(WARM_RECOVERY_DELAY)
            if not self._closed:
                asyncio.create_task(self._warm_one())

    # ── Status & lifecycle ────────────────────────────────────────────

    def _account_distribution(self) -> dict[str, int]:
        """Count how many warm clients belong to each account."""
        dist: dict[str, int] = {}
        # Peek at all items in the queue (non-destructive via internal deque)
        for client in list(self._available._queue):  # type: ignore[attr-defined]
            email = getattr(client, '_pool_account', {}).get('email', 'unknown')
            dist[email] = dist.get(email, 0) + 1
        return dist

    def status(self) -> dict:
        """Return pool status for the /api/pool-status endpoint."""
        now = time.time()
        return {
            "pool_size": self._pool_size,
            "available": self._available.qsize(),
            "in_use": self._in_use,
            "warming": self._warming,
            "queue_depth": self._queue_depth,
            "accounts": [a["email"] for a in self._accounts],
            "accounts_on_cooldown": [
                email for email, expires in self._account_cooldowns.items()
                if expires > now
            ],
            "account_distribution": self._account_distribution(),
        }

    @property
    def available_count(self) -> int:
        return self._available.qsize()

    @property
    def warming_count(self) -> int:
        return self._warming

    async def shutdown(self):
        """Disconnect all pooled clients."""
        self._closed = True
        while not self._available.empty():
            try:
                client = self._available.get_nowait()
                await self.safe_disconnect(client)
            except asyncio.QueueEmpty:
                break
        logger.info("Client pool shut down")
