-- FASE 9: aislamiento SaaS a nivel de PostgreSQL.
-- Requiere que la aplicación use managerws y que las filas existentes respeten organization_id.
ALTER TABLE managerws.membership
  ADD CONSTRAINT uq_membership_org_user_pair UNIQUE (organization_id, user_id);

ALTER TABLE managerws.client ADD CONSTRAINT uq_client_org_id_pair UNIQUE (organization_id, id);
ALTER TABLE managerws.loan_application ADD CONSTRAINT uq_loan_application_org_id_pair UNIQUE (organization_id, id);
ALTER TABLE managerws.credit ADD CONSTRAINT uq_credit_org_id_pair UNIQUE (organization_id, id);
ALTER TABLE managerws.loan_installment ADD CONSTRAINT uq_installment_org_id_pair UNIQUE (organization_id, id);
ALTER TABLE managerws.payment ADD CONSTRAINT uq_payment_org_id_pair UNIQUE (organization_id, id);
ALTER TABLE managerws.cash_session ADD CONSTRAINT uq_cash_session_org_id_pair UNIQUE (organization_id, id);

ALTER TABLE managerws.payment_request ADD CONSTRAINT fk_payment_request_org_user
  FOREIGN KEY (organization_id, user_id) REFERENCES managerws.membership (organization_id, user_id);
ALTER TABLE managerws.subscription ADD CONSTRAINT fk_subscription_org_user
  FOREIGN KEY (organization_id, user_id) REFERENCES managerws.membership (organization_id, user_id);
ALTER TABLE managerws.client ADD CONSTRAINT fk_client_org_user
  FOREIGN KEY (organization_id, user_id) REFERENCES managerws.membership (organization_id, user_id);
ALTER TABLE managerws.loan_application ADD CONSTRAINT fk_loan_application_org_client
  FOREIGN KEY (organization_id, client_id) REFERENCES managerws.client (organization_id, id);
ALTER TABLE managerws.loan_application ADD CONSTRAINT fk_loan_application_org_creator
  FOREIGN KEY (organization_id, created_by) REFERENCES managerws.membership (organization_id, user_id);
ALTER TABLE managerws.loan_application ADD CONSTRAINT fk_loan_application_org_reviewer
  FOREIGN KEY (organization_id, reviewed_by) REFERENCES managerws.membership (organization_id, user_id);
ALTER TABLE managerws.credit ADD CONSTRAINT fk_credit_org_user
  FOREIGN KEY (organization_id, user_id) REFERENCES managerws.membership (organization_id, user_id);
ALTER TABLE managerws.credit ADD CONSTRAINT fk_credit_org_client
  FOREIGN KEY (organization_id, client_id) REFERENCES managerws.client (organization_id, id);
ALTER TABLE managerws.credit ADD CONSTRAINT fk_credit_org_application
  FOREIGN KEY (organization_id, application_id) REFERENCES managerws.loan_application (organization_id, id);
ALTER TABLE managerws.loan_installment ADD CONSTRAINT fk_installment_org_credit
  FOREIGN KEY (organization_id, credit_id) REFERENCES managerws.credit (organization_id, id);
ALTER TABLE managerws.payment ADD CONSTRAINT fk_payment_org_credit
  FOREIGN KEY (organization_id, credit_id) REFERENCES managerws.credit (organization_id, id);
ALTER TABLE managerws.payment ADD CONSTRAINT fk_payment_org_collector
  FOREIGN KEY (organization_id, collector_user_id) REFERENCES managerws.membership (organization_id, user_id);
ALTER TABLE managerws.payment_allocation ADD CONSTRAINT fk_allocation_org_payment
  FOREIGN KEY (organization_id, payment_id) REFERENCES managerws.payment (organization_id, id);
ALTER TABLE managerws.payment_allocation ADD CONSTRAINT fk_allocation_org_installment
  FOREIGN KEY (organization_id, installment_id) REFERENCES managerws.loan_installment (organization_id, id);
ALTER TABLE managerws.ledger_entry ADD CONSTRAINT fk_ledger_org_credit
  FOREIGN KEY (organization_id, credit_id) REFERENCES managerws.credit (organization_id, id);
ALTER TABLE managerws.ledger_entry ADD CONSTRAINT fk_ledger_org_payment
  FOREIGN KEY (organization_id, payment_id) REFERENCES managerws.payment (organization_id, id);
ALTER TABLE managerws.cash_session ADD CONSTRAINT fk_cash_session_org_collector
  FOREIGN KEY (organization_id, collector_user_id) REFERENCES managerws.membership (organization_id, user_id);
ALTER TABLE managerws.cash_movement ADD CONSTRAINT fk_cash_movement_org_session
  FOREIGN KEY (organization_id, cash_session_id) REFERENCES managerws.cash_session (organization_id, id);
ALTER TABLE managerws.cash_movement ADD CONSTRAINT fk_cash_movement_org_user
  FOREIGN KEY (organization_id, user_id) REFERENCES managerws.membership (organization_id, user_id);
ALTER TABLE managerws.cash_movement ADD CONSTRAINT fk_cash_movement_org_payment
  FOREIGN KEY (organization_id, payment_id) REFERENCES managerws.payment (organization_id, id);
ALTER TABLE managerws.audit_log ADD CONSTRAINT fk_audit_org_actor
  FOREIGN KEY (organization_id, actor_user_id) REFERENCES managerws.membership (organization_id, user_id);

CREATE INDEX IF NOT EXISTS ix_subscription_org_created ON managerws.subscription (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_payment_request_org_status_created ON managerws.payment_request (organization_id, status, created_at DESC);
