"""Trusted, durable per-run model budget callbacks for ModelRelay.

One Mongo document owns both the budget and its bounded call ledger. Admission,
refunds and counters change atomically, including across backend processes.
Unknown outcomes retain their holds. Never bind these callbacks to worker data.
This is priced API usage, not subscription invoice accounting or chat cutover.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re

from pymongo.write_concern import WriteConcern

from isolation.models import ModelDenied, ModelRelay
from isolation.usage import UsageReceipt, count


@dataclass(frozen=True)
class BudgetSpec:
    protocol: str
    model: str
    account_id: str  # Opaque trusted account reference, never a credential.
    input_ceiling: int
    output_ceiling: int
    input_rate: int  # Integer nanodollars per token, pinned for this run.
    output_rate: int
    cache_read_rate: int
    cache_write_rate: int
    budget_nusd: int
    max_calls: int = 128

    def __post_init__(self):
        if self.protocol not in {'responses', 'messages', 'chat'}:
            raise ValueError('Unsupported billing protocol')
        for value in (self.model, self.account_id):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError('A fixed model and account are required')
        for value, maximum in ((self.input_ceiling, 2_000_000), (self.output_ceiling, 131072),
                               (self.budget_nusd, 100_000_000_000), (self.max_calls, 128)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError('Invalid model budget limit')
        for rate in (self.input_rate, self.output_rate, self.cache_read_rate, self.cache_write_rate):
            if type(rate) is not int or not 0 <= rate <= 10_000_000:
                raise ValueError('Invalid model price')
        if not self.input_rate or not self.output_rate:
            raise ValueError('Input and output prices must be positive')

    @property
    def reservation(self):
        return (self.input_ceiling * max(self.input_rate, self.cache_read_rate, self.cache_write_rate)
                + self.output_ceiling * self.output_rate)

    def charge(self, receipt):
        if (not isinstance(receipt, UsageReceipt) or receipt.protocol != self.protocol
                or receipt.model != self.model):
            raise ValueError('Provider model or protocol differs from pinned pricing')
        incoming, outgoing, reads, writes = [count(v) for v in (
            receipt.input_tokens, receipt.output_tokens, receipt.cache_read_tokens, receipt.cache_write_tokens)]
        if not isinstance(receipt.response_id, str) or not 1 <= len(receipt.response_id) <= 256:
            raise ValueError('Invalid provider receipt identifier')
        if self.protocol == 'messages':
            total_input, plain = incoming + reads + writes, incoming
        else:
            if reads > incoming or writes:
                raise ValueError('Invalid OpenAI cache usage')
            total_input, plain = incoming, incoming - reads
        if total_input > self.input_ceiling or outgoing > self.output_ceiling:
            raise ValueError('Provider usage exceeded reserved limits')
        return plain * self.input_rate + outgoing * self.output_rate + reads * self.cache_read_rate + writes * self.cache_write_rate


def _now():
    return datetime.now(timezone.utc)


class ModelBudget:
    def __init__(self, db, authority, spec):
        self.authority, self.spec = authority, spec
        # Majority acknowledgement before dispatch; no best-effort observability
        # writes on this path. Default Mongo network errors propagate fail-closed.
        self.collection = db.isolated_model_budgets.with_options(write_concern=WriteConcern(w='majority'))
        self.key = {'_id': authority.run_id, 'owner': authority.user_email, 'spec': asdict(spec)}

    async def initialize(self):
        # Reopening can never reset a budget, replace its account/rates, or clear
        # a stop. The intrinsic unique _id index arbitrates racing initializers.
        from pymongo.errors import DuplicateKeyError
        try:
            await self.collection.insert_one({**self.key, 'active': True, 'blocked': False,
                'committed_nusd': 0, 'recorded_nusd': 0, 'calls': [], 'call_count': 0, 'created_at': _now()})
        except DuplicateKeyError:
            if not await self.collection.find_one(self.key, {'_id': 1}):
                raise ModelDenied('Existing budget has a different owner or contract') from None

    def _check(self, authority, call_id):
        if (authority != self.authority or not isinstance(call_id, str)
                or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', call_id)):
            raise ModelDenied('Invalid accounting scope')

    async def reserve(self, authority, call_id, grant, body):
        self._check(authority, call_id)
        if (grant.protocol != self.spec.protocol or grant.model != self.spec.model
                or grant.max_output_tokens > self.spec.output_ceiling or grant.max_calls > self.spec.max_calls):
            raise ModelDenied('Model grant exceeds the pinned budget contract')
        amount = self.spec.reservation
        result = await self.collection.update_one({**self.key, 'active': True, 'blocked': False,
            'calls.call_id': {'$ne': call_id}, 'call_count': {'$lt': self.spec.max_calls},
            'committed_nusd': {'$lte': self.spec.budget_nusd - amount}},
            {'$inc': {'committed_nusd': amount, 'call_count': 1}, '$push': {'calls': {
                'call_id': call_id, 'status': 'held', 'reserved_nusd': amount, 'at': _now()}}})
        if result.modified_count != 1:
            # A repeated reserve is NOT permission to replay a provider request.
            raise ModelDenied('Budget unavailable, exhausted, stopped or call already reserved')

    async def _entry(self, call_id):
        saved = await self.collection.find_one({**self.key, 'calls.call_id': call_id}, {'calls.$': 1})
        if not saved:
            raise ModelDenied('No matching durable model reservation')
        return saved['calls'][0]

    async def record_usage(self, authority, call_id, receipt):
        self._check(authority, call_id)
        entry = await self._entry(call_id)
        if receipt is None:
            # Missing evidence is never a refund, including after process loss.
            return
        try:
            actual = self.spec.charge(receipt)
        except ValueError:
            await self.collection.update_one(self.key, {'$set': {'blocked': True}})
            raise ModelDenied('Unrecognized provider usage; hold retained and budget blocked') from None
        evidence = asdict(receipt)
        if entry['status'] == 'recorded':
            if entry['receipt'] != evidence:
                await self.collection.update_one(self.key, {'$set': {'blocked': True}})
                raise ModelDenied('Conflicting usage evidence')
            return
        # Prevent the same provider response from refunding multiple calls within
        # this run. Calls from different account/run ledgers are never coalesced.
        result = await self.collection.update_one({**self.key,
            'calls.receipt.response_id': {'$ne': receipt.response_id},
            'calls': {'$elemMatch': {'call_id': call_id, 'status': 'held'}}},
            {'$inc': {'committed_nusd': actual - self.spec.reservation, 'recorded_nusd': actual},
             '$set': {'calls.$.status': 'recorded', 'calls.$.receipt': evidence,
                      'calls.$.actual_nusd': actual, 'calls.$.recorded_at': _now()}})
        if result.modified_count != 1:
            # Distinguish a concurrent identical settlement from contradictory
            # evidence or a duplicate response. Never apply counters twice.
            current = await self._entry(call_id)
            if current.get('status') != 'recorded' or current.get('receipt') != evidence:
                await self.collection.update_one(self.key, {'$set': {'blocked': True}})
                raise ModelDenied('Conflicting usage evidence')

    async def settle(self, authority, call_id, outcome):
        self._check(authority, call_id)
        if outcome not in {'stream_ended', 'interrupted', 'unknown'}:
            raise ModelDenied('Invalid transport outcome')
        await self._entry(call_id)
        # A transport event adds metadata only, never modifies held funds.
        await self.collection.update_one({**self.key, 'calls': {'$elemMatch': {
            'call_id': call_id, 'outcome': {'$exists': False}}}},
            {'$set': {'calls.$.outcome': outcome, 'calls.$.ended_at': _now()}})

    async def stop(self):
        await self.collection.update_one(self.key, {'$set': {'active': False}})

    def relay(self, grant, *, session, authorize, audit):
        """Bind only on the trusted backend; account selection remains external."""
        return ModelRelay(self.authority, grant, session=session, authorize=authorize, audit=audit,
                          reserve=self.reserve, settle=self.settle, record_usage=self.record_usage)
