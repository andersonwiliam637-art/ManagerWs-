-- ManagerW.S Phase 3: workflow de prestamista
CREATE TABLE IF NOT EXISTS loan_application (
 id SERIAL PRIMARY KEY, organization_id INTEGER NOT NULL REFERENCES organization(id), client_id INTEGER NOT NULL REFERENCES client(id),
 requested_amount NUMERIC(14,2) NOT NULL CHECK (requested_amount > 0), requested_term INTEGER NOT NULL CHECK (requested_term > 0),
 frequency VARCHAR(20) NOT NULL DEFAULT 'monthly', interest_rate NUMERIC(8,4) NOT NULL DEFAULT 0, interest_method VARCHAR(20) NOT NULL DEFAULT 'simple',
 purpose VARCHAR(255), status VARCHAR(20) NOT NULL DEFAULT 'pending', decision_note VARCHAR(500), reviewed_by INTEGER REFERENCES "user"(id), reviewed_at TIMESTAMP,
 created_by INTEGER NOT NULL REFERENCES "user"(id), created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_loan_application_org_status ON loan_application(organization_id,status);
ALTER TABLE credit ADD COLUMN IF NOT EXISTS application_id INTEGER REFERENCES loan_application(id);
CREATE INDEX IF NOT EXISTS ix_credit_application_id ON credit(application_id);
