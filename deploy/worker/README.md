# Isolated worker build and deployment

Run these steps on an approved **dedicated Docker/gVisor host**, not the Loma
backend host. This repository does not provision a host, grant network access,
install runsc, or enable production cutover.

1. Install Docker/buildx and runsc on the dedicated host. Configure Docker's
   `runsc` runtime. Firewall TCP 8443 to backend addresses only.
2. Authenticate Docker to the approved registry. Build both images from a
   clean checkout using an allowlisted temporary context:
   ```sh
   python3 deploy/worker/build.py --repository registry.example.test/loma \
     --tag pr191-reviewed --output images.env
   ```
   This publishes two images. `images.env` is written atomically only after
   both builds return immutable registry digests. Never substitute mutable tags.
3. Pull the native digest onto the **same daemon** the supervisor will control:
   ```sh
   set -a; . ./images.env; set +a
   docker pull "$LOMA_WORKER_IMAGE"
   ```
4. Prepare a random control token (at least 32 characters), server TLS cert/key,
   and trusted backend client CA as files readable only by the operator. Set
   their absolute paths in `supervisor.env`, along with the private bind IP:
   ```text
   LOMA_WORKER_BIND_IP=10.0.0.10
   LOMA_WORKER_CONTROL_TOKEN_PATH=/etc/loma-worker/control-token
   LOMA_WORKER_SERVER_CERT_PATH=/etc/loma-worker/server.crt
   LOMA_WORKER_SERVER_KEY_PATH=/etc/loma-worker/server.key
   LOMA_WORKER_CLIENT_CA_PATH=/etc/loma-worker/client-ca.crt
   ```
5. Validate and start only after operator approval:
   ```sh
   docker compose --env-file images.env --env-file supervisor.env \
     -f deploy/worker/compose.yaml config --quiet
   docker compose --env-file images.env --env-file supervisor.env \
     -f deploy/worker/compose.yaml up -d
   ```
   Supervisor startup requires runsc and the pinned worker image; it never
   falls back to runc. It removes abandoned labelled workers on that dedicated
   daemon. Do not run two supervisors against the same daemon.
6. Configure the backend's `LOMA_WORKER_URL` (wss, `/v1/run`), client TLS
   credentials, server CA, matching control token, account lists, and persistent
   artifact directory per `.env.example`. Do not enable `LOMA_REMOTE_WORKERS`
   yet. Test authenticated health, hostile-worker containment, disconnect/kill
   cleanup, all native runtimes, live OAuth refresh and usage settlement first.
7. After certification and a drain, an operator may enable cutover. To roll
   back, drain remote runs, disable cutover and restore the previous deployment.
   Never interrupt an uncertain provider write and automatically retry it.

The supervisor alone mounts the dedicated Docker socket. Workers receive no
socket, host mounts, credentials or network; they run read-only under runsc.
Code/configuration checks are **not** evidence of an image build or containment
certification. Record build digests and dedicated-host test output on the PR.
