# Contributing to Loma

Thanks for helping improve Loma.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd dashboard && npm install
```

Run backend tests from the repo root:

```bash
pytest
```

Build the dashboard:

```bash
cd dashboard
npm run build
```

## Pull requests

- Keep company-specific knowledge out of source code.
- Do not commit credentials, `.env` files, customer data, private prompts, or private playbooks.
- Prefer generic configuration hooks over hardcoded company behavior.
