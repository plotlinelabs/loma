# Personal Crews

A crew is a team of agents you design yourself. Loma does not ship any fixed
roles: you decide who is on the team, what each member may do, and how they
are wired together. Off unless the server sets `LOMA_CREWS_ENABLED=true` and
you turn your crew on from the Crew page.

## Building blocks

| Piece | What you set | Enforced in |
|---|---|---|
| **Member** | Name, one-line description, instructions, model. Optionally based on a Loma agent (Agents page): that agent's skills and tools then limit the member. Up to 12. | `fleet/service.py` `clean_members`, `member_tool_config` |
| **Hand work to** | For each member, the members it may give tasks to. Any shape works (chains, fan-out, cycles); the depth limit bounds it. | `tool_create` |
| **Reviewed by** | Another member that checks this member's result before it counts as done: accept, rework (up to the rework limit) or escalate to you. | `dispatcher.finish`, `apply_verdict` |
| **Send and merge** | `ask` (default): sending email/Slack/Telegram, changing calendar events and merging PRs are blocked until you approve that exact action. `deny`: never, not even with approval. `allow`: no guard; only offered when the server sets `LOMA_CREWS_ALLOW_UNATTENDED_WRITES=true`. | `agent/agent_scope.py` write guard, `api/human_task_worker.py` |
| **Can ask me** | Whether the member may open a Needs-you task for an answer or approval. | `tool_escalate` |
| **Schedule** | Start a task for a member at a time on chosen weekdays, with a prompt. Optionally include today's crew activity (skipped on idle days) and deliver the reply to your Loma inbox and/or a Slack DM. | `dispatcher.run_schedules` |
| **Give a task** | Hand any member a one-off task from the Crew page. Its reply comes to your inbox. | `POST /api/crew/tasks` |
| **Limits** | Agents at once, daily USD, tasks per day, levels deep, tasks per parent, rework rounds. | dispatcher and `tool_create` |

Templates on the Crew page (Chief of Staff team, Builder and reviewer, Inbox
assistant, Blank) only pre-fill the editor; they live in
`dashboard/src/lib/crew-templates.ts` and the backend knows nothing about them.

## How a task moves

`queued -> running -> waiting (on its own tasks) | in_review | blocked (needs you) -> accepted | done | failed | cancelled`

- Every task is a taskboard conversation acting as you. Reviews stay off the board.
- A member hands out work with `tools/fleet.py create --to <member-id>`, then ends its turn. It is woken with the results when all its tasks finish. Waiting costs nothing.
- Links, permissions and limits are read from the current crew config on every call, so edits apply to running work at once. Removing a member fails its open tasks.
- Runs cut off by a restart are marked failed, never replayed.
- Pause stops new runs. Stop all also cancels open tasks.

## Agent tool

```
python3 tools/fleet.py --user-email E --auth-token T --conversation-id C status
python3 tools/fleet.py ... members
python3 tools/fleet.py ... create --to MEMBER --title T --brief B --acceptance A --request-key K [--depends-on ID,ID]
python3 tools/fleet.py ... children
python3 tools/fleet.py ... review --verdict accept|rework|escalate --notes N
python3 tools/fleet.py ... escalate --kind approval|information --title T --details D --request-key K
```

## Known limits

- One backend replica (in-memory run claim and dispatcher).
- Codex runs only partly respect the write guard (Codex is off by default).
- An approval lifts the write guard for the whole resumed turn, not only the approved action.
- Personal crews only; shared-board crews come later.
