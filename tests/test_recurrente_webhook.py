"""Functional tests for Recurrente's signed subscription webhooks."""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta

import app as managerws


class TestRecurrenteWebhook:
    def setup_method(self):
        managerws.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            WTF_CSRF_CHECK_DEFAULT=False,
        )
        with managerws.app.app_context():
            managerws.db.session.remove()
            managerws.db.drop_all()
            managerws.db.create_all()
        self.client = managerws.app.test_client()

    def create_owner(self):
        with managerws.app.app_context():
            user = managerws.User(
                username="recurrente_owner",
                email="billing-owner@example.com",
                password="test-hash",
                role="owner",
                is_active=True,
                email_verified=True,
            )
            organization = managerws.Organization(
                name="Recurrente Test Company",
                slug="recurrente-test-company",
                business_type="lender",
                currency="GTQ",
            )
            managerws.db.session.add_all([user, organization])
            managerws.db.session.flush()
            managerws.db.session.add(managerws.Membership(
                organization_id=organization.id,
                user_id=user.id,
                role="owner",
                is_active=True,
            ))
            managerws.db.session.commit()

    def send_event(self, monkeypatch, payload, message_id, *, invalid_signature=False):
        secret_bytes = b"managerws-recurrente-test-signing-secret"
        secret = "whsec_" + base64.b64encode(secret_bytes).decode("ascii")
        monkeypatch.setenv("RECURRENTE_WEBHOOK_SECRET", secret)

        timestamp = str(int(datetime.utcnow().timestamp()))
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        signed_content = message_id.encode() + b"." + timestamp.encode() + b"." + body
        signature = base64.b64encode(
            hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
        ).decode("ascii")
        if invalid_signature:
            signature = ("A" if signature[0] != "A" else "B") + signature[1:]

        return self.client.post(
            "/webhooks/recurrente",
            data=body,
            content_type="application/json",
            headers={
                "svix-id": message_id,
                "svix-timestamp": timestamp,
                "svix-signature": "v1," + signature,
            },
        )

    def successful_payment(self):
        return {
            "event_type": "intent.succeeded",
            "type": "payment",
            "id": "in_managerws_payment_001",
            "status": "succeeded",
            "raw_status": "succeeded",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "amount_in_cents": 9900,
            "currency": "GTQ",
            "customer_id": "cus_managerws_001",
            "customer": {"id": "cus_managerws_001", "email": "billing-owner@example.com"},
            "product": {"id": "prod_managerws_pro"},
            "subscription": {"id": "sub_managerws_001"},
            "payment": {"id": "pay_managerws_001"},
        }

    def test_valid_payment_activates_exact_plan_and_duplicate_delivery_is_idempotent(self, monkeypatch):
        self.create_owner()
        payload = self.successful_payment()

        first = self.send_event(monkeypatch, payload, "msg_recurrente_001")
        assert first.status_code == 200
        assert first.get_json()["status"] == "processed"

        with managerws.app.app_context():
            subscription = managerws.Subscription.query.filter_by(
                provider="recurrente",
                provider_subscription_id="sub_managerws_001",
            ).one()
            assert subscription.plan == "pro"
            assert subscription.amount == managerws.Decimal("99.00")
            assert subscription.currency == "GTQ"
            assert subscription.status == "active"
            assert subscription.current_period_end > managerws.utcnow()
            assert managerws.BillingTransaction.query.filter_by(
                provider="recurrente",
                provider_transaction_id="pay_managerws_001",
            ).count() == 1

        duplicate = self.send_event(monkeypatch, payload, "msg_recurrente_001")
        assert duplicate.status_code == 200
        assert duplicate.get_json()["status"] == "already_processed"

        with managerws.app.app_context():
            assert managerws.Subscription.query.count() == 1
            assert managerws.BillingTransaction.query.count() == 1

    def test_invalid_signature_cannot_create_or_activate_a_subscription(self, monkeypatch):
        self.create_owner()
        response = self.send_event(
            monkeypatch,
            self.successful_payment(),
            "msg_recurrente_invalid",
            invalid_signature=True,
        )
        assert response.status_code == 400

        with managerws.app.app_context():
            assert managerws.Subscription.query.count() == 0
            assert managerws.BillingWebhookEvent.query.count() == 0

    def test_wrong_amount_is_ignored_without_granting_access(self, monkeypatch):
        self.create_owner()
        payload = self.successful_payment()
        payload["amount_in_cents"] = 12345

        response = self.send_event(monkeypatch, payload, "msg_recurrente_wrong_amount")
        assert response.status_code == 200
        assert response.get_json()["status"] == "ignored"

        with managerws.app.app_context():
            assert managerws.Subscription.query.count() == 0
            row = managerws.BillingWebhookEvent.query.one()
            assert row.status == "ignored"

    def test_past_due_and_cancel_events_update_existing_subscription(self, monkeypatch):
        self.create_owner()
        payment_response = self.send_event(
            monkeypatch, self.successful_payment(), "msg_recurrente_paid_before_dunning"
        )
        assert payment_response.status_code == 200

        past_due = {
            "event_type": "subscription.past_due",
            "id": "sub_managerws_001",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "customer_email": "billing-owner@example.com",
            "payment_retries": 1,
        }
        response = self.send_event(monkeypatch, past_due, "msg_recurrente_past_due")
        assert response.status_code == 200

        with managerws.app.app_context():
            subscription = managerws.Subscription.query.filter_by(
                provider_subscription_id="sub_managerws_001"
            ).one()
            assert subscription.status == "past_due"
            assert subscription.grace_period_end > managerws.utcnow()

        canceled = {
            "event_type": "subscription.cancel",
            "id": "sub_managerws_001",
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        response = self.send_event(monkeypatch, canceled, "msg_recurrente_cancel")
        assert response.status_code == 200

        with managerws.app.app_context():
            subscription = managerws.Subscription.query.filter_by(
                provider_subscription_id="sub_managerws_001"
            ).one()
            assert subscription.status == "canceled"
            assert subscription.is_current is False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
