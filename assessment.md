# WORK TRIAL
## An asset library in Loma
Everything you need: the problem, how to get set up, and what to send back.

| REPO | WINDOW | DELIVERABLE | QUESTIONS |
| :--- | :--- | :--- | :--- |
| `plotlinelabs/loma` | 24 hours | PR (Loom optional) | Email |

---

## 01 | The problem

Loma is an AI agents platform. It creates files and documents while working on tasks, such as reports, spreadsheets and images. Today, those outputs are spread across conversations. Finding something again often means remembering which conversation produced it and searching through the messages.

**Build an asset library:** a place where users can find outputs they have access to, open or download them, and return to the conversation they came from.

Build on the existing file handling and preview capabilities, and extend the current dashboard design. A barebones, working experience is enough. How users find their files and move between the library, an output and its source conversation is yours to decide.

### Keep the scope focused
You do not need to introduce a new storage service or migrate historical files. Focus on file-backed outputs available through the existing application. Uploads, public sharing, folder hierarchies and new file-generation tools are not required.

If existing file handling has limitations, explain them and make a reasonable, documented choice about what your implementation supports.

### Access is part of the problem
Users should only be able to access files they are permitted to see. Enforce access on the backend, not just through what the interface displays. Build on the existing access rules rather than assuming that appearing in the library makes a file safe to open.

### The design decisions are yours
The brief is open on purpose. There is no hidden interface or architecture we expect you to reproduce. How you organise the experience, model the data and connect the pieces is yours to decide.

We care about your decisions, how the implementation handles situations beyond the happy path, and whether the approach can grow sensibly. Usability matters; visual polish is secondary. Depth beats breadth. A coherent working feature is more valuable than a broad but unfinished asset-management product.

---

## 02 | Getting set up

Do this before starting the clock. Setup is not part of the assessment. Use an isolated development database and dummy content, never production credentials or customer files.

You need: repository access, Docker with Compose, and MongoDB (Atlas or a separate local container; it is not bundled in Compose). A working model connection is useful for creating sample outputs. Confirm the key/provider with us before starting.

Node and Python are only needed on the host if you run outside Docker.

### 1. Clone and create your environment files
```bash
git clone https://github.com/plotlinelabs/loma.git && cd loma
cp .env.example .env
cp dashboard/.env.example dashboard/.env
```

### 2. Set these values in the root `.env`
```env
ENV=DEV
OBSERVABILITY_MONGODB_URI=<your development MongoDB URI>
OBSERVABILITY_DB_NAME=loma_trial
LOMA_SETUP_TOKEN=<a random setup token>
OPENCODE_API_KEY=<your OpenCode key>
SLACK_BOT_TOKEN=xoxb-placeholder
LOMA_ENABLE_SLACK=false
LOMA_ENABLE_SCHEDULER=false
LOMA_ENABLE_METRICS=false
NEXT_PUBLIC_AUTH_PROVIDER=local
PUBLIC_BASE_URL=http://localhost:3001
NGINX_HOST_PORT=8080
NGINX_HOST_BIND=127.0.0.1
```
Retain the default model setting from `.env.example` and leave remote workers off. MongoDB must be reachable from both containers; `localhost` inside a container is not your host. For Atlas, allow your development machine's IP and use a database user with access to your trial database.

### 3. Configure `dashboard/.env` too
Set the same MongoDB URI, database name and setup token in `dashboard/.env`. Set `AUTH_PROVIDER=local`, `AUTH_URL=http://localhost:3001`, and replace the placeholder `AUTH_SECRET` with a random secret, for example from `openssl rand -hex 32`. Keep the existing Docker backend URL and leave `NEXT_PUBLIC_API_URL` blank.

### 4. Override host mounts and start
For macOS, or hosts without the default Linux directories, add these root `.env` values. Create the directories first; use an empty SSH directory for this trial.
```env
LOMA_SECRETS_DIR=./.local/secrets
LOMA_SSH_DIR=./.local/ssh
```
```bash
mkdir -p .local/secrets .local/ssh
docker compose up --build
```
Open `http://localhost:3001` and use the setup-token option on the sign-in screen to create your first admin account. Email us if setup blocks you.

---

## 03 | Groundwork & Submission

### Understand the existing experience
In Loma, an artifact is an output associated with a conversation. Some contain inline text or code; others point to a generated file. This task concerns the latter. Open a conversation with a file output and explore its existing preview and download experience before building.

### Create your local reference data
With a working model connection, ask in Chat: *"Create a small PDF report using fictional data and return a download link."* Repeat in another conversation with an image or spreadsheet. Confirm that the outputs are registered and can be opened, rather than only seeing a filename in a message.

You may instead use a small, repeatable local fixture script that registers dummy files through existing application code and records their artifact metadata. No model-powered functionality is required in the library itself. Include fixture instructions so we can reproduce your demo.

You are ready to build when you can sign in and open a sample file from a conversation. If using Chat to create samples, first confirm that your configured model can reply.

### Working with existing files
Some existing file-download registrations are temporary, even though artifact metadata is saved. You are not expected to replace that infrastructure. Use the existing file handling for your demonstration, explain its limitations and include any steps needed to recreate your sample files.

---

## 04 | What to send back

A pull request against `main` on `plotlinelabs/loma`, with a working UI and backend, relevant tests, CI green, screenshots and instructions to run your demo.

Commit a short `DESIGN.md` before implementation, explaining your intended approach. Put it in a trial-specific directory so it does not replace the dashboard's existing design guide.

Commit `NOTES.md` at the end, answering:
* What did you try that did not work, and what did you do instead?
* Which part is weakest, and what would break it first?
* What did you deliberately not build, and why?

A Loom or video walkthrough is optional. If you skip it, let `NOTES.md` explain the implementation and tradeoffs. We will review the code, run the feature and arrange a follow-up conversation.

*Use any tools you like, including AI coding tools. Be ready to explain any part of your diff. Depth beats breadth. Where a decision is ambiguous, state your assumption and proceed. Email us with questions.*