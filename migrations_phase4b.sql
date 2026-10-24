-- ManagerW.S Phase 4B hardening
-- Run against the existing PostgreSQL schema after backing up.

ALTER TABLE credit
    ADD CONSTRAINT uq_credit_org_application UNIQUE (organization_id, application_id);

ALTER TABLE payment
    DROP CONSTRAINT IF EXISTS payment_idempotency_key_key;

ALTER TABLE payment
    ADD CONSTRAINT uq_payment_org_idempotency UNIQUE (organization_id, idempotency_key);

CREATE INDEX IF NOT EXISTS ix_payment_org_status ON payment (organization_id, status);
CREATE INDEX IF NOT EXISTS ix_installment_org_due_status ON loan_installment (organization_id, due_date, status);
CREATE INDEX IF NOT EXISTS ix_ledger_org_created ON ledger_entry (organization_id, created_at);
CREATE INDEX IF NOT EXISTS ix_cash_movement_session_created ON cash_movement (cash_session_id, created_at);
