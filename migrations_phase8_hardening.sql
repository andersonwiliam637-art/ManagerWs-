-- ManagerW.S Phase 8 — PostgreSQL hardening
-- Apply AFTER phase5 constraints, in staging first.
-- Financial records are append-only: corrections happen through reversal/adjustment entries.

CREATE INDEX IF NOT EXISTS ix_audit_org_created
  ON audit_log (organization_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_ledger_org_credit_created
  ON ledger_entry (organization_id, credit_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_cash_org_session_created
  ON cash_movement (organization_id, cash_session_id, created_at DESC);

CREATE OR REPLACE FUNCTION managerws_reject_financial_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'Immutable financial/audit record: % on % is not permitted', TG_OP, TG_TABLE_NAME
    USING ERRCODE = 'restrict_violation';
END;
$$;

DROP TRIGGER IF EXISTS trg_ledger_entry_immutable ON ledger_entry;
CREATE TRIGGER trg_ledger_entry_immutable
BEFORE UPDATE OR DELETE ON ledger_entry
FOR EACH ROW EXECUTE FUNCTION managerws_reject_financial_mutation();

DROP TRIGGER IF EXISTS trg_payment_allocation_immutable ON payment_allocation;
CREATE TRIGGER trg_payment_allocation_immutable
BEFORE UPDATE OR DELETE ON payment_allocation
FOR EACH ROW EXECUTE FUNCTION managerws_reject_financial_mutation();

DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log;
CREATE TRIGGER trg_audit_log_immutable
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION managerws_reject_financial_mutation();
