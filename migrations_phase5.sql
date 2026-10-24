-- ManagerW.S Phase 5 — financial integrity constraints
-- Apply after phase4b migration. Test in a staging database first.

ALTER TABLE loan_application
  ADD CONSTRAINT ck_application_frequency CHECK (frequency IN ('weekly','biweekly','monthly')),
  ADD CONSTRAINT ck_application_interest_method CHECK (interest_method IN ('simple','amortized')),
  ADD CONSTRAINT ck_application_rate_range CHECK (interest_rate >= 0 AND interest_rate <= 100);

ALTER TABLE credit
  ADD CONSTRAINT ck_credit_frequency CHECK (frequency IN ('weekly','biweekly','monthly')),
  ADD CONSTRAINT ck_credit_interest_method CHECK (interest_method IN ('simple','amortized')),
  ADD CONSTRAINT ck_credit_rate_range CHECK (interest_rate >= 0 AND interest_rate <= 100),
  ADD CONSTRAINT ck_credit_paid_late_fees_nonnegative CHECK (paid_late_fees >= 0),
  ADD CONSTRAINT ck_credit_grace_days_nonnegative CHECK (grace_days >= 0),
  ADD CONSTRAINT ck_credit_late_fee_values_nonnegative CHECK (late_fee_value >= 0 AND late_fee_cap >= 0),
  ADD CONSTRAINT ck_credit_late_fee_type CHECK (late_fee_type IN ('none','fixed','percent_daily','percent_installment')),
  ADD CONSTRAINT ck_credit_status CHECK (status IN ('active','overdue','paid','restructured','canceled'));

ALTER TABLE loan_installment
  ADD CONSTRAINT ck_installment_components_nonnegative CHECK (principal_due >= 0 AND interest_due >= 0 AND late_fee_due >= 0 AND late_fee_paid >= 0),
  ADD CONSTRAINT ck_installment_amount_reconciles CHECK (amount_due = principal_due + interest_due),
  ADD CONSTRAINT ck_installment_status CHECK (status IN ('pending','partial','paid','overdue'));

ALTER TABLE payment_allocation
  ADD CONSTRAINT ck_allocation_components_nonnegative CHECK (late_fee_amount >= 0 AND interest_amount >= 0 AND principal_amount >= 0),
  ADD CONSTRAINT ck_allocation_amount_reconciles CHECK (amount = late_fee_amount + interest_amount + principal_amount);

ALTER TABLE payment
  ADD CONSTRAINT ck_payment_status CHECK (status IN ('posted','reversed')),
  ADD CONSTRAINT ck_payment_method CHECK (payment_method IN ('cash','bank','transfer','card','mobile','other'));

CREATE INDEX IF NOT EXISTS ix_credit_org_client_status ON credit (organization_id, client_id, status);
CREATE INDEX IF NOT EXISTS ix_payment_org_credit_date ON payment (organization_id, credit_id, date);
CREATE INDEX IF NOT EXISTS ix_allocation_org_installment ON payment_allocation (organization_id, installment_id);
