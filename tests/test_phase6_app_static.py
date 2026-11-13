import re
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / 'app.py'
SOURCE = APP.read_text(encoding='utf-8')


def function_body(name):
    match = re.search(rf'^def {name}\([^\n]*\):\n(?P<body>.*?)(?=^@app\.route|^def |\Z)', SOURCE, re.M | re.S)
    return match.group('body') if match else ''


class Phase6AppStaticTests(unittest.TestCase):
    def test_subscription_model_retains_legacy_provider_plan_reference(self):
        start = SOURCE.index('class Subscription(db.Model):')
        end = SOURCE.index('class BillingTransaction(db.Model):', start)
        model = SOURCE[start:end]
        self.assertRegex(model, r'provider_plan_id\s*=\s*db\.Column\(db\.String\(180\)\)')

    def test_health_and_readiness_probes_exist(self):
        self.assertIn("@app.route('/health', methods=['GET'])", SOURCE)
        self.assertIn("@app.route('/ready', methods=['GET'])", SOURCE)
        self.assertIn("db.session.execute(text('SELECT 1'))", SOURCE)

    def test_read_only_lender_views_do_not_commit(self):
        for name in ('loan_detail', 'loan_payoff', 'loan_reports', 'collections'):
            with self.subTest(route=name):
                self.assertNotIn('db.session.commit()', function_body(name))

    def test_critical_mutations_are_post_only(self):
        routes = {
            '/payment/new': "methods=['POST']",
            '/payment/<int:id>/reverse': "methods=['POST']",
            '/prestamos/loans/<int:id>/refinanciar': "methods=['POST']",
            '/prestamos/solicitudes/<int:application_id>/decision': "methods=['POST']",
            '/prestamos/solicitudes/<int:application_id>/desembolsar': "methods=['POST']",
        }
        for path, methods in routes.items():
            with self.subTest(route=path):
                start = SOURCE.index(f"@app.route('{path}'")
                chunk = SOURCE[start:start + 220]
                self.assertIn(methods, chunk)

    def test_payment_idempotency_and_org_scope_present(self):
        self.assertIn('Payment.query.filter_by(idempotency_key=idem,organization_id=membership.organization_id)', SOURCE)
        self.assertIn('UniqueConstraint(\'organization_id\', \'idempotency_key\'', SOURCE)
        self.assertIn('except IntegrityError:', SOURCE)

    def test_payment_reversal_is_a_state_change_not_delete(self):
        body = function_body('reverse_payment')
        self.assertIn("payment.status='reversed'", body)
        self.assertIn("post_ledger(credit,'reversal'", body)
        self.assertNotIn('db.session.delete(payment)', body)

    def test_application_lifecycle_is_guarded(self):
        decision = function_body('loan_application_decision')
        disburse = function_body('loan_application_disburse')
        self.assertIn("if a.status!='pending':", decision)
        self.assertIn("if decision not in {'approved','rejected'}", decision)
        self.assertIn("if a.status!='approved':", disburse)
        self.assertIn('if Credit.query.filter_by(application_id=a.id,organization_id=oid).first():', disburse)

    def test_collections_uses_grace_period(self):
        body = function_body('collections')
        self.assertIn('credit.grace_days', body)
        self.assertIn("today.date() > grace_cutoff.date()", body)

    def test_financial_float_is_absent(self):
        self.assertNotRegex(SOURCE, r'db\.Float')

    def test_past_due_subscription_respects_grace_period(self):
        start = SOURCE.index('class Subscription(db.Model):')
        end = SOURCE.index('class BillingTransaction(db.Model):', start)
        model = SOURCE[start:end]
        self.assertIn("if self.status == 'past_due':", model)
        self.assertIn('self.grace_period_end is not None and self.grace_period_end > now', model)

    def test_recurrente_is_the_only_plan_checkout(self):
        self.assertIn("RECURRENTE_CHECKOUT_URLS", SOURCE)
        self.assertIn("https://app.recurrente.com/s/willy-salan/managerws-pro", SOURCE)
        self.assertIn("https://app.recurrente.com/s/willy-salan/managerws-bussines", SOURCE)
        self.assertIn("https://app.recurrente.com/s/willy-salan/managerws-enterprise", SOURCE)
        checkout = function_body('checkout')
        self.assertIn("exclusivamente por Recurrente", checkout)
        self.assertNotIn("PaymentRequest(", checkout)
        pricing = Path(__file__).resolve().parents[1].joinpath('templates/pricing.html').read_text(encoding='utf-8')
        self.assertIn("{{ recurrente_links[name] }}", pricing)
        self.assertIn("Beneficios incluidos", pricing)
        self.assertIn("mismo correo verificado", pricing)
        self.assertNotIn("PayPal", pricing)
        self.assertNotIn("url_for('checkout')", pricing)
        self.assertNotIn("@app.route('/webhooks/paypal'", SOURCE)
        self.assertNotIn("paypal_subscribe", SOURCE)

    def test_neon_pooler_does_not_receive_unsupported_search_path_startup_parameter(self):
        self.assertNotIn("'connect_args': {'options': '-csearch_path=managerws,public'}", SOURCE)
        self.assertNotIn("options': '-csearch_path", SOURCE)

    def test_registration_requires_a_complete_email_provider_before_persisting(self):
        body = function_body('register')
        self.assertIn('if not email_provider_configured():', body)
        self.assertIn('if not sent:', body)
        self.assertIn('db.session.rollback()', body)


if __name__ == '__main__':
    unittest.main()
