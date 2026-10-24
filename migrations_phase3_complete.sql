-- Phase 3 complete lender motor migration for PostgreSQL.
ALTER TABLE credit ADD COLUMN IF NOT EXISTS paid_late_fees NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE credit ADD COLUMN IF NOT EXISTS grace_days INTEGER NOT NULL DEFAULT 0;
ALTER TABLE credit ADD COLUMN IF NOT EXISTS late_fee_type VARCHAR(30) NOT NULL DEFAULT 'none';
ALTER TABLE credit ADD COLUMN IF NOT EXISTS late_fee_value NUMERIC(14,4) NOT NULL DEFAULT 0;
ALTER TABLE credit ADD COLUMN IF NOT EXISTS late_fee_cap NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE credit ADD COLUMN IF NOT EXISTS payment_order VARCHAR(80) NOT NULL DEFAULT 'late_fee,interest,principal';
ALTER TABLE credit ADD COLUMN IF NOT EXISTS parent_credit_id INTEGER REFERENCES credit(id);
ALTER TABLE loan_installment ADD COLUMN IF NOT EXISTS late_fee_paid NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE payment_allocation ADD COLUMN IF NOT EXISTS late_fee_amount NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE payment_allocation ADD COLUMN IF NOT EXISTS interest_amount NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE payment_allocation ADD COLUMN IF NOT EXISTS principal_amount NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE payment ADD COLUMN IF NOT EXISTS payment_method VARCHAR(30) NOT NULL DEFAULT 'cash';
ALTER TABLE payment ADD COLUMN IF NOT EXISTS collector_user_id INTEGER REFERENCES "user"(id);
ALTER TABLE payment ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(80);
ALTER TABLE payment ADD COLUMN IF NOT EXISTS receipt_number VARCHAR(60);
CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_idempotency_key ON payment(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_receipt_number ON payment(receipt_number) WHERE receipt_number IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_credit_parent_credit_id ON credit(parent_credit_id);
CREATE TABLE IF NOT EXISTS cash_session (
 id SERIAL PRIMARY KEY, organization_id INTEGER NOT NULL REFERENCES organization(id), collector_user_id INTEGER NOT NULL REFERENCES "user"(id),
 opened_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, closed_at TIMESTAMP, opening_balance NUMERIC(14,2) NOT NULL DEFAULT 0,
 closing_balance NUMERIC(14,2), counted_balance NUMERIC(14,2), difference NUMERIC(14,2), status VARCHAR(20) NOT NULL DEFAULT 'open', notes VARCHAR(255)
);
CREATE TABLE IF NOT EXISTS cash_movement (
 id SERIAL PRIMARY KEY, organization_id INTEGER NOT NULL REFERENCES organization(id), cash_session_id INTEGER NOT NULL REFERENCES cash_session(id),
 user_id INTEGER NOT NULL REFERENCES "user"(id), payment_id INTEGER REFERENCES payment(id), movement_type VARCHAR(30) NOT NULL,
 amount NUMERIC(14,2) NOT NULL, note VARCHAR(255), created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_cash_session_org_status ON cash_session(organization_id,status);
CREATE INDEX IF NOT EXISTS ix_cash_movement_session ON cash_movement(cash_session_id,created_at);
-- The partial unique index must be created after the table exists.
CREATE UNIQUE INDEX IF NOT EXISTS uq_open_cash_session_per_collector ON cash_session(organization_id, collector_user_id) WHERE status='open';
