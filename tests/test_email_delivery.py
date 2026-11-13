"""Outbound email delivery fallback and configuration tests."""
from types import SimpleNamespace

import app as managerws


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host
        self.port = port
        self.messages = []
        self.logged_in = False
        self.started_tls = False
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def ehlo(self):
        return None

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, username, password):
        self.logged_in = True

    def send_message(self, message):
        self.messages.append(message)


class TestEmailDelivery:
    def setup_method(self):
        FakeSMTP.instances.clear()

    def clear_email_env(self, monkeypatch):
        for key in (
            "RESEND_API_KEY", "EMAIL_FROM", "SMTP_HOST", "SMTP_FROM",
            "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
        ):
            monkeypatch.delenv(key, raising=False)

    def test_no_provider_is_reported_as_unconfigured(self, monkeypatch):
        self.clear_email_env(monkeypatch)
        assert managerws.email_provider_configured() is False
        assert managerws.send_email("customer@example.com", "Test", "Body") is False

    def test_resend_rejection_falls_back_to_smtp(self, monkeypatch):
        self.clear_email_env(monkeypatch)
        monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
        monkeypatch.setenv("EMAIL_FROM", "ManagerWS <onboarding@resend.dev>")
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM", "ManagerWS <no-reply@example.com>")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("SMTP_USERNAME", "smtp-user")
        monkeypatch.setenv("SMTP_PASSWORD", "smtp-password")

        monkeypatch.setattr(
            managerws.requests,
            "post",
            lambda *args, **kwargs: SimpleNamespace(status_code=403),
        )
        monkeypatch.setattr(managerws.smtplib, "SMTP", FakeSMTP)

        assert managerws.email_provider_configured() is True
        assert managerws.send_email("customer@example.com", "Verify", "Verification URL") is True
        assert len(FakeSMTP.instances) == 1
        smtp = FakeSMTP.instances[0]
        assert smtp.started_tls is True
        assert smtp.logged_in is True
        assert len(smtp.messages) == 1
        assert smtp.messages[0]["To"] == "customer@example.com"

    def test_resend_success_does_not_use_smtp_fallback(self, monkeypatch):
        self.clear_email_env(monkeypatch)
        monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
        monkeypatch.setenv("EMAIL_FROM", "ManagerWS <verified@managerws.example>")
        monkeypatch.setattr(
            managerws.requests,
            "post",
            lambda *args, **kwargs: SimpleNamespace(status_code=200),
        )
        monkeypatch.setattr(managerws.smtplib, "SMTP", FakeSMTP)

        assert managerws.send_email("customer@example.com", "Verify", "Verification URL") is True
        assert FakeSMTP.instances == []

    def test_smtp_ssl_465_is_supported(self, monkeypatch):
        self.clear_email_env(monkeypatch)
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_FROM", "ManagerWS <no-reply@example.com>")
        monkeypatch.setenv("SMTP_PORT", "465")
        monkeypatch.setattr(managerws.smtplib, "SMTP_SSL", FakeSMTP)

        assert managerws.send_email("customer@example.com", "Verify", "Verification URL") is True
        assert FakeSMTP.instances[0].port == 465


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
