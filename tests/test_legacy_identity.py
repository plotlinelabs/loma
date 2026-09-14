"""Legacy identity migration never broadens the runtime authorization fallback."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from scheduler.legacy_identity import backfill_legacy_identities, recorded_creator


@pytest.mark.parametrize('creator, expected', [
    ({'source': 'OWNER@example.com '}, 'owner@example.com'),
    ({'user_name': 'owner@example.com'}, 'owner@example.com'),
    ({'source': 'dashboard', 'user_name': 'owner@example.com'}, 'owner@example.com'),
    ({'source': 'owner@example.com', 'user_name': 'Owner'}, 'owner@example.com'),
    ({'source': 'owner@example.com', 'user_name': ' OWNER@example.com'}, 'owner@example.com'),
    ({'source': 'victim@example.com', 'user_name': 'owner@example.com'}, None),
    ({'source': 42}, None), (None, None), ('owner@example.com', None),
    ({'source': 'a@@example.com'}, None), ({'source': '@example.com'}, None),
    ({'source': 'owner@'}, None), ({'source': 'owner @example.com'}, None),
])
def test_recorded_creator(creator, expected):
    assert recorded_creator({'created_by': creator}) == expected


def database(flows, users):
    db = MagicMock()
    db.flows.find.return_value.__aiter__.return_value = flows
    db.users.find.return_value.to_list = AsyncMock(return_value=users)
    db.flows.bulk_write = AsyncMock(return_value=MagicMock(modified_count=1))
    return db


def flow():
    return {'_id': 'id', 'flow_id': 'f', 'created_by': {'source': 'owner@example.com'}}


@pytest.mark.asyncio
async def test_dry_run_and_guarded_batched_assignment():
    item = flow()
    item['updated_at'] = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = database([item], [{'email': 'owner@example.com'}])
    assert await backfill_legacy_identities(db, dry_run=True) == {'eligible': 1, 'assigned': 0, 'unresolved': 0}
    db.flows.bulk_write.assert_not_called()
    assert (await backfill_legacy_identities(db))['assigned'] == 1
    operations = db.flows.bulk_write.call_args.args[0]
    assert len(operations) == 1
    operation = operations[0]
    assert operation._filter['updated_at'] == item['updated_at']
    assert operation._filter['created_by'] == item['created_by']
    assert operation._filter['run_as'] == {'$in': [None, '']}
    assert operation._filter['identity_version'] == {'$exists': False}
    assert operation._doc['$set']['run_as'] == 'owner@example.com'
    assert operation._doc['$set']['run_as_backfill']['account'] == 'owner@example.com'
    db.users.find.assert_called_with({'email': {'$in': ['owner@example.com']}, 'status': 'active'}, {'email': 1})


@pytest.mark.asyncio
async def test_unresolved_account_does_not_write():
    db = database([flow()], [])
    assert (await backfill_legacy_identities(db))['unresolved'] == 1
    db.flows.bulk_write.assert_not_called()


@pytest.mark.asyncio
async def test_batches_are_bounded():
    db = database([flow() for _ in range(201)], [{'email': 'owner@example.com'}])
    result = await backfill_legacy_identities(db)
    assert result['eligible'] == 201
    assert [len(call.args[0]) for call in db.flows.bulk_write.call_args_list] == [100, 100, 1]


@pytest.mark.asyncio
async def test_absent_database_is_noop():
    assert (await backfill_legacy_identities(None))['assigned'] == 0
