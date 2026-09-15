"""Conservative USD reservations shared by a root and all its delegates.

Integer nanodollars avoid float races. Prices are operator-supplied ceilings, not
live provider quotes. The fixed text-only request is bounded to 100k UTF-8 bytes;
reserve 131072 input tokens plus the entire 2048-token output before dispatch.
A crash/timeout keeps the full reservation. Only a trustworthy usage response
can release unused funds. No refund from a client, model result or owner report.
"""
import json
import os
from decimal import Decimal, InvalidOperation

from pymongo import ReturnDocument
from autonomy.core import now, ident, TERMINAL

INPUT_CEILING = 131072
OUTPUT_CEILING = 2048
DEFAULT_BUDGET_MICROUSD = 1000000


def validate_budget(value):
    if type(value) is not int or not 10000 <= value <= 100000000:
        raise ValueError('Model budget must be between $0.01 and $100 per run')
    return value


def pricing(model):
    """Exact model allowlist; never silently borrow another model's price."""
    try:
        config = json.loads(os.getenv('LOMA_WORK_PRICING_JSON', '{}'))[model]
        rates = []
        for key in ('input_usd_per_million', 'output_usd_per_million'):
            rate = Decimal(str(config[key])) * 1000
            if not rate.is_finite() or not 0 < rate <= 10000000 or rate != rate.to_integral_value():
                raise ValueError()
            rates.append(int(rate))
        return {'model': model, 'input_nusd_per_token': rates[0], 'output_nusd_per_token': rates[1]}
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ValueError('Model pricing is missing or invalid. Ask an admin to configure price ceilings.') from None


def ready():
    try:
        pricing(os.getenv('LOMA_WORK_MODEL', ''))
        return True
    except ValueError:
        return False


async def reserve(db, run, rates):
    # All descendants spend the root's pinned cap. A caller cannot supply a
    # larger child budget. Existing runs get a finite default, never unlimited.
    amount = INPUT_CEILING * rates['input_nusd_per_token'] + OUTPUT_CEILING * rates['output_nusd_per_token']
    entry = {'reservation_id': ident(), 'run_id': run['run_id'], 'status': 'held',
             'reserved_nusd': amount, 'rates': rates, 'at': now()}
    result = await db.agent_runs.find_one_and_update(
        {'run_id': run['root_id'], 'owner': run['owner'], 'status': {'$nin': list(TERMINAL)}, 'cost_blocked': {'$ne': True},
         '$expr': {'$lte': [{'$add': [{'$ifNull': ['$cost_committed_nusd', 0]}, amount]},
                            {'$multiply': [{'$ifNull': ['$snapshot.max_cost_microusd', DEFAULT_BUDGET_MICROUSD]}, 1000]}]}},
        {'$inc': {'cost_committed_nusd': amount}, '$push': {'cost_ledger': entry}},
        return_document=ReturnDocument.AFTER)
    if not result:
        raise ValueError('Shared model budget exhausted or run stopped. Uncertain charges stay reserved; no model call was made.')
    return entry


async def settle(db, run, entry, usage):
    # Usage is supplied by the SDK response, never the model's JSON output.
    values = [getattr(usage, name, None) for name in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')]
    if any(type(v) is not int or v < 0 for v in values[:2]) or any(v not in (None, 0) for v in values[2:]):
        raise ValueError('Unrecognized model billing usage. Full cost remains reserved.')
    input_tokens, output_tokens = values[:2]
    if input_tokens > INPUT_CEILING or output_tokens > OUTPUT_CEILING:
        # Fail closed for future calls if the provider violated request bounds.
        await db.agent_runs.update_one({'run_id': run['root_id']}, {'$set': {'cost_blocked': True}})
        raise ValueError('Provider usage exceeded the reserved ceiling. Further model calls are blocked.')
    rates = entry['rates']
    actual = input_tokens * rates['input_nusd_per_token'] + output_tokens * rates['output_nusd_per_token']
    await db.agent_runs.update_one(
        {'run_id': run['root_id'], 'cost_ledger': {'$elemMatch': {'reservation_id': entry['reservation_id'], 'status': 'held'}}},
        {'$inc': {'cost_committed_nusd': actual - entry['reserved_nusd'], 'cost_recorded_nusd': actual,
                  'input_tokens_used': input_tokens, 'output_tokens_used': output_tokens},
         '$set': {'cost_ledger.$.status': 'recorded', 'cost_ledger.$.actual_nusd': actual,
                  'cost_ledger.$.input_tokens': input_tokens, 'cost_ledger.$.output_tokens': output_tokens,
                  'cost_ledger.$.settled_at': now()}})
