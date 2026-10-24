import re
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / 'app.py'
SOURCE = APP.read_text(encoding='utf-8')


def function_body(name):
    match = re.search(rf'^def {name}\([^\n]*\):\n(?P<body>.*?)(?=^@app\.route|^def |\Z)', SOURCE, re.M | re.S)
    return match.group('body') if match else ''


class Phase6AppStaticTests(unittest.TestCase):
    def test_subscription_model_declares_provider_plan_id_used_by_paypal_checkout(self):
        start = SOURCE.index('class Subscription(db.Model):')
        end = SOURCE.index('class BillingTransaction(db.Model):', start)
        model = SOURCE[start:end]
        self.assertRegex(model, r'provider_plan_id\s*=\s*db\.Column\(db\.String\(180\)\)')
        self.assertIn('provider_plan_id=pp_plan_id', SOURCE)

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

    def test_paypal_checkout_is_disabled_until_explicitly_enabled(self):
        self.assertIn("os.environ.get('PAYPAL_ENABLED', 'false')", SOURCE)
        self.assertIn("if not paypal_checkout_configured(plan, currency)", SOURCE)

    def test_paypal_currency_and_amount_are_separate_from_localized_display_prices(self):
        body = function_body('paypal_subscribe')
        self.assertIn('currency = paypal_currency()', body)
        self.assertIn('amount = paypal_price(plan, currency)', body)
        self.assertNotIn('amount = Decimal(str(plan_data[plan]))', body)
        self.assertIn('paypal_amounts=paypal_amounts', SOURCE)
        self.assertIn('Precio de la suscripción PayPal', Path(__file__).resolve().parents[1].joinpath('templates/pricing.html').read_text(encoding='utf-8'))

    def test_paypal_plan_is_checked_server_side_before_creating_subscription(self):
        body = function_body('paypal_subscribe')
        self.assertIn('paypal_validate_plan(pp_plan_id, currency, amount)', body)
        validation = function_body('paypal_validate_plan')
        self.assertIn("details.get('status') != 'ACTIVE'", validation)
        self.assertIn("frequency.get('interval_unit') != 'MONTH'", validation)
        self.assertIn("fixed.get('currency_code')", validation)

    def test_paypal_webhook_handles_ignored_duplicate_events(self):
        body = function_body('paypal_webhook')
        self.assertIn("existing.status in {'processed', 'ignored'}", body)
        self.assertIn('except IntegrityError:', body)

    def test_live_paypal_handoff_never_requests_client_secret_in_chat(self):
        handoff = Path(__file__).resolve().parents[1].joinpath('PAYPAL_LIVE_HANDOFF.md').read_text(encoding='utf-8')
        self.assertIn('PAYPAL_ENABLED=false', handoff)
        self.assertIn('No compartas aquí el PayPal Client Secret', handoff)
        self.assertIn('/webhooks/paypal', handoff)

    def test_past_due_subscription_respects_grace_period(self):
        start = SOURCE.index('class Subscription(db.Model):')
        end = SOURCE.index('class BillingTransaction(db.Model):', start)
        model = SOURCE[start:end]
        self.assertIn("if self.status == 'past_due':", model)
        self.assertIn('self.grace_period_end is not None and self.grace_period_end > now', model)

    def test_successful_paypal_renewal_refreshes_period_from_provider(self):
        process = function_body('process_paypal_event')
        self.assertIn('paypal_get_subscription_details(sub.provider_subscription_id)', process)
        self.assertIn("refreshed_billing.get('next_billing_time')", process)
        self.assertIn("sub.status = 'unpaid'", process)


if __name__ == '__main__':
    unittest.main()
