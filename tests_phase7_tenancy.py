import re
from pathlib import Path

APP = Path('app.py').read_text()
MIG4 = Path('migrations_phase4b.sql').read_text().lower()
MIG5 = Path('migrations_phase5.sql').read_text().lower()

def test_selected_org_is_membership_scoped():
    assert "Membership.query.filter_by(user_id=user.id, organization_id=organization_id, is_active=True).first()" in APP
    assert "session['organization_id'] = organization_id" in APP

def test_current_membership_rejects_stale_org():
    assert "session.pop('organization_id', None)" in APP
    assert "if membership.organization_id == selected_id" in APP

def test_financial_queries_scope_organization():
    critical = ['Payment.query.filter_by(id=id,organization_id=membership.organization_id)',
                'Credit.query.filter_by(id=id,organization_id=membership.organization_id)',
                'LoanApplication.query.filter_by(id=application_id,organization_id=oid)']
    for needle in critical:
        assert needle in APP

def test_payment_idempotency_unique_per_org():
    assert 'uq_payment_org_idempotency' in MIG4
    assert 'unique (organization_id, idempotency_key)' in MIG4

def test_application_disbursement_unique_per_org():
    assert 'uq_credit_org_application' in MIG4

def test_financial_indexes_exist():
    for needle in ['ix_credit_org_client_status','ix_payment_org_credit_date','ix_allocation_org_installment']:
        assert needle in MIG5

def test_financial_get_routes_do_not_commit():
    for fn in ['def loan_detail(', 'def loan_payoff(', 'def loan_reports(', 'def collections(']:
        start = APP.index(fn)
        nxt = APP.find('\n@app.route', start + 1)
        block = APP[start:nxt if nxt != -1 else None]
        assert 'db.session.commit()' not in block, fn

def test_financial_mutations_are_post():
    pairs = [('/payment/new', "methods=['POST']"),('/payment/<int:id>/reverse', "methods=['POST']"),('/prestamos/loans/<int:id>/refinanciar', "methods=['POST']"),('/organizacion/seleccionar', "methods=['POST']")]
    for route, methods in pairs:
        pos=APP.index(route)
        chunk=APP[pos:APP.find('def ',pos)]
        assert methods in chunk, route
