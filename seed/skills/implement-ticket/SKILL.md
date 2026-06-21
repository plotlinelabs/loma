---
name: implement-ticket
description: Implement a Linear ticket — read the ticket, plan code changes, create a branch, push code, and open a draft PR on GitHub. Use when someone shares a Linear ticket and asks you to implement it.
user-invocable: false
---

# Implement Linear Ticket Playbook

When someone shares a Linear ticket (URL or ID) and asks you to implement it, follow this workflow.

---

## Tool Usage Rules (MANDATORY)

### BANNED tools after cloning

Once a repo is cloned to `/tmp/<repo-name>-<ticket-id>`, the following tools are **BANNED for the rest of the session**:

Only use GitHub MCP tools for operations that CANNOT be done locally:
- `mcp__github__create_branch` — only if not using local `git checkout -b`

| Banned Tool | Use Instead |
|---|---|
| `mcp__github__get_file_contents` | `Read` tool on `/tmp/<repo-name>-<ticket-id>/...` |
| `mcp__github__search_code` | `Grep` tool on `/tmp/<repo-name>-<ticket-id>/...` |

**Zero tolerance** — every call to a banned tool after cloning is a bug. There are no exceptions.

**This ban applies to subagents too.** When spawning Agent/Task subagents for code exploration, instruct them to use `Read`/`Grep`/`Glob` on the local clone path — NOT GitHub MCP tools.

### Allowed GitHub MCP tools (non-file operations only)
- `mcp__github__get_label` / `mcp__github__issue_write` — label management
- `mcp__github__search_code` — **ONLY in Step 0** (before cloning, for duplicate PR checks)
- `mcp__github__update_pull_request` — updating PR descriptions

> **PR creation**: Use `gh pr create` via Bash with a HEREDOC (NOT `mcp__github__create_pull_request`). The MCP tool double-escapes newlines in the body parameter, causing broken formatting on GitHub.

---

## Step 0: Check for Existing PRs and Duplicates

Before starting implementation, always check for existing PRs and duplicate/related tickets:

1. **Search GitHub for existing PRs**: Search for the ticket ID in branch names and PR titles to check if a PR already exists.
2. **Check Linear for related/duplicate tickets**: Search for similar titles or linked issues that may already have been implemented.
3. **If a PR already exists**: Report the existing PR and ask whether to update it or if the ticket is a duplicate. Even when a ticket is a duplicate with an existing PR, still post any requested comments on the ORIGINAL ticket that was specified — follow the user's instructions precisely regarding which ticket to comment on, regardless of duplicate status.

---

## Step 1: Read the Linear Ticket

Use **Linear MCP** tools to fetch the issue. Extract:
- **Title** and **description** (the requirements)
- **Acceptance criteria** (if listed)
- **Labels** and **team** (helps identify which area of the codebase)
- **Priority** (helps decide how thorough to be)
- **Comments** (may contain additional context or decisions)

If the ticket is vague or lacks enough detail to implement, say so and ask for clarification before proceeding.

---

## Step 2: Identify the Affected Repo(s)

Determine the target repository from the available context, in this order:

1. **The ticket itself** — a linked repo, a branch reference, file paths, or component names mentioned in the description, labels, or comments often point directly at the repo.
2. **The conversation** — the user may name the repo explicitly, or the thread may already reference one.
3. **A user-provided or configured repo** — if your environment defines a default repo (or a mapping of teams/areas to repos), use it. If a project-specific skill or doc maps feature areas to repositories, load it and follow that mapping.

If you maintain such a mapping, keep it obviously illustrative, e.g.:
- Frontend / web UI → your web app repo
- Backend API / business logic → your server repo
- Client SDKs → your SDK repo

Do **not** assume a fixed monorepo layout. Inspect the cloned repo (Step 3) to learn its actual structure rather than relying on a hardcoded directory map.

If you cannot confidently determine the target repo from any of the above, ask the user which repository to work in before proceeding.

**Important**: If the ticket requires changes across multiple *separate* repos, create separate PRs for each repo. If the scope is too large (major refactor, many repos), say so and suggest breaking the ticket into smaller tasks.

---

## Step 3: Clone the Repo Locally & Understand the Code

Clone the target repo to `/tmp` for local exploration and code changes:

```bash
# Clone the repo (shallow clone for speed)
git clone --depth=50 git@github.com:<owner>/<repo-name>.git /tmp/<repo-name>-<ticket-id>
cd /tmp/<repo-name>-<ticket-id>

# Checkout the default branch (confirm the repo's actual default — e.g. `main`, `master`, or `release`)
git checkout <default-branch>
```

Once cloned, explore the codebase locally to understand what exists:

1. **Search for relevant code** using Grep and Glob tools on the local clone
2. **Read the specific files** that will be modified — note coding style, patterns, imports
3. **Check for related files**: tests, type definitions, configuration files

> **Why local clone?** Working locally allows faster file reads, better search across the codebase, and the ability to run linters/tests before pushing.

> **IMPORTANT:** From this point forward, use ONLY local tools (`Read`, `Grep`, `Glob`) to explore the codebase. Do NOT use `mcp__github__get_file_contents` or `mcp__github__search_code` — the repo is already on disk.

---

## Step 4: Present the Implementation Plan

Before making any changes, share your plan in Slack:

```
*Implementation Plan for <ticket-id>*

*Repo*: <owner>/<repo-name>

*Files to modify*:
• `path/to/file1.ts` — <what change and why>
• `path/to/file2.ts` — <what change and why>

*New files*:
• `path/to/new-file.ts` — <purpose>

*Approach*:
<brief description of the implementation approach>

Shall I proceed with this implementation?
```

**Wait for confirmation** before proceeding to Step 5. Do NOT push code without explicit approval.

> **Webhook-triggered mode:** When this skill is invoked from a Linear webhook (automated flow), skip this step entirely — there is no human in the loop to approve. Proceed directly to Step 5.

---

## Step 5: Implement Changes Locally

After approval, work on the local clone in `/tmp/<repo-name>-<ticket-id>`. Use ONLY local tools (`Read`, `Grep`, `Glob`, `Edit`, `Write`) — never `mcp__github__get_file_contents`.

1. **Create a branch** from the default branch:
   ```bash
   cd /tmp/<repo-name>-<ticket-id>
   git checkout -b <branch-name>
   # Branch naming: ENG-123/short-description or feat/ENG-123-add-webhook-retry
   ```

2. **Make the code changes** using Edit/Write tools on the local files

3. **Verify the changes**:
   - Run any available linters or type checks
   - Review the diff: `git diff`

4. **Commit and push**:
   ```bash
   cd /tmp/<repo-name>-<ticket-id>
   git add <specific-files>
   git commit -m "feat: <description> (<ticket-id>)"
   git push -u origin <branch-name>
   ```

**Guidelines for the code you write (MANDATORY):**
- **Minimal changes only** — make the smallest diff that satisfies the requirement. Fewer lines changed = easier to review and merge. Do not refactor, rename, or reorganize surrounding code.
- **Reuse existing code** — before writing anything new, search the codebase for existing components, utilities, helpers, and patterns that already do what you need. Import and reuse them. Never recreate something that already exists.
- **Match existing UI patterns** — for frontend/UI changes, find similar existing components and copy their structure, styling, and patterns. The result must look visually coherent with the rest of the app. Do not invent new styles or layouts when existing ones can be reused.
- Follow the existing code style in the repo (indentation, naming, imports)
- Add comments only where the logic isn't self-evident
- Reference the Linear ticket ID in the commit message

---

## Step 6: Create a Draft PR

Use `gh pr create` via Bash with a HEREDOC for the body. Do NOT use `mcp__github__create_pull_request` — the MCP tool's JSON serialization double-escapes newlines (`\\n` instead of actual line breaks), causing PR descriptions to render as broken text on GitHub.

```bash
cd /tmp/<repo-name>
gh pr create \
  --repo <owner>/<repo-name> \
  --head <branch-name> \
  --base <default-branch> \
  --draft \
  --title "feat: <concise PR title>" \
  --body "$(cat <<'EOF'
## Summary
<1-3 bullet points describing what changed>

## Linear Ticket
<link to the Linear ticket>

## Changes
- `path/to/file1.ts` — <what changed>
- `path/to/file2.ts` — <what changed>

## Notes
- This PR was generated by the agent based on the Linear ticket above
- Please review carefully before merging
EOF
)"
```

The HEREDOC (`<<'EOF'`) ensures actual newlines are preserved in the body text. Extract the PR number from the URL printed by `gh pr create` for use in Step 6a.

---

## Step 6a: Add "Agent PR" Label

After creating the draft PR, add the `Agent PR` label to it. This applies to *every* draft PR you create, regardless of which repo it targets.

### Procedure

1. **Check if the label exists** on the target repo:
   - Use `mcp__github__get_label` with `owner: "<owner>"`, `repo: <target-repo>`, `name: "Agent PR"`

2. **If the label does NOT exist** (the call returns an error or not found), create it using Bash:
   ```bash
   curl -s -X POST \
     -H "Authorization: token $GITHUB_API_KEY" \
     -H "Accept: application/vnd.github+json" \
     https://api.github.com/repos/<owner>/<target-repo>/labels \
     -d '{"name":"Agent PR","color":"D93F0B","description":"PR generated by the agent"}'
   ```

3. **Add the label to the PR**:
   - Use `mcp__github__issue_write` with:
     - `method: "update"`
     - `owner: "<owner>"`
     - `repo: <target-repo>`
     - `issue_number: <PR number from Step 6>`
     - `labels: ["Agent PR"]`

> **Note:** GitHub PRs and issues share the same numbering — `issue_write` works for PRs too.

---

## Step 6b: Add a "preview" Label for Significant Changes (optional)

If your repo uses a `preview` label to trigger preview deployments or extra review, evaluate whether the changes are significant enough to warrant it. Add the label if **any** of the following are true:

- Changes touch **multiple apps or packages** within a monorepo
- Changes modify **API contracts**, schemas, or shared interfaces
- Changes affect **database schemas**, migrations, or data models
- Changes touch **critical paths**: authentication, billing, delivery, event pipeline
- The PR modifies **10+ files** or includes substantial new functionality
- Changes affect **infrastructure config** (Dockerfiles, CI workflows, build config)

If none of the above apply (e.g. a small single-file fix, copy change, or config tweak), skip this step.

### Procedure

**Add the label to the PR** (in addition to "Agent PR"):
- Use `mcp__github__issue_write` with:
  - `method: "update"`
  - `owner: "<owner>"`
  - `repo: <target-repo>`
  - `issue_number: <PR number from Step 6>`
  - `labels: ["Agent PR", "preview"]`

> **Scope:** Only apply this label if the target repo actually uses it. Skip this step otherwise.

---

## Step 7: Share the Result

Post back in Slack:

```
*PR Created* :white_check_mark:

<pr-url|View Draft PR>

*Changes*:
• `path/to/file1.ts` — <brief description>
• `path/to/file2.ts` — <brief description>

This is a *draft PR* — please review the changes before marking it ready for review.
```

> **Webhook-triggered mode:** When invoked from a Linear webhook, instead of posting to Slack, comment on the Linear ticket with the PR details using `mcp__linear__create_comment`. The comment MUST start with a bot marker comment (e.g. `<!-- agent -->`) to prevent webhook loops.

---

## Step 8: Cleanup

After the PR is created, clean up the local clone:

```bash
rm -rf /tmp/<repo-name>-<ticket-id>
```

---

## Guardrails

- *Always draft PRs* — never create ready-for-review PRs. Humans must review AI-generated code.
- *Always ask before pushing* — present the plan (Step 4) and wait for explicit confirmation.
  - Exception: webhook-triggered flows skip approval (Step 4 is skipped).
- *One repo per PR* — if changes span multiple repos, create separate PRs.
- *Scope limits* — if the ticket requires a large refactor, touches 10+ files, or needs changes across many repos, explain that it's too complex for automated implementation and suggest breaking it down.
- *No destructive changes* — don't delete files or remove functionality unless the ticket explicitly asks for it.
- *Follow existing patterns* — match the repo's code style, don't introduce new frameworks or patterns.
- *Local clone in /tmp* — always clone to `/tmp/<repo-name>-<ticket-id>` for implementation. This keeps the working directory clean and ensures a fresh codebase state.

---

## Thread Follow-ups

When someone replies in a thread where you've created a PR:
- **First, check if the PR is still open.** If the PR has already been merged, create a new branch from the latest default branch and open a fresh PR for follow-up changes. Do not assume the original PR branch is still open for updates.
- If they ask for changes to the PR → clone the repo again to `/tmp`, checkout the PR branch, make changes, commit, and push
- If they ask to update the PR description → use `mcp__github__update_pull_request`
- If they report issues with the code → read the specific feedback, fix the code, push a new commit
- If they approve → note that they can mark the PR as ready for review on GitHub

---

## Webhook-triggered PR Modifications

When invoked from a Linear webhook in "modify" mode (a comment mentioning the agent on a ticket that already has a PR):

1. **FIRST: Distinguish human change requests from automated/bot messages.** If the comment contains no actionable change requests (e.g., it's a bot status update like "⏳ Working on it!", a progress link, or an automated notification), do NOT proceed through the full "update PR" workflow. Instead, immediately respond that the comment contained no specific change requests and skip unnecessary steps. This avoids wasted tool calls.
2. Find the existing draft PR for the ticket on GitHub (search by ticket ID in title/branch)
3. Read the comment to understand what changes are requested
4. **Clone the repo locally** to `/tmp/<repo-name>-<ticket-id>`, checkout the PR branch, and read the current files using local tools (`Read`, `Grep`, `Glob` — NOT `mcp__github__get_file_contents`). Also read the latest ticket description. If the comment says "ticket content was updated, re-implement these changes", perform a detailed diff between the current ticket description and the existing PR code to identify specific deltas. Something must have changed — find exactly what changed and implement only the delta. Pushing an identical commit that re-applies the same files without any actual changes is wasteful and misleading. If after careful comparison you genuinely find no differences, explicitly call that out and ask for clarification rather than pushing a no-op commit.
5. Make changes locally, commit, and push to the same branch
6. Comment on the Linear ticket confirming the changes (with a bot marker comment, e.g. `<!-- agent -->`)
7. Clean up: `rm -rf /tmp/<repo-name>-<ticket-id>`

This is different from the initial implementation flow — you are *modifying* an existing PR, not creating a new one.

---

## Learnings from PR Reviews

Rules extracted from human PR review feedback and bot self-review patterns. These address recurring issues the agent should avoid when implementing tickets.

- **Update the PR description after follow-up commits that change behavior or output format.** When a follow-up commit changes the behavior described in the PR (e.g., switching a date format, changing an API response shape), update the PR description to match the new behavior using `mcp__github__update_pull_request`. Stale PR descriptions mislead reviewers and downstream consumers who reference the PR for integration details.

- **Use deep copies when duplicating objects with nested properties.** When duplicating or cloning objects that contain nested objects or arrays, use a deep copy mechanism (e.g., `JSON.parse(JSON.stringify(...))` in JS/TS, a serialization round-trip in other languages) instead of a shallow spread (`{ ...obj }`). Shallow copies create shared references to nested objects, causing mutations to one copy to affect the other.

- **Ensure all fields referenced by downstream code are included in data queries.** When adding UI components or logic that references specific data fields, verify that the upstream query actually fetches those fields. A common bug: code references a field the query never selected, causing it to silently fall through to a fallback value. Always trace the data flow from query → transform → render to confirm field availability.

- **Use explicit timezone formatting for external-facing timestamps.** When formatting timestamps for webhooks, API responses, or any external consumer, always use explicit timezone-aware formats (e.g., RFC3339 with a `Z` or offset, `toISOString()` in JS). Never rely on the implicit server timezone — normalize to UTC before formatting to make the intent self-documenting.

- **Follow the established route/endpoint conventions when adding API routes.** When adding a new API route, check how existing routes are organized (prefixes, versioning, grouping) and follow that pattern exactly rather than inventing an ad-hoc prefix. Consistent routing keeps the API predictable for consumers and reviewers.

- **Use batch operations for bulk database deletes/updates, not individual calls in a loop.** When implementing cleanup, migration, or bulk-action logic that deletes or updates multiple records, use batch/bulk database operations (e.g., a single bulk delete/write) instead of looping and operating one record at a time. Per-record calls in a loop create unnecessary round-trips, are slower, and can cause timeouts at scale. Before implementing, estimate how many records could be affected and design accordingly.

- **Verify that error-handling control flow is correct.** When writing error-handling blocks, double-check that the handling code (logging, returning) only runs on the error path. In some languages, misplaced braces or indentation compile cleanly but change control flow so the handler runs unconditionally. After writing any error-handling block, re-read it and confirm the scope is correct.

- **Match the repo's styling conventions for UI changes.** When adding or modifying UI components, follow the project's established styling approach (e.g., CSS/SCSS modules, a styling library, utility classes) rather than introducing a different one. Before adding styles, check how adjacent components are styled and reuse the same pattern.

- **Sanitize all user-controlled or dynamic values interpolated into SQL queries.** When building queries that include dynamic values (column names, filter values, breakdown dimensions), never interpolate them directly into the query string. Use parameterized queries where possible, or validate the value against an allowlist of known-safe values. This applies even when the value currently comes from a controlled source — the query function may be called from other contexts later. Pay special attention to identifiers like column aliases and `ORDER BY` clauses, which are commonly overlooked injection vectors.

- **Check for iteration errors after row-scanning loops.** When iterating over query result rows, check for an iteration error after the loop completes (the idiom varies by language/driver). Iteration can terminate early due to a network timeout or deserialization failure; without the check, partial results are silently treated as complete. Match the pattern used by sibling functions in the same file.

- **Follow the project's component/state conventions.** When writing or modifying components, follow the project's established conventions for ordering declarations, lifecycle/hooks, and state management. Before adding to a component, check the existing pattern and insert new code in the correct position. Convention violations are frequently flagged as blocking issues in review.

- **When adding a new status/enum value, update ALL code paths that consume it.** When introducing a new status, enum value, or state, systematically search for ALL locations that switch on or filter by the existing values — including metrics queries, export functions, action menus, filter dropdowns, API responses, and scheduled jobs. Each consumer must handle the new value explicitly. A common failure mode is binary logic (e.g., a query that assumes a value is one of two options) breaking when a third value is added.

- **Keep user-facing UI elements concise and dismissable — test visual co-existence with dynamic page states.** When adding alerts, banners, info messages, or any persistent UI element, keep text short (1-2 lines max) and make the element dismissable (closable). Before committing, verify how the element looks when other dynamic content is also visible on the same view — e.g., error banners, loading states, empty states, or validation messages. A verbose, non-dismissable alert that looks fine in isolation can make the page look cluttered or broken when combined with other contextual UI.

- **Do not let detached background work depend on a request's lifecycle.** When spawning fire-and-forget background work from a request handler (e.g., for replication, logging, or background writes), do not tie that work to the request's cancellation/lifecycle, which ends when the response is sent. Give the background work its own independent timeout/lifecycle so it can outlive the request instead of being cancelled mid-flight.

- **Include testing artifacts in bot-generated PR descriptions or comments.** When creating a PR, include evidence that the change was tested — screenshots, test command output, or a brief description of manual testing steps performed. Human reviewers explicitly request testing artifacts before merging bot-generated PRs. At minimum, add a "## Testing" section documenting: (1) what was tested, (2) how it was tested (e.g., manual testing on a preview deployment, unit tests pass), and (3) any screenshots or logs if applicable. If automated tests were run, include the output. For UI changes, include before/after screenshots when possible.

- **Document concurrency and write-ordering implications for async replication patterns.** When implementing fire-and-forget background writes, dual-write replication, or any async worker pattern that processes writes concurrently, proactively document the write-ordering guarantees and data-consistency model in code comments. Address: (1) what happens when multiple workers write the same key concurrently, (2) whether the pattern is last-writer-wins, eventually consistent, or strictly ordered, (3) the impact of out-of-order writes on correctness, and (4) whether a replica serves reads during a migration. Reviewers will scrutinize concurrent write patterns for state-corruption risk — anticipate this. If the pattern has known consistency gaps (e.g., transient staleness during warmup), document them as accepted trade-offs rather than leaving them for reviewers to discover.

- **When modifying a function's return signature, update ALL call sites to handle the new return values.** When asked to add return values to a function (e.g., so callers can distinguish "action skipped" from "action failed"), systematically find ALL call sites and update each one to consume the new values. Some languages do not enforce that extra return values are consumed — callers can silently discard them. After changing a signature, search the codebase for every call site and verify each either uses the new values or explicitly documents why they are discarded. Never leave call sites silently ignoring new return values — it defeats the purpose of adding them.

- **Exclude structured string values (URLs, file paths, base64, JSON) from text-level transformations.** When applying string transformations to record fields — such as encoding, escaping, regex replacements, or character insertion — validate the semantic type of each value before transforming it. URLs, file paths, base64 data, and serialized JSON must be excluded because text-level modifications corrupt their structure (e.g., inserting characters into a URL breaks parsing, causing images to fail to load). Before implementing any bulk text transformation, add explicit detection logic (e.g., regex for `http(s)://`, `data:`, file extensions) to skip non-prose values. Test with real data that mixes value types (text + URLs + media references) to catch edge cases.

---

## Cross-Platform Bug Fixes

When fixing a bug reported on a specific platform, do NOT assume which platforms are affected based on code reading alone. Before implementing fixes across multiple SDKs (Android, iOS, Web, React Native, Flutter):

1. Check available QA data, crash reports, user agent strings, or platform-specific logs to identify which platforms actually exhibit the bug
2. Enumerate ALL platform SDKs and systematically verify which ones are impacted
3. Only implement fixes for confirmed affected platforms
4. If unsure, implement for the reported platform first and note which other platforms need verification
