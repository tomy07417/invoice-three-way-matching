# Run durable invoice processing

The opt-in backend supports authenticated private uploads, durable Azure extraction and atomic deterministic evaluation. This is a local-demo backend, not a production authentication service. The Intake frontend is the next PRD.

## Quick path

1. Export PostgreSQL configuration and backend-only `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT`. Use the exact supplied endpoint and assigned deployment. Never expose these through `NEXT_PUBLIC_*`.
2. Migrate explicitly; startup never creates tables:
   ```sh
   uv run --project backend alembic -c backend/alembic.ini upgrade head
   ```
3. Provision an Intake account; the password is prompted, not passed as a command-line argument:
   ```sh
   PYTHONPATH=backend backend/.venv/bin/python -m app.interfaces.cli.provision_user --username intake-demo --role intake
   ```
   For automation, `--password-env VARIABLE_NAME` reads an explicitly supplied environment variable. Existing accounts cannot be overwritten. Roles are `intake`, `ap_analyst`, `ap_manager`.
4. Set `INVOICE_PROCESSING_ENABLED=1` and `INVOICE_STORAGE_ROOT` to a private directory in your project/deployment volume, then start the backend using the README command. `DATABASE_URL` takes precedence over the existing PostgreSQL component variables.
5. Log in through `POST /sessions` with JSON `username`/`password`. Keep the HttpOnly session cookie. `GET /sessions/current` provides a CSRF token; send it as `X-CSRF-Token` on authenticated mutations.

Existing health/PO routes still work without processing configuration. Processing defaults to disabled. Local HTTP uses `SESSION_SECURE_COOKIE=0`; HTTPS deployment must use `1`.

Compose forwards Azure variables only to the backend and persists originals in `invoice_documents`. After migrating, provision with:

```sh
docker compose exec backend /app/.venv/bin/python -m app.interfaces.cli.provision_user --username intake-demo --role intake
```

Do not remove persistent volumes to recover from processing errors. The environment example was not modified; required feature variables are listed here.

## API

| Endpoint | Contract |
| --- | --- |
| `POST /invoices` | Multipart `file`, cookie, CSRF and `Idempotency-Key`; returns 202 with stable `invoice_id`/`job_id`. Same key/content reuses the job; different content conflicts. |
| `GET /invoices` | Owner-filtered list; optional technical `status`, `offset`, `limit`. |
| `GET /invoices/{id}` | Extracted fields, exact money strings, evaluation and explanatory findings. |
| `GET /invoices/{id}/processing` | Attempts, next eligible execution and state events. |
| `GET /invoices/{id}/document` | Authenticated private original, never a public storage URL. |
| `POST /invoices/{id}/retry` | Reports already-scheduled work idempotently; never advances eligibility or resets the attempt budget. Terminal/exhausted work returns 409. |
| `DELETE /sessions/current` | CSRF-protected logout. |

Intake sees only its own uploads; AP roles can inspect cases. Automatic outcomes are separate from technical states. Incomplete evidence is not `Rejected` or implicit `Auto-Pay`.

File defaults are 8 MiB, 5 PDF pages and 16 megapixels. Encrypted/malformed PDFs, unsupported/truncated images, MIME/extension mismatches and excessive dimensions are rejected. Filenames never determine storage paths. Credentials, provider error bodies and invoice text are not logged by these adapters.

## Retry and recovery

- **Three total provider attempts** by default, including the initial call. Timeout/429/5xx and malformed responses are retryable; authentication/unsupported requests are terminal.
- Local backoff: `min(60, 2 × 2^(attempt−1))` seconds. Add independent uniform jitter from 0 to 50% of that backoff. `Retry-After` or the token-window reopening time is a mandatory minimum **before** jitter. Example windows: 2–3 seconds, then 4–6 seconds. A shared 60-second provider floor gives 60–61 seconds, then 60–62 seconds rather than synchronized calls.
- `RETRY_WAIT`, attempts and `next_eligible_at` live in PostgreSQL. The worker holds no transaction/connection during waits or Azure calls. Local token deferrals do not consume provider attempts.
- Global defaults: `MAX_ACTIVE_EXTRACTIONS=1`, `MAX_PENDING_INVOICES=20`, 10,000 tokens per rolling 60-second window. A database control row coordinates admission, claims and reservations. Failed calls retain the estimated charge; observed usage can increase, never reduce it. Reservation estimates are not an exact tokenizer guarantee: provider 429 remains authoritative.
- Extraction leases last 120 seconds. Provider deadline is at most 90 seconds and is further bounded by remaining lease minus a settlement margin. Slow preparation cannot invoke Azure with an expired lease; stale results are fenced out.
- Restarted workers recover expired extraction attempts without resetting their count. Extracted data is persisted before evaluation. Evaluation and job linkage commit atomically, preventing duplicate evaluation after a crash. Lease expiry during evaluation becomes an explicit technical failure rather than an infinite loop.
- Only exact unique tax-ID identity, explicit PO-local line association and explicitly net prices establish comparison evidence. No fuzzy matching, currency conversion or tax inference is invented.

## Verification

Use an explicitly isolated `TEST_DATABASE_URL` whose database name starts with `test_`. Tests create/drop unique databases; never point this at an application database:

```sh
PYTHONPATH="$PWD/backend:$PWD" backend/.venv/bin/python -m pytest backend/tests
```

Without the test database variable, PostgreSQL tests skip. Retry tests use simulated providers and clocks, not Azure load. A real extraction is a separate limited check and does not prove every extracted field is accurate.

## Limitations

The MVP runs one async processing loop and bounded blocking offload, with no external queue. Session throttling/account recovery, exhausted-job reset, operational dashboards and high-throughput deployment tuning are not implemented. Private local disk is not encrypted by this application. Human decisions, corrections and payment approvals are later work; `Auto-Pay` never executes payment.
