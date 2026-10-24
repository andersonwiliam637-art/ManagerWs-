# ManagerWS — Phase 14 billing core (candidato de staging)

ManagerW.S is being rebuilt as a multi-tenant SaaS with a shared core and vertical modules for lenders and distributors.

## Phase 1 + Phase 2

### Security / SaaS foundation
- Organization + Membership multi-tenancy.
- Prepared roles: owner, admin, manager, seller, collector, accountant, viewer.
- Password hashing with PBKDF2-SHA256.
- Email verification and password reset tokens.
- CSRF protection and rate limits.
- Hardened session cookies and production security headers.
- No default administrator password.
- Administrative mutations use POST + CSRF.
- Audit log.
- Production requires explicit secrets/database/SMTP configuration.

### Financial core
- Money stored as `NUMERIC(14,2)`.
- Credit contract stores principal, total receivable, annual rate, interest method, term, frequency and dates.
- `LoanInstallment` stores the individual payment schedule.
- `PaymentAllocation` records exactly which installments receive each payment.
- Payments are allocated FIFO to oldest unpaid installments.
- `LedgerEntry` records disbursement, payment and reversal events.
- Financial payments are reversed rather than silently deleted.
- PostgreSQL row locks protect concurrent payment operations.
- Overdue installments and credit status are refreshed from schedule state.
- Credit detail page shows schedule and ledger.

## Current financial conventions

- Rate is annual percentage. `24` means 24% annual.
- `simple`: flat/simple interest prorated across periods.
- `amortized`: periodic amortization.
- Monthly installments use calendar-month arithmetic, including safe end-of-month rollover (for example, Jan 31 → Feb 28/29).
- Late fees are calculated from the configured grace period, fee type/value, and optional cap when installments become overdue.

## Billing status

The Flask backend contains a PayPal recurring-subscription checkout and signed webhook handler. PayPal checkout is disabled by default and the backend validates the active plan's monthly frequency, currency and amount against configuration before creating a subscription. See `PAYPAL_LIVE_HANDOFF.md` for the Live account handoff. The repository shared for deployment (`andersonwiliam637-art/ManagerWs-`) was empty at the last inspection. This package has not been deployed and no Live transaction was tested. Recurrente is **not wired into the Flask login/UI flow**; legacy Supabase Edge Functions are out of this billing path. `PaymentRequest` remains a temporary manual bridge.

## Database migrations

Flask-Migrate/Alembic is installed, but `migrations/` does not yet contain a versioned Alembic environment/revisions. Treat schema migration as a release blocker; do not assume `flask db upgrade` will modify the existing Supabase schema. Review `MIGRATION_BLOCKERS_AND_PLAN.md` before deployment.

Never use `AUTO_CREATE_DB=true` in production.

## Local execution

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export AUTO_CREATE_DB=true
python app.py
```

For production use PostgreSQL, Redis-backed rate limiting, HTTPS and SMTP. Do not deploy until the remaining security and financial test phases are complete.

## Tests

Development tests use pytest. Install the development requirements and run the full test suite:

```bash
python -m pip install -r requirements-dev.txt
python -m py_compile app.py financial.py
python -m pytest -q
```
