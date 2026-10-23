# Personal Crews

A crew is a small team of agents that works on your taskboard without you
starting each task.

- **Chief of Staff (CoS)** plans your day every morning from your sources
  (Gmail read only by default, Calendar, the taskboard; Slack and Linear are
  opt-in), creates one task per goal and sends you a morning brief.
- **PM** splits a goal into worker tasks with acceptance criteria.
- **Worker** does one task end to end.
- **PM review** is a separate run that checks each worker result against its
  acceptance criteria: accept, rework (up to `max_rework` rounds) or escalate.
- In the evening the CoS writes a report of the day's tree.

Reports go to the Loma inbox and, when enabled, a Slack DM from the bot.

## Turning it on

1. Server: `LOMA_CREWS_ENABLED=true` (default off) and the scheduler on.
   `LOMA_FLEET_MAX_CONCURRENT` caps runs across all crews (default 8).
2. User: open **Crew** in the sidebar, adjust settings, press **Turn on**.
   Rituals whose time already passed today are skipped; use **Plan now**.

## How it works

Every crew agent is a taskboard conversation with a `fleet` block
(`fleet/service.py`). `fleet/dispatcher.py` runs every 10 seconds:

1. creates the morning plan / evening report when due (once per local day);
2. wakes a parent when all its children finished (waiting costs nothing);
3. picks up tasks whose escalation the owner answered;
4. starts queued tasks whose `depends_on` are done, under the crew's
   `max_concurrent` and daily USD budget.

Agents grow the tree only through `tools/fleet.py` (`create`, `children`,
`status`, `review`, `escalate`). Depth, fan-out, roles and the daily task cap
are checked server side.

## Safety

- Every run acts as the crew owner. Children keep the parent's scope.
- Crew runs carry a write guard (`agent/agent_scope.py`): sending email,
  Slack or Telegram messages, changing calendar events and merging PRs are
  denied on Claude and OpenCode, and Bash commands are checked on Codex.
  Drafts and draft PRs are allowed. To act, an agent escalates; only the
  owner's **Approve** on the human task lifts the guard for the resumed turn.
- A run cut off by a restart is marked failed and never replayed.
- **Pause** stops new dispatch. **Stop all** cancels open tasks and
  interrupts running ones.

## Settings

| Setting | Default | Range |
|---|---|---|
| Agents at once | 5 | 1-8 |
| Daily budget | $25 | $1-500 |
| Max depth | 3 (CoS, PM, worker) | 2-3 |
| Tasks per parent | 8 | 1-12 |
| Tasks per day | 40 | 1-100 |
| Rework rounds | 2 | 0-3 |
