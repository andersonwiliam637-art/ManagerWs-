"""Functional authentication and company-registration tests using isolated SQLite."""
import os
from unittest.mock import patch
from urllib.parse import urlparse

# Import the Flask app against an isolated in-memory database, never production.
os.environ.pop("FLASK_ENV", None)
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["AUTO_CREATE_DB"] = "true"
os.environ["SECRET_KEY"] = "managerws-auth-test-secret"
os.environ["REDIS_URL"] = "memory://"

import app as managerws  # noqa: E402


class AuthRegistrationTests:
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

    def register(self, username="empresa_demo", email="demo@example.com"):
        return self.client.post(
            "/register",
            data={
                "username": username,
                "email": email,
                "password": "CorrectHorseBattery!27",
                "organization_name": "Empresa Demo",
                "business_type": "distributor",
                "currency": "GTQ",
            },
        )

    def test_register_creates_user_company_and_owner_membership_then_login_after_verification(self):
        with patch.object(managerws, "send_email", return_value=True) as send_email:
            response = self.register()

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")
        assert send_email.call_count == 1

        with managerws.app.app_context():
            user = managerws.User.query.filter_by(email="demo@example.com").one()
            organization = managerws.Organization.query.filter_by(name="Empresa Demo").one()
            membership = managerws.Membership.query.filter_by(
                user_id=user.id, organization_id=organization.id
            ).one()
            token_row = managerws.EmailVerificationToken.query.filter_by(user_id=user.id).one()
            assert user.email_verified is False
            assert user.is_active is True
            assert membership.role == "owner"
            assert membership.is_active is True
            assert organization.currency == "GTQ"
            assert organization.business_type == "distributor"
            assert token_row.used_at is None
            assert token_row.expires_at > managerws.utcnow()

        # An unverified account must not establish a session.
        blocked = self.client.post(
            "/login",
            data={"username": "empresa_demo", "password": "CorrectHorseBattery!27"},
        )
        assert blocked.status_code == 200
        assert b"Verifica tu correo" in blocked.data

        # Consume the actual verification URL sent by the mocked mail provider.
        email_body = send_email.call_args.args[2]
        verification_url = email_body.splitlines()[2]
        verification_path = urlparse(verification_url).path
        verified = self.client.get(verification_path)
        assert verified.status_code == 302

        with managerws.app.app_context():
            user = managerws.User.query.filter_by(email="demo@example.com").one()
            assert user.email_verified is True

        logged_in = self.client.post(
            "/login",
            data={"username": "empresa_demo", "password": "CorrectHorseBattery!27"},
        )
        assert logged_in.status_code == 302
        dashboard = self.client.get("/dashboard")
        assert dashboard.status_code == 200

    def test_duplicate_registration_does_not_create_another_company(self):
        with patch.object(managerws, "send_email", return_value=True):
            self.register()
            duplicate = self.register(username="otra_empresa")

        assert duplicate.status_code == 302
        with managerws.app.app_context():
            assert managerws.User.query.count() == 1
            assert managerws.Organization.query.count() == 1
            assert managerws.Membership.query.count() == 1

    def test_expired_verification_can_be_resent_without_exposing_account_existence(self):
        with patch.object(managerws, "send_email", return_value=True):
            self.register()

        with managerws.app.app_context():
            user = managerws.User.query.filter_by(email="demo@example.com").one()
            old_token = managerws.EmailVerificationToken.query.filter_by(user_id=user.id).one()
            old_token.expires_at = managerws.utcnow() - managerws.timedelta(days=1)
            managerws.db.session.commit()

        with patch.object(managerws, "send_email", return_value=True) as send_email:
            resent = self.client.post(
                "/resend-verification",
                data={"email": "demo@example.com"},
                follow_redirects=True,
            )
            unknown = self.client.post(
                "/resend-verification",
                data={"email": "unknown@example.com"},
                follow_redirects=True,
            )

        assert resent.status_code == 200
        assert unknown.status_code == 200
        assert b"Si la cuenta existe y necesita verificacion" in resent.data or b"Si la cuenta existe y necesita verificaci" in resent.data
        assert send_email.call_count == 1

        with managerws.app.app_context():
            tokens = managerws.EmailVerificationToken.query.order_by(
                managerws.EmailVerificationToken.id.asc()
            ).all()
            assert len(tokens) == 2
            assert tokens[0].used_at is not None
            assert tokens[1].used_at is None
            assert tokens[1].expires_at > managerws.utcnow()
