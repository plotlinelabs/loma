import os
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pytest

from agent import pool
from broker import controller
from broker.credential_files import protect_account_directory
from broker.service import Denied


@pytest.mark.asyncio
async def test_unscoped_background_call_never_borrows_account(monkeypatch):
    account = Mock(side_effect=AssertionError('must not borrow credentials'))
    spawn = AsyncMock()
    monkeypatch.setattr(pool, 'get_pool', account)
    monkeypatch.setattr(controller, 'current_run', SimpleNamespace(get=lambda: None))
    monkeypatch.setattr(pool.worker_mod, 'spawn_worker', spawn)
    result = await pool.run_background_cli(['claude', '-p', 'synthetic'], timeout=1)
    assert result[0] == 1
    account.assert_not_called()
    spawn.assert_not_awaited()


def test_legacy_account_permissions_are_private(tmp_path):
    path = tmp_path / 'account'
    path.mkdir(mode=0o755)
    protect_account_directory(path)
    assert path.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_uid == os.geteuid()


def test_account_symlink_not_followed(tmp_path):
    target = tmp_path / 'target'
    target.mkdir(mode=0o755)
    alias = tmp_path / 'alias'
    alias.symlink_to(target)
    with pytest.raises(Denied):
        protect_account_directory(alias)
    assert target.stat().st_mode & 0o777 == 0o755


@pytest.mark.skipif(os.geteuid() != 0, reason='requires synthetic uid ownership')
def test_legacy_worker_owned_directory_is_reclaimed(tmp_path):
    path = tmp_path / 'account'
    path.mkdir()
    os.chown(path, 299999, 299999)
    protect_account_directory(path)
    assert path.stat().st_uid == 0
    assert path.stat().st_mode & 0o777 == 0o700
