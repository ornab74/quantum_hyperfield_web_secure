# Quantum Hyperfield Web — Hardened Flask/Docker Edition

This is the second implementation of the CQSE/TCQH network observatory. It is a
web application rather than a CustomTkinter desktop process.

It was architecturally inspired by the uploaded Roadscanner project in the
following limited ways:

- multi-stage Docker build;
- non-root runtime user;
- persistent `/var/data` root;
- Gunicorn application server;
- loopback-only host publishing by default;
- Docker health checks;
- read-only root filesystem;
- tmpfs for `/tmp`;
- dropped Linux capabilities;
- `no-new-privileges`;
- resource/PID limits;
- secrets kept outside the image.

No Roadscanner application logic, cryptographic code, or unrelated product
features are copied into this project.

## What the UI contains

The UI is fully local/static: no CDN runtime is required.

- Observatory overview with network metrics and PennyLane Q16 distribution.
- CQSE colorspace workbench:
  - semantic probe text;
  - color palette;
  - 96-D chromatic heatmap;
  - 32-state PennyLane chromatic probability vector;
  - OKLab manifold;
  - raw RGB/XYZ/OKLab/OKLCH coordinates.
- TCQH temporal hyperfield:
  - replayable worldline;
  - deterministic regime attractors;
  - transition boundaries;
  - quantum Jensen-Shannon state divergence.
- Weaviate memory explorer with BM25 + vector retrieval.
- Safe simulated cybersecurity dataset ingestion.
- Human approval queue.
- Container/runtime security posture surface.

CQSE and TCQH are experimental feature transforms. They are not presented as
validated scientific embedding standards or evidence of quantum advantage.

## Quick start

```bash
cp .env.example .env
```

Generate an Argon2 admin hash locally:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock.txt
python qnhctl.py hash-password
```

Put the resulting hash in `.env`:

```text
QNH_ADMIN_PASSWORD_HASH=$argon2id$...
```

Start:

```bash
docker compose build
docker compose up -d
```

Then open:

```text
http://127.0.0.1:8080/
```

The application is deliberately bound to loopback by default. Put an
authenticated TLS reverse proxy, VPN, or zero-trust tunnel in front of it for
remote access.

## Secure deployment properties

The application container uses:

```yaml
read_only: true
tmpfs:
  - /tmp:rw,nosuid,nodev,noexec
cap_drop:
  - ALL
security_opt:
  - no-new-privileges:true
pids_limit: 256
```

It runs as UID/GID `10001`.

Only `/var/data` is persistent and writable.

### Secret files

The application understands secret-file variables:

```text
QNH_SECRET_KEY_FILE=/run/secrets/flask_secret_key
OPENAI_API_KEY_FILE=/run/secrets/openai_api_key
QNH_ADMIN_PASSWORD_HASH_FILE=/run/secrets/admin_password_hash
WEAVIATE_API_KEY_FILE=/run/secrets/weaviate_api_key
```

This allows Docker/Kubernetes/host credential managers to mount files without
putting secrets in the image.

### HTTP hardening

The Flask layer adds:

- CSRF validation;
- SameSite=Strict and HttpOnly sessions;
- optional Secure cookies;
- strict CSP that needs no CDN;
- `X-Frame-Options: DENY`;
- `X-Content-Type-Options: nosniff`;
- restrictive Permissions-Policy;
- no-store caching;
- optional HSTS;
- request-body cap;
- login and API throttling.

Set these behind HTTPS:

```text
QNH_COOKIE_SECURE=1
QNH_HSTS=1
QNH_TRUST_PROXY=1
```

Do not enable `QNH_TRUST_PROXY` unless the app is actually behind a trusted
reverse proxy.

## Weaviate

The included Weaviate service has no host port. It is only reachable through
the internal Docker network.

The default Compose configuration therefore uses anonymous access *inside that
private network*. For multi-tenant or shared Docker environments, enable
Weaviate API-key or OIDC authentication and configure `WEAVIATE_API_KEY`.

The web app creates purpose-built named-vector collections for telemetry,
incidents, action playbooks, dataset memories, CQSE surfaces, and temporal
hyperfields.

## OpenAI embeddings

If `OPENAI_API_KEY` (or `OPENAI_API_KEY_FILE`) is configured,
`text-embedding-3-large` is used as an additional named semantic vector. If it
is not configured, the CQSE/quantum surfaces remain available.

The code never tries to extract ChatGPT/Codex browser/session credentials.

## Datasets

The web build exposes **simulation only**. This is intentional.

It can generate bounded synthetic corpora representing:

1. LANL unified host/network concepts
2. LANL multi-source events
3. LANL user/computer authentication
4. CIC intrusion data family
5. KDD Cup 1999
6. the linked network-malware connection-analysis concept

The web API contains no pickle/joblib/notebook/script execution or arbitrary
archive extraction path.

Use the desktop edition's explicit sandboxed tabular importer if you need real
Kaggle CSV/TSV/JSONL/Parquet ingestion.

## Human approval

`/api/actions` only creates and transitions approval records. There is no API
that runs arbitrary shell commands from an LLM action block.

That separation is deliberate.

## Audit chain

Login attempts, memory initialization, CQSE generation, temporal-field builds,
dataset simulation and action state changes are stored in SQLite with a
previous-hash/event-hash linkage.

It is tamper-evident bookkeeping, not an immutable external ledger.

## Optional local ML

The default container intentionally avoids shipping a huge PyTorch/Transformers
stack. `requirements-ml.txt` is included for installations that want local
Contriever or other transformer experiments.

Gemma 4 `.litertlm` should be run with a compatible LiteRT-LM runtime; it is not
a llama.cpp format. Keep model artifacts under `/var/data/models` and verify
their pinned SHA-256 before use.

## Development without Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock.txt

export QNH_BOOTSTRAP_ADMIN_PASSWORD='a-long-local-development-password'
python main.py
```

Open `http://127.0.0.1:8080`.

The built-in Flask server is for development only; production should use the
included Gunicorn container command.


## Advanced local-ML image

The package includes `Dockerfile.ml`. It adds PyTorch + Transformers so the
optional Facebook/Meta `facebook/contriever-msmarco` 768-D vector space can be
enabled:

```bash
docker build -f Dockerfile.ml -t quantum-hyperfield-web:ml .
```

Then set:

```text
QNH_ENABLE_CONTRIEVER=1
```

This image is intentionally separate because PyTorch materially increases image
size and attack/dependency surface.

## Gemma integrity surface

The UI exposes Gemma verification state. The configured model artifact is
accepted only when SHA-256 equals:

```text
ab7838cdfc8f77e54d8ca45eadceb20452d9f01e4bfade03e5dce27911b27e42
```

Model download is disabled by default. To explicitly permit it:

```text
QNH_ALLOW_MODEL_DOWNLOADS=1
```

The download path uses HTTPS, resumable Range requests, size consistency checks,
streaming SHA-256 verification, deletion on mismatch, and atomic rename.

The `.litertlm` artifact is treated as a LiteRT-LM model. It is not passed to
llama.cpp.

## Structured AI analysis

The web edition does not hard-code a potentially stale reasoning model ID.
Configure the current model available to your OpenAI project:

```text
OPENAI_API_KEY=...
QNH_REASONING_MODEL=...
QNH_REASONING_EFFORT=high
```

The **AI Analysis Chamber** then combines deterministic telemetry, CQSE,
temporal hyperfield structure and retrieved memories and requests the structured
`[status]`, `[observation]`, `[evidence]`, `[risk]`, `[action]`,
`[replytemplate]`, `[confidence]`, `[final]` protocol.

No model-generated action is automatically executed.


## Dependency security

See `DEPENDENCY_LOCK.md` for exact direct pins and the immutable Contriever safetensors revision.
