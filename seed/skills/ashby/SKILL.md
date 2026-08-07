---
name: ashby
description: Ashby ATS — jobs, candidates, applications, interview stages, and application form data (read-only) — use when asked about hiring pipelines, open roles, candidates, reviewing/shortlisting applications for a job, resumes, or interview feedback in Ashby.
user-invocable: false
---

# Ashby — Jobs, Candidates & Applications (Read-Only)

Query the Ashby ATS for jobs, candidates, applications, interview stages, form
submissions, feedback, and resume download URLs. Use for hiring-pipeline
questions and for exporting applications for AI-assisted review.

## Available Commands

This is a **CLI tool** — invoke via Bash. Do NOT look for MCP tools.
Requires the `ASHBY_API_KEY` environment variable (read-only Ashby key).

### `check-auth` — Verify the key and endpoint access

```bash
python3 tools/ashby.py check-auth
```

### `list-jobs` — All jobs

```bash
python3 tools/ashby.py list-jobs
python3 tools/ashby.py list-jobs --status Open
```

Returns id, title, status, department/location IDs. Note: multiple jobs can
share a title (e.g. re-opened roles) — disambiguate by `status` and `createdAt`.

### `get-job` — Job details

```bash
python3 tools/ashby.py get-job 463d3765-d0db-4657-b312-92ae87e88128
```

### `list-stages` — Interview stages for a job

```bash
python3 tools/ashby.py list-stages --job-id JOB_ID
```

### `list-candidates` / `get-candidate`

```bash
python3 tools/ashby.py list-candidates --limit 100
python3 tools/ashby.py get-candidate CANDIDATE_ID
```

`get-candidate` includes a short-lived `resumeUrl` for downloading the resume.

### `list-applications` — All applications for a job (auto-paginates)

```bash
python3 tools/ashby.py list-applications --job-id JOB_ID
python3 tools/ashby.py list-applications --job-id JOB_ID --status Active
python3 tools/ashby.py list-applications --job-id JOB_ID --status Archived
```

Returns a `by_stage` breakdown plus one row per application (candidate, stage,
source, archive reason).

### `get-application` / `export-application`

```bash
python3 tools/ashby.py get-application APPLICATION_ID
python3 tools/ashby.py export-application APPLICATION_ID
```

`export-application` aggregates application + candidate profile + resume URL +
interview feedback into one JSON blob — the right input for AI review.

### `export-job-applications` — Bulk export for AI review

```bash
python3 tools/ashby.py export-job-applications --job-id JOB_ID --status Active --limit 50
```

Exports up to `--limit` (default 200) full application records for a job. For
large jobs, prefer reviewing in batches of 25-50 to keep context manageable.

## AI Review Workflow (shortlisting)

1. `list-jobs --status Open` → find the job ID.
2. `list-applications --job-id ID --status Active` → see pipeline counts.
3. `export-job-applications --job-id ID --status Active --limit 50` → full data.
4. Review resumes/answers against the role criteria, produce a shortlist with
   evidence per candidate.
5. Present the shortlist to the user for approval. **Loma never moves, rejects,
   or contacts candidates** — this integration is read-only; the human acts in
   the Ashby dashboard.

## Security Guardrails (do not work around these)

- **Read-only**: no write endpoints exist in the tool. Do not attempt stage
  moves, archiving, notes, or offers via this tool or raw curl.
- **Compensation is always redacted**: job compensation, salary/CTC/equity
  fields, and comp-related form answers come back as
  `[REDACTED:compensation-data-blocked-by-loma]`. Never attempt to bypass this
  via raw curl with the same key, and never echo redacted markers' surrounding
  context as if it were comp data.
- **Offer endpoints are blocked** locally by an allowlist AND the API key
  should not have `offersRead`/`offersWrite`. If a user needs offer data, tell
  them it requires a separate restricted HR key and explicit setup.
- Candidate PII (emails, phones, resumes) is sensitive: share only what the
  requesting user needs, and never post it to public channels.
