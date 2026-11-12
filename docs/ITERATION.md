# Reliability iteration — 2026-11-12

## Delivered scope

- Persistent LangGraph SQLite checkpoints (`durability=sync`), original workflow definition fingerprints, renewable per-run execution leases, native human-review interrupts, crash recovery and explicit failed/partial/cancelled task status.
- Per-node operation journal. Completed effects reuse their recorded result. A crash or timeout with an uncertain tool effect blocks automatic replay; an administrator must reconcile it with external evidence.
- Immediate node-event persistence; recovery and reconciliation APIs live in `app/api/recovery.py`, separate from general studio routes.
- Independent PostgreSQL vector and full-text recall; ACL predicates run before both candidate limits. Empty or unsupported queries can abstain. Malformed/empty ACLs deny access.
- Server-issued bearer credentials (or browser-native HTTP Basic), hashed tokens, revocation and database-backed project/team membership. Client actor/role headers are not trusted. Private memories retain private ACLs when indexed.
- Loopback-only personal mode. Shared-mode members can use authorized knowledge/memory APIs; filesystem scans, tool execution, global configuration and workflow operations remain administrator-only. This is not an untrusted-code hosting service.
- Extracted workflow/report presentation helpers from the large React entrypoint without changing its layout.

## Reproduce acceptance

Install with `python -m pip install -c security-constraints.txt -e ".[dev,vector]"` and run:

```powershell
python scripts/verify_iteration.py -q
python scripts/verify_iteration.py --rag-eval
npm --prefix web ci
npm --prefix web run build
```

The verifier switches to a temporary working directory and sets `APP_ENV_FILE` to an absent file. Developer databases and model keys are not needed. PostgreSQL tests are explicitly opt-in:

```powershell
docker run --detach --rm --name devagent-iteration-pg --publish 127.0.0.1:15438:5432 --env POSTGRES_DB=iteration_test --env POSTGRES_USER=iteration --env POSTGRES_PASSWORD=iteration-test-only pgvector/pgvector:pg17
$env:DEV_AGENT_TEST_PGVECTOR='1'
$env:PGVECTOR_DATABASE_URL='postgresql://iteration:iteration-test-only@127.0.0.1:15438/iteration_test'
python scripts/verify_iteration.py -q
docker stop devagent-iteration-pg
```

Use only a disposable test database. CI provisions these dependencies automatically.

## Recovery contract

`POST /api/v1/tasks/{task_id}/recover` resumes the persisted workflow. `GET /api/v1/tasks/{task_id}/recovery` shows its status and lease. A live lease returns HTTP 409; after a process dies it expires (90 seconds by default, configurable with `DEV_AGENT_RUN_LEASE_SECONDS`). A changed workflow definition requires a new task.

Use the existing approve/reject endpoints for pending review. Repeated approval/recovery does not replay completed nodes. For parallel reviews, one interrupt is approved per request.

For an uncertain external effect, inspect `GET /api/v1/tasks/{task_id}/operations`, verify the external system, then submit:

```json
{"outcome":"completed","reason":"Verified external receipt 42","output":{"receipt":42},"extra":{}}
```

to `POST /api/v1/tasks/{task_id}/operations/{operation_id}/reconcile`. Alternatively use `outcome=not_executed` only after verifying that no effect occurred. Decisions are audited. This is conservative replay protection, not a claim of exactly-once execution across arbitrary external systems. Set `read_only=true` only for genuinely read-only tools.

## Shared deployment

Provision credentials locally (the token is printed once; do not commit it):

```powershell
python -m app.security create-user operator --role admin
python -m app.security create-user alice
python -m app.security grant alice team team-one editor
$env:DEV_AGENT_AUTH_MODE='required'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Put a trusted HTTPS reverse proxy in front of shared deployments. API clients use `Authorization: Bearer TOKEN`; browsers can use the HTTP Basic challenge with actor ID and token. `python -m app.security revoke alice` revokes credentials. `DEV_AGENT_AUTH_DB` and `DEV_AGENT_CHECKPOINT_DB` select persistent files. Back up both the checkpoint database and its `.journal` companion. Restrict filesystem permissions on these files. Production refuses personal mode.

## Evaluation interpretation

`tests/data/rag_reliability_gold.json` contains 100 assistant-curated synthetic regression cases over 20 short bilingual documents: 80 answerable queries, 10 no-answer and 10 ACL-denied queries. It is **not** human-labeled production traffic or an independent generalization benchmark. The offline ablation deliberately uses `hash_fallback`, not a paid semantic embedding API.

Local run: lexical Recall@3=1.00/MRR=1.00; hash-vector Recall@3=0.25/MRR=0.25; hybrid Recall@3=1.00/MRR=0.9875. All modes abstained on the designated negative/ACL cases. Precision@3 is 0.3333 for lexical/hybrid because each positive query has one relevant document. Hybrid did not beat lexical-only MRR on this fixture. Real semantic-model quality still requires independent representative queries and recorded model/version settings.

Measured local hybrid P95 was 3.30 ms in the final in-memory ranking ablation (not API, network, PostgreSQL, or model latency). Tests separately verify real PostgreSQL ACL-before-limit behavior and lexical rescue outside the vector shortlist.

## Acceptance evidence

- Full Python suite with PostgreSQL: 29 passed after the dependency security updates.
- Subprocess `os._exit` test: completed predecessor ran once; interrupted node resumed after lease expiry.
- Authentication, spoofed identities, cross-user memory access, membership and CSRF tests passed.
- TypeScript/Vite production build and correctness lint passed.
- Dependency lock refreshed within compatible versions; npm audit and pip-audit reported no known vulnerabilities. `pip check` reported no broken requirements.
- GitHub Actions repeats backend, PostgreSQL, evaluation, build and dependency gates.

No production deployment, paid model benchmark, or real external side-effect reconciliation is performed by these tests.
