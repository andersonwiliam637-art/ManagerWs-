import os
import secrets
import logging
import smtplib
import ssl
from datetime import datetime, timedelta, date
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from functools import wraps
from urllib.parse import urljoin
import base64
import calendar
import hashlib
import hmac
import json
import requests

from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import selectinload
from sqlalchemy import Numeric, CheckConstraint, UniqueConstraint, Index, or_, func, text
from sqlalchemy.exc import IntegrityError
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from redis_config import normalize_redis_storage_uri

# ---------------------------------------------------------------------------
# App configuration
# ---------------------------------------------------------------------------
app = Flask(__name__)

IS_PRODUCTION = os.environ.get('FLASK_ENV') == 'production'
SECRET_KEY = os.environ.get('SECRET_KEY')
if IS_PRODUCTION and not SECRET_KEY:
    raise RuntimeError('SECRET_KEY debe estar definida en producción.')
app.secret_key = SECRET_KEY or secrets.token_hex(32)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=4),
    WTF_CSRF_TIME_LIMIT=3600,
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'pool_size': int(os.environ.get('DB_POOL_SIZE', '5')),
        'max_overflow': int(os.environ.get('DB_MAX_OVERFLOW', '10')),
    },
)

csrf = CSRFProtect(app)
storage_uri = normalize_redis_storage_uri(
    os.environ.get('REDIS_URL'),
    is_production=IS_PRODUCTION,
)
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=storage_uri,
    default_limits=['200 per hour'],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('managerws')

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)
if IS_PRODUCTION and not db_url:
    raise RuntimeError('DATABASE_URL debe estar definida en producción.')
if IS_PRODUCTION and not os.environ.get('REDIS_URL'):
    raise RuntimeError('REDIS_URL debe estar definida en producción para rate limiting compartido entre instancias.')

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///managerws_gt.db'
# Neon role managerws_owner already has search_path=managerws,public.
# Do not pass options=-csearch_path in the startup packet: PgBouncer rejects it.
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CURRENCIES = {
    'GTQ': 'Q', 'USD': '$', 'HNL': 'L', 'CRC': '₡',
    'NIO': 'C$', 'MXN': '$'
}

PLANS_PPP = {
    'GT': {'name': 'Guatemala', 'currency': 'GTQ', 'symbol': 'Q', 'pro': 99, 'business': 249, 'enterprise': 499},
    'SV': {'name': 'El Salvador', 'currency': 'USD', 'symbol': '$', 'pro': 12, 'business': 29, 'enterprise': 59},
    'HN': {'name': 'Honduras', 'currency': 'HNL', 'symbol': 'L', 'pro': 299, 'business': 699, 'enterprise': 1399},
    'NI': {'name': 'Nicaragua', 'currency': 'NIO', 'symbol': 'C$', 'pro': 450, 'business': 1099, 'enterprise': 2199},
    'CR': {'name': 'Costa Rica', 'currency': 'CRC', 'symbol': '₡', 'pro': 6990, 'business': 16990, 'enterprise': 33990},
    'PA': {'name': 'Panamá', 'currency': 'USD', 'symbol': '$', 'pro': 14, 'business': 34, 'enterprise': 69},
    'MX': {'name': 'México', 'currency': 'MXN', 'symbol': '$', 'pro': 249, 'business': 599, 'enterprise': 1199},
    'US': {'name': 'Estados Unidos', 'currency': 'USD', 'symbol': '$', 'pro': 19, 'business': 49, 'enterprise': 99},
}

PAYMENT_CONFIG = {
    'wise': os.environ.get('WISE_ACCOUNT', ''),
    'bank_gt': os.environ.get('BANK_GT', ''),
    'whatsapp': os.environ.get('WHATSAPP', ''),
}

# Hosted recurring checkout links supplied for the ManagerWS plans.
RECURRENTE_CHECKOUT_URLS = {
    'pro': 'https://app.recurrente.com/s/willy-salan/managerws-pro',
    'business': 'https://app.recurrente.com/s/willy-salan/managerws-bussines',
    'enterprise': 'https://app.recurrente.com/s/willy-salan/managerws-enterprise',
}

PLAN_NAMES = {'Gratis', 'pro', 'business', 'enterprise'}
DUMMY_PASSWORD_HASH = generate_password_hash('managerws-dummy-password', method='pbkdf2:sha256:600000')
SUBSCRIPTION_STATUSES = {'approval_pending', 'trialing', 'active', 'past_due', 'paused', 'canceled', 'unpaid', 'expired'}
ROLES = {'owner', 'admin', 'manager', 'seller', 'collector', 'accountant', 'viewer'}
ORG_TYPES = {'lender', 'distributor', 'hybrid'}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def utcnow():
    return datetime.utcnow()


def money(amount, cur='GTQ'):
    try:
        value = Decimal(str(amount)).quantize(Decimal('0.01'))
        return f"{CURRENCIES.get(cur, 'Q')} {value:,.2f}"
    except (TypeError, ValueError, InvalidOperation):
        return f"{CURRENCIES.get(cur, 'Q')} 0.00"


def safe_decimal(value, default=Decimal('0.00')):
    try:
        raw = str(value).strip()
        if not raw:
            return default
        v = Decimal(raw)
        if not v.is_finite() or v < 0:
            return default
        return v.quantize(Decimal('0.01'))
    except (TypeError, ValueError, InvalidOperation):
        return default


def detect_country():
    # Country detection is UX-only. It MUST NOT be trusted for authorization or billing.
    return os.environ.get('DEFAULT_COUNTRY', 'GT') if os.environ.get('DEFAULT_COUNTRY', 'GT') in PLANS_PPP else 'GT'


def token_serializer(salt):
    return URLSafeTimedSerializer(app.secret_key, salt=salt)


def make_token(value, salt):
    return token_serializer(salt).dumps(value)


def read_token(token, salt, max_age):
    try:
        return token_serializer(salt).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def audit(action, *, actor_user_id=None, organization_id=None, target_type=None,
          target_id=None, metadata=None):
    entry = AuditLog(
        actor_user_id=actor_user_id,
        organization_id=organization_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        ip_address=(request.remote_addr or '')[:45] if request else None,
        user_agent=(request.headers.get('User-Agent') or '')[:500] if request else None,
        metadata_json=metadata or {},
    )
    db.session.add(entry)


def email_provider_configured():
    """Return whether at least one complete outbound email provider is configured."""
    resend_ready = bool(
        (os.environ.get('RESEND_API_KEY') or '').strip()
        and (os.environ.get('EMAIL_FROM') or '').strip()
    )
    smtp_ready = bool(
        (os.environ.get('SMTP_HOST') or '').strip()
        and ((os.environ.get('SMTP_FROM') or os.environ.get('EMAIL_FROM') or '').strip())
    )
    return resend_ready or smtp_ready


def send_email(to, subject, body):
    """Send through Resend first, then fall back to SMTP when available."""
    resend_api_key = (os.environ.get('RESEND_API_KEY') or '').strip()
    email_from = (os.environ.get('EMAIL_FROM') or '').strip()

    if resend_api_key and email_from:
        try:
            response = requests.post(
                'https://api.resend.com/emails',
                headers={
                    'Authorization': f'Bearer {resend_api_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'from': email_from,
                    'to': [to],
                    'subject': subject,
                    'text': body,
                },
                timeout=15,
            )
            if response.status_code in (200, 201):
                return True
            logger.error('Resend rechazó el correo (HTTP %s); se intentará el proveedor SMTP alternativo.', response.status_code)
        except requests.RequestException as exc:
            logger.error('Resend no respondió (%s); se intentará el proveedor SMTP alternativo.', type(exc).__name__)
    elif resend_api_key and not email_from:
        logger.error('RESEND_API_KEY existe, pero EMAIL_FROM está vacío; se intentará SMTP si está configurado.')

    host = (os.environ.get('SMTP_HOST') or '').strip()
    sender = (os.environ.get('SMTP_FROM') or email_from).strip()
    if not host or not sender:
        logger.error('No hay un proveedor de correo completo: configura RESEND_API_KEY + EMAIL_FROM o SMTP_HOST + SMTP_FROM.')
        return False

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = to
    msg.set_content(body)
    try:
        port = int(os.environ.get('SMTP_PORT', '587'))
        username = (os.environ.get('SMTP_USERNAME') or '').strip()
        password = os.environ.get('SMTP_PASSWORD') or ''
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context()) as smtp:
                if username:
                    smtp.login(username, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                if username:
                    smtp.login(username, password)
                smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        logger.error('No se pudo enviar el correo mediante SMTP (%s).', type(exc).__name__)
        return False


# ---------------------------------------------------------------------------
# Models — foundation / multi-tenant
# ---------------------------------------------------------------------------
class Organization(db.Model):
    __tablename__ = 'organization'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    business_type = db.Column(db.String(20), nullable=False, default='lender')
    currency = db.Column(db.String(10), nullable=False, default='GTQ')
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    memberships = db.relationship('Membership', back_populates='organization', cascade='all, delete-orphan')


class User(db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='owner', nullable=False, index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    # Legacy flag retained only for backwards compatibility. Subscription model is authoritative.
    is_active_subscription = db.Column(db.Boolean, default=False, nullable=False, index=True)
    currency = db.Column(db.String(10), default='GTQ', nullable=False)
    plan = db.Column(db.String(50), default='Gratis', nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login = db.Column(db.DateTime)

    memberships = db.relationship('Membership', back_populates='user', cascade='all, delete-orphan')
    subscriptions = db.relationship('Subscription', back_populates='user', cascade='all, delete-orphan')
    clients = db.relationship('Client', back_populates='owner', lazy='selectin')
    credits = db.relationship('Credit', back_populates='owner', lazy='selectin')


class Membership(db.Model):
    __tablename__ = 'membership'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False, default='viewer')
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    __table_args__ = (db.UniqueConstraint('organization_id', 'user_id', name='uq_membership_org_user'),)

    organization = db.relationship('Organization', back_populates='memberships')
    user = db.relationship('User', back_populates='memberships')


class Subscription(db.Model):
    __tablename__ = 'subscription'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    provider = db.Column(db.String(30), nullable=False, default='manual')
    provider_customer_id = db.Column(db.String(180), index=True)
    # Legacy provider-plan reference retained for compatibility with existing subscription rows.
    provider_plan_id = db.Column(db.String(180))
    provider_subscription_id = db.Column(db.String(180), unique=True, index=True)
    plan = db.Column(db.String(50), nullable=False, default='Gratis')
    currency = db.Column(db.String(10), nullable=False, default='GTQ')
    amount = db.Column(Numeric(14, 2), nullable=False, default=Decimal('0.00'))
    interval = db.Column(db.String(20), nullable=False, default='month')
    status = db.Column(db.String(20), nullable=False, default='trialing', index=True)
    current_period_start = db.Column(db.DateTime)
    current_period_end = db.Column(db.DateTime)
    cancel_at_period_end = db.Column(db.Boolean, nullable=False, default=False)
    canceled_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    trial_end = db.Column(db.DateTime)
    grace_period_end = db.Column(db.DateTime)
    last_payment_at = db.Column(db.DateTime)
    last_payment_provider_id = db.Column(db.String(180))
    failed_payment_count = db.Column(db.Integer, nullable=False, default=0)
    metadata_json = db.Column('metadata', db.JSON, nullable=False, default=dict)

    user = db.relationship('User', back_populates='subscriptions')
    organization = db.relationship('Organization')

    @property
    def is_current(self):
        now = utcnow()
        if self.status in {'trialing', 'active'}:
            return self.current_period_end is None or self.current_period_end > now
        # Failed payments keep access only through the explicit grace-period deadline.
        if self.status == 'past_due':
            return self.grace_period_end is not None and self.grace_period_end > now
        return False


class BillingTransaction(db.Model):
    __tablename__ = 'billing_transaction'
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey('subscription.id'), index=True)
    provider = db.Column(db.String(30), nullable=False)
    provider_transaction_id = db.Column(db.String(180), index=True)
    provider_order_id = db.Column(db.String(180), index=True)
    provider_event_id = db.Column(db.String(180), index=True)
    transaction_type = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(30), nullable=False, default='pending', index=True)
    amount = db.Column(Numeric(14,2), nullable=False)
    currency = db.Column(db.String(10), nullable=False)
    occurred_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    failure_code = db.Column(db.String(120))
    failure_message = db.Column(db.String(500))
    metadata_json = db.Column('metadata', db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class BillingWebhookEvent(db.Model):
    __tablename__ = 'billing_webhook_event'
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True)
    provider = db.Column(db.String(30), nullable=False)
    provider_event_id = db.Column(db.String(180), nullable=False)
    event_type = db.Column(db.String(120), nullable=False)
    resource_id = db.Column(db.String(180))
    status = db.Column(db.String(20), nullable=False, default='received', index=True)
    received_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    processed_at = db.Column(db.DateTime)
    error_message = db.Column(db.String(1000))
    payload = db.Column(db.JSON, nullable=False, default=dict)
    __table_args__ = (db.UniqueConstraint('provider','provider_event_id',name='uq_billing_webhook_provider_event'),)


class SubscriptionEvent(db.Model):
    __tablename__ = 'subscription_event'
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey('subscription.id'), index=True)
    provider = db.Column(db.String(30), nullable=False)
    event_type = db.Column(db.String(120), nullable=False)
    provider_event_id = db.Column(db.String(180))
    previous_status = db.Column(db.String(30))
    new_status = db.Column(db.String(30))
    occurred_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    metadata_json = db.Column('metadata', db.JSON, nullable=False, default=dict)
    __table_args__ = (db.UniqueConstraint('provider','provider_event_id',name='uq_subscription_event_provider_event'),)


class AuditLog(db.Model):
    __tablename__ = 'audit_log'
    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), index=True)
    action = db.Column(db.String(80), nullable=False, index=True)
    target_type = db.Column(db.String(50))
    target_id = db.Column(db.String(80))
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(500))
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


class PasswordResetToken(db.Model):
    __tablename__ = 'password_reset_token'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    token_hash = db.Column(db.String(128), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class EmailVerificationToken(db.Model):
    __tablename__ = 'email_verification_token'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    token_hash = db.Column(db.String(128), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


# ---------------------------------------------------------------------------
# Business models — phase 1 hardened foundation
# ---------------------------------------------------------------------------
class Client(db.Model):
    __tablename__ = 'client'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30))
    notes = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    owner = db.relationship('User', back_populates='clients')
    credits = db.relationship('Credit', back_populates='client', lazy='selectin')


class CollectionActivity(db.Model):
    __tablename__ = 'collection_activity'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False, index=True)
    credit_id = db.Column(db.Integer, db.ForeignKey('credit.id'), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    action_type = db.Column(db.String(30), nullable=False)
    outcome = db.Column(db.String(30))
    note = db.Column(db.String(2000))
    promised_amount = db.Column(Numeric(14, 2), nullable=False, default=Decimal('0.00'))
    promised_at = db.Column(db.DateTime)
    next_action_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    client = db.relationship('Client')
    credit = db.relationship('Credit')
    user = db.relationship('User')
    __table_args__ = (
        CheckConstraint("action_type IN ('call','visit','whatsapp','sms','email','promise','payment','other')", name='ck_collection_activity_type'),
        CheckConstraint('promised_amount >= 0', name='ck_collection_activity_amount'),
    )


class CollectionAssignment(db.Model):
    __tablename__ = 'collection_assignment'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    credit_id = db.Column(db.Integer, db.ForeignKey('credit.id'), nullable=False, index=True)
    collector_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    assigned_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    assigned_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    unassigned_at = db.Column(db.DateTime)
    note = db.Column(db.String(500))
    credit = db.relationship('Credit')
    collector = db.relationship('User', foreign_keys=[collector_user_id])
    assigner = db.relationship('User', foreign_keys=[assigned_by])


class CollectionPolicy(db.Model):
    __tablename__ = 'collection_policy'
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), primary_key=True)
    overdue_1_days = db.Column(db.Integer, nullable=False, default=1)
    overdue_2_days = db.Column(db.Integer, nullable=False, default=8)
    overdue_3_days = db.Column(db.Integer, nullable=False, default=31)
    overdue_4_days = db.Column(db.Integer, nullable=False, default=61)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class LoanApplication(db.Model):
    __tablename__ = 'loan_application'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False, index=True)
    requested_amount = db.Column(Numeric(14, 2), nullable=False)
    requested_term = db.Column(db.Integer, nullable=False)
    frequency = db.Column(db.String(20), nullable=False, default='monthly')
    interest_rate = db.Column(Numeric(8, 4), nullable=False, default=Decimal('0.0000'))
    interest_method = db.Column(db.String(20), nullable=False, default='simple')
    purpose = db.Column(db.String(255))
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    decision_note = db.Column(db.String(500))
    reviewed_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    reviewed_at = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    client = db.relationship('Client')
    __table_args__ = (
        CheckConstraint('requested_amount > 0', name='ck_application_amount_positive'),
        CheckConstraint('requested_term > 0', name='ck_application_term_positive'),
        CheckConstraint("frequency IN ('weekly','biweekly','monthly')", name='ck_application_frequency'),
        CheckConstraint("interest_method IN ('simple','amortized')", name='ck_application_interest_method'),
        CheckConstraint('interest_rate >= 0 AND interest_rate <= 100', name='ck_application_rate_range'),
    )

class Credit(db.Model):
    __tablename__ = 'credit'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False, index=True)

    # Financial contract. total is the contractual receivable (principal + scheduled interest).
    principal = db.Column(Numeric(14, 2), nullable=False, default=Decimal('0.00'))
    total = db.Column(Numeric(14, 2), nullable=False)
    paid = db.Column(Numeric(14, 2), default=Decimal('0.00'), nullable=False)
    paid_late_fees = db.Column(Numeric(14, 2), default=Decimal('0.00'), nullable=False)
    interest_rate = db.Column(Numeric(8, 4), nullable=False, default=Decimal('0.0000'))
    interest_method = db.Column(db.String(20), nullable=False, default='simple')  # simple | amortized
    term_count = db.Column(db.Integer, nullable=False, default=1)
    frequency = db.Column(db.String(20), nullable=False, default='monthly')  # weekly | biweekly | monthly
    disbursed_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    first_due_date = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='active', nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    due_date = db.Column(db.DateTime)
    version = db.Column(db.Integer, nullable=False, default=1)
    grace_days = db.Column(db.Integer, nullable=False, default=0)
    late_fee_type = db.Column(db.String(30), nullable=False, default='none')  # none|fixed|percent_daily|percent_installment
    late_fee_value = db.Column(Numeric(14, 4), nullable=False, default=Decimal('0.0000'))
    late_fee_cap = db.Column(Numeric(14, 2), nullable=False, default=Decimal('0.00'))
    payment_order = db.Column(db.String(40), nullable=False, default='late_fee,interest,principal')
    parent_credit_id = db.Column(db.Integer, db.ForeignKey('credit.id'), index=True)
    application_id = db.Column(db.Integer, db.ForeignKey('loan_application.id'), index=True)

    owner = db.relationship('User', back_populates='credits')
    client = db.relationship('Client', back_populates='credits')
    payments = db.relationship('Payment', back_populates='credit', lazy='selectin')
    installments = db.relationship('LoanInstallment', back_populates='credit', lazy='selectin', cascade='all, delete-orphan', order_by='LoanInstallment.number')
    ledger_entries = db.relationship('LedgerEntry', back_populates='credit', lazy='selectin', cascade='all, delete-orphan', order_by='LedgerEntry.created_at')

    __table_args__ = (
        CheckConstraint('principal >= 0', name='ck_credit_principal_nonnegative'),
        CheckConstraint('total >= 0', name='ck_credit_total_nonnegative'),
        CheckConstraint('paid >= 0', name='ck_credit_paid_nonnegative'),
        CheckConstraint('term_count > 0', name='ck_credit_term_positive'),
        UniqueConstraint('organization_id', 'application_id', name='uq_credit_org_application'),
        CheckConstraint("frequency IN ('weekly','biweekly','monthly')", name='ck_credit_frequency'),
        CheckConstraint("interest_method IN ('simple','amortized')", name='ck_credit_interest_method'),
        CheckConstraint('interest_rate >= 0 AND interest_rate <= 100', name='ck_credit_rate_range'),
        CheckConstraint('paid_late_fees >= 0', name='ck_credit_paid_late_fees_nonnegative'),
        CheckConstraint('grace_days >= 0', name='ck_credit_grace_days_nonnegative'),
        CheckConstraint('late_fee_value >= 0 AND late_fee_cap >= 0', name='ck_credit_late_fee_values_nonnegative'),
        CheckConstraint("late_fee_type IN ('none','fixed','percent_daily','percent_installment')", name='ck_credit_late_fee_type'),
        CheckConstraint("status IN ('active','overdue','paid','restructured','canceled')", name='ck_credit_status'),
    )

    @property
    def pending(self):
        return max(Decimal('0.00'), Decimal(str(self.total or 0)) - Decimal(str(self.paid or 0)))

    @property
    def fees_pending(self):
        return sum((max(Decimal('0.00'), Decimal(str(i.late_fee_due or 0)) - Decimal(str(i.late_fee_paid or 0))) for i in self.installments), Decimal('0.00'))

    @property
    def total_due(self):
        return quantize_money(self.pending + self.fees_pending)

    @property
    def overdue_amount(self):
        return sum((Decimal(str(i.balance)) + max(Decimal('0.00'), Decimal(str(i.late_fee_due or 0)) - Decimal(str(i.late_fee_paid or 0)) ) for i in self.installments if i.status == 'overdue'), Decimal('0.00'))

    @property
    def next_installment(self):
        return next((i for i in self.installments if i.status in {'pending', 'partial', 'overdue'}), None)


class LoanInstallment(db.Model):
    __tablename__ = 'loan_installment'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    credit_id = db.Column(db.Integer, db.ForeignKey('credit.id'), nullable=False, index=True)
    number = db.Column(db.Integer, nullable=False)
    due_date = db.Column(db.DateTime, nullable=False, index=True)
    principal_due = db.Column(Numeric(14, 2), nullable=False, default=Decimal('0.00'))
    interest_due = db.Column(Numeric(14, 2), nullable=False, default=Decimal('0.00'))
    late_fee_due = db.Column(Numeric(14, 2), nullable=False, default=Decimal('0.00'))
    late_fee_paid = db.Column(Numeric(14, 2), nullable=False, default=Decimal('0.00'))
    amount_due = db.Column(Numeric(14, 2), nullable=False, default=Decimal('0.00'))
    amount_paid = db.Column(Numeric(14, 2), nullable=False, default=Decimal('0.00'))
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)  # pending|partial|paid|overdue
    paid_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    credit = db.relationship('Credit', back_populates='installments')
    allocations = db.relationship('PaymentAllocation', back_populates='installment', lazy='selectin', cascade='all, delete-orphan')

    __table_args__ = (
        UniqueConstraint('credit_id', 'number', name='uq_installment_credit_number'),
        CheckConstraint('amount_due >= 0', name='ck_installment_amount_nonnegative'),
        CheckConstraint('amount_paid >= 0', name='ck_installment_paid_nonnegative'),
        CheckConstraint('principal_due >= 0 AND interest_due >= 0 AND late_fee_due >= 0 AND late_fee_paid >= 0', name='ck_installment_components_nonnegative'),
        CheckConstraint('amount_due = principal_due + interest_due', name='ck_installment_amount_reconciles'),
        CheckConstraint("status IN ('pending','partial','paid','overdue')", name='ck_installment_status'),
    )

    @property
    def balance(self):
        return max(Decimal('0.00'), Decimal(str(self.amount_due or 0)) - Decimal(str(self.amount_paid or 0)))


class PaymentAllocation(db.Model):
    __tablename__ = 'payment_allocation'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=False, index=True)
    installment_id = db.Column(db.Integer, db.ForeignKey('loan_installment.id'), nullable=False, index=True)
    amount = db.Column(Numeric(14, 2), nullable=False)
    late_fee_amount = db.Column(Numeric(14, 2), nullable=False, default=Decimal('0.00'))
    interest_amount = db.Column(Numeric(14, 2), nullable=False, default=Decimal('0.00'))
    principal_amount = db.Column(Numeric(14, 2), nullable=False, default=Decimal('0.00'))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    payment = db.relationship('Payment', back_populates='allocations')
    installment = db.relationship('LoanInstallment', back_populates='allocations')
    __table_args__ = (
        CheckConstraint('amount > 0', name='ck_allocation_positive'),
        CheckConstraint('late_fee_amount >= 0 AND interest_amount >= 0 AND principal_amount >= 0', name='ck_allocation_components_nonnegative'),
        CheckConstraint('amount = late_fee_amount + interest_amount + principal_amount', name='ck_allocation_amount_reconciles'),
    )


class LedgerEntry(db.Model):
    __tablename__ = 'ledger_entry'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    credit_id = db.Column(db.Integer, db.ForeignKey('credit.id'), nullable=False, index=True)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), index=True)
    entry_type = db.Column(db.String(40), nullable=False, index=True)  # disbursement|payment|reversal|late_fee|adjustment
    amount = db.Column(Numeric(14, 2), nullable=False)
    currency = db.Column(db.String(10), nullable=False, default='GTQ')
    reference = db.Column(db.String(120))
    metadata_json = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    credit = db.relationship('Credit', back_populates='ledger_entries')
    payment = db.relationship('Payment', back_populates='ledger_entries')
    __table_args__ = (CheckConstraint('amount <> 0', name='ck_ledger_nonzero'),)


class Payment(db.Model):
    __tablename__ = 'payment'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    credit_id = db.Column(db.Integer, db.ForeignKey('credit.id'), nullable=False, index=True)
    amount = db.Column(Numeric(14, 2), nullable=False)
    date = db.Column(db.DateTime, nullable=False, default=utcnow)
    note = db.Column(db.String(120))
    status = db.Column(db.String(20), nullable=False, default='posted')  # posted | reversed
    payment_method = db.Column(db.String(30), nullable=False, default='cash')
    collector_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
    idempotency_key = db.Column(db.String(80), index=True)
    receipt_number = db.Column(db.String(60), unique=True, index=True)
    reversed_at = db.Column(db.DateTime)
    reversal_reason = db.Column(db.String(255))

    credit = db.relationship('Credit', back_populates='payments')
    allocations = db.relationship('PaymentAllocation', back_populates='payment', lazy='selectin', cascade='all, delete-orphan')
    ledger_entries = db.relationship('LedgerEntry', back_populates='payment', lazy='selectin', cascade='all, delete-orphan')
    __table_args__ = (
        CheckConstraint('amount > 0', name='ck_payment_positive'),
        CheckConstraint("status IN ('posted','reversed')", name='ck_payment_status'),
        CheckConstraint("payment_method IN ('cash','bank','transfer','card','mobile','other')", name='ck_payment_method'),
        UniqueConstraint('organization_id', 'idempotency_key', name='uq_payment_org_idempotency'),
    )


class CashSession(db.Model):
    __tablename__ = 'cash_session'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    collector_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    opened_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    closed_at = db.Column(db.DateTime)
    opening_balance = db.Column(Numeric(14, 2), nullable=False, default=Decimal('0.00'))
    closing_balance = db.Column(Numeric(14, 2))
    counted_balance = db.Column(Numeric(14, 2))
    difference = db.Column(Numeric(14, 2))
    status = db.Column(db.String(20), nullable=False, default='open', index=True)
    notes = db.Column(db.String(255))


class CashMovement(db.Model):
    __tablename__ = 'cash_movement'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    cash_session_id = db.Column(db.Integer, db.ForeignKey('cash_session.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), index=True)
    movement_type = db.Column(db.String(30), nullable=False)  # opening|payment|expense|withdrawal|adjustment|closing
    amount = db.Column(Numeric(14, 2), nullable=False)
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


# Manual requests remain as a temporary migration mechanism, not the final subscription billing system.
class PaymentRequest(db.Model):
    __tablename__ = 'payment_request'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    user = db.relationship('User')
    plan = db.Column(db.String(50), nullable=False)
    method = db.Column(db.String(20), nullable=False)
    amount = db.Column(Numeric(14, 2), nullable=False)
    currency = db.Column(db.String(10), default='GTQ', nullable=False)
    reference = db.Column(db.String(120))
    status = db.Column(db.String(20), default='pending', nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

from financial import FREQUENCY_DAYS, build_schedule, add_period, quantize_money

# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        uid = session.get('user_id')
        if not uid:
            flash('Debe iniciar sesión para continuar.', 'warning')
            return redirect(url_for('login'))
        user = db.session.get(User, uid)
        if not user or not user.is_active:
            session.clear()
            flash('La sesión ya no es válida.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrap


def admin_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        user = current_user()
        membership = current_membership(user)
        if not user or not membership or membership.role not in {'owner', 'admin'}:
            flash('Acceso restringido.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrap


def subscription_active(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        user = db.session.get(User, session['user_id'])
        if not user or not user.is_active:
            session.clear()
            return redirect(url_for('login'))
        membership = current_membership(user)
        if not membership or not membership.organization.is_active:
            abort(403)
        # Subscription is an organization entitlement, not a per-user entitlement.
        # Any active member of the organization must resolve the same billing state.
        current = (Subscription.query
                   .filter_by(organization_id=membership.organization_id)
                   .order_by(Subscription.created_at.desc())
                   .first())
        if not current or not current.is_current:
            flash('Su suscripción no está activa. Active un plan para continuar.', 'warning')
            return redirect(url_for('pricing'))
        return f(*args, **kwargs)
    return wrap


def current_user():
    uid = session.get('user_id')
    return db.session.get(User, uid) if uid else None


ROLE_PERMISSIONS = {
    'owner': {'collections.read', 'collections.write', 'collections.assign', 'clients.read', 'clients.write', 'loans.read', 'loans.write', 'loans.review', 'payments.read', 'payments.write', 'payments.reverse', 'reports.read', 'team.manage'},
    'admin': {'collections.read', 'collections.write', 'collections.assign', 'clients.read', 'clients.write', 'loans.read', 'loans.write', 'loans.review', 'payments.read', 'payments.write', 'payments.reverse', 'reports.read', 'team.manage'},
    'manager': {'collections.read', 'collections.write', 'collections.assign', 'clients.read', 'clients.write', 'loans.read', 'loans.write', 'payments.read', 'payments.write', 'reports.read'},
    'seller': {'collections.read', 'collections.write', 'clients.read', 'clients.write', 'loans.read', 'loans.write', 'payments.read', 'payments.write'},
    'collector': {'collections.read', 'collections.write', 'clients.read', 'loans.read', 'payments.read', 'payments.write'},
    'accountant': {'collections.read', 'clients.read', 'loans.read', 'payments.read', 'reports.read'},
    'viewer': {'collections.read', 'clients.read', 'loans.read', 'reports.read'},
}

def current_membership(user=None):
    user = user or current_user()
    if not user:
        return None
    memberships = (Membership.query.filter_by(user_id=user.id, is_active=True)
                   .order_by(Membership.id.asc()).all())
    if not memberships:
        session.pop('organization_id', None)
        return None
    selected_id = session.get('organization_id')
    if selected_id is not None:
        for membership in memberships:
            if membership.organization_id == selected_id:
                return membership
        # Never trust a stale/tampered organization id from the session.
        session.pop('organization_id', None)
    return memberships[0]


@app.route('/organizacion/seleccionar', methods=['POST'])
@login_required
def select_organization():
    user = current_user()
    try:
        organization_id = int(request.form.get('organization_id', '0'))
    except (TypeError, ValueError):
        abort(400)
    membership = (Membership.query.filter_by(user_id=user.id, organization_id=organization_id, is_active=True).first())
    if not membership:
        abort(403)
    session['organization_id'] = organization_id
    session.modified = True
    return redirect(request.referrer or url_for('dashboard'))

def require_permission(permission):
    user = current_user()
    membership = current_membership(user)
    if not user or not membership or permission not in ROLE_PERMISSIONS.get(membership.role, set()):
        abort(403)
    return user, membership

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Cache-Control'] = 'no-store' if session.get('user_id') else 'no-cache'
    if IS_PRODUCTION:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
            "upgrade-insecure-requests"
        )
    return response

# ---------------------------------------------------------------------------
# Financial engine integration
# ---------------------------------------------------------------------------
def post_ledger(credit, entry_type, amount, currency, payment=None, reference=None, metadata=None):
    entry = LedgerEntry(organization_id=credit.organization_id, credit_id=credit.id, payment_id=payment.id if payment else None,
                        entry_type=entry_type, amount=quantize_money(amount), currency=currency, reference=reference, metadata_json=metadata or {})
    db.session.add(entry)
    return entry

def refresh_overdue(credit, now=None):
    """Refresh aging state in-memory; callers decide whether/when to persist it."""
    now = now or utcnow()
    for inst in credit.installments:
        if inst.balance <= 0 and Decimal(str(inst.late_fee_due or 0)) <= Decimal(str(inst.late_fee_paid or 0)):
            inst.status='paid'; continue
        grace_cutoff = inst.due_date + timedelta(days=max(0, credit.grace_days))
        if now.date() > grace_cutoff.date():
            inst.status='overdue'
            days_late = max(1, (now.date() - grace_cutoff.date()).days)
            if credit.late_fee_type == 'fixed':
                fee = Decimal(str(credit.late_fee_value or 0))
            elif credit.late_fee_type == 'percent_daily':
                fee = quantize_money(inst.balance * Decimal(str(credit.late_fee_value or 0)) / Decimal('100') * Decimal(days_late))
            elif credit.late_fee_type == 'percent_installment':
                fee = quantize_money(inst.amount_due * Decimal(str(credit.late_fee_value or 0)) / Decimal('100'))
            else:
                fee = Decimal('0.00')
            cap = Decimal(str(credit.late_fee_cap or 0))
            if cap > 0: fee=min(fee,cap)
            if fee > inst.late_fee_due: inst.late_fee_due=fee
        elif inst.amount_paid > 0:
            inst.status='partial'
        else:
            inst.status='pending'
    if credit.paid >= credit.total and credit.fees_pending <= 0 and all(i.balance <= 0 for i in credit.installments):
        credit.status='paid'
    elif any(i.status=='overdue' for i in credit.installments):
        credit.status='overdue'
    else:
        credit.status='active'

def _allocate_payment(credit, payment, amount, now=None):
    now=now or utcnow(); left=quantize_money(amount)
    refresh_overdue(credit, now)
    installments=(LoanInstallment.query.filter_by(credit_id=credit.id)
                  .filter(LoanInstallment.status.in_(['pending','partial','overdue']))
                  .order_by(LoanInstallment.number).with_for_update().all())
    total_principal=total_interest=total_late=Decimal('0.00')
    order=[x.strip() for x in credit.payment_order.split(',') if x.strip()]
    for inst in installments:
        if left <= 0: break
        buckets={
            'late_fee': max(Decimal('0.00'), Decimal(str(inst.late_fee_due or 0)) - Decimal(str(inst.late_fee_paid or 0))),
            'interest': max(Decimal('0.00'), Decimal(str(inst.interest_due or 0)) - sum((Decimal(str(a.interest_amount)) for a in inst.allocations if a.payment.status=='posted'), Decimal('0.00'))),
            'principal': max(Decimal('0.00'), Decimal(str(inst.principal_due or 0)) - sum((Decimal(str(a.principal_amount)) for a in inst.allocations if a.payment.status=='posted'), Decimal('0.00'))),
        }
        paid_late=paid_interest=paid_principal=Decimal('0.00')
        for bucket in order:
            if left<=0: break
            take=min(left,buckets.get(bucket,Decimal('0.00')))
            if bucket=='late_fee': paid_late+=take
            elif bucket=='interest': paid_interest+=take
            elif bucket=='principal': paid_principal+=take
            left=quantize_money(left-take)
        used=paid_late+paid_interest+paid_principal
        if used>0:
            inst.amount_paid=quantize_money(inst.amount_paid+paid_interest+paid_principal)
            inst.late_fee_paid=quantize_money(inst.late_fee_paid+paid_late)
            total_principal=quantize_money(total_principal+paid_principal); total_interest=quantize_money(total_interest+paid_interest); total_late=quantize_money(total_late+paid_late)
            db.session.add(PaymentAllocation(organization_id=credit.organization_id,payment_id=payment.id,installment_id=inst.id,amount=used,late_fee_amount=paid_late,interest_amount=paid_interest,principal_amount=paid_principal))
            if inst.balance<=0 and Decimal(str(inst.late_fee_due or 0)) <= Decimal(str(inst.late_fee_paid or 0)): inst.status='paid'; inst.paid_at=now
            elif now.date() > (inst.due_date + timedelta(days=max(0, credit.grace_days))).date(): inst.status='overdue'
            else: inst.status='partial'
    if left>0:
        # Overpayment is forbidden; caller should validate against total outstanding.
        raise ValueError('Pago excede el saldo contractual.')
    credit.paid=quantize_money(credit.paid+total_principal+total_interest)
    credit.paid_late_fees=quantize_money(credit.paid_late_fees+total_late)
    refresh_overdue(credit,now)
    return installments

def outstanding_with_fees(credit):
    refresh_overdue(credit)
    return sum((Decimal(str(i.balance))+Decimal(str(i.late_fee_due or 0)) for i in credit.installments),Decimal('0.00'))

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route('/health', methods=['GET'])
def health():
    """Liveness probe for the hosting platform; does not expose secrets or DB details."""
    return {"status": "ok", "service": "managerws"}, 200


@app.route('/ready', methods=['GET'])
def readiness():
    """Readiness probe; checks database connectivity without exposing internals."""
    try:
        db.session.execute(text('SELECT 1'))
        db.session.remove()
        return {"status": "ready"}, 200
    except Exception:
        db.session.rollback()
        logger.exception('ManagerWS readiness database check failed')
        return {"status": "not_ready"}, 503


@app.route('/')
def index():
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit('15 per minute')
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        user = User.query.filter_by(username=username).first()
        # Always perform a password-hash verification, including for unknown users,
        # to reduce username-enumeration through response timing.
        password_ok = check_password_hash(user.password, password) if user else check_password_hash(DUMMY_PASSWORD_HASH, password)
        if user and user.is_active and password_ok:
            if not user.email_verified:
                flash('Verifica tu correo antes de iniciar sesión.', 'warning')
                return render_template('login.html')
            # Clear the previous signed session before establishing a new identity.
            session.clear()
            session['user_id'] = user.id
            session.permanent = True
            user.last_login = utcnow()
            audit('LOGIN_SUCCESS', actor_user_id=user.id)
            db.session.commit()
            return redirect(url_for('index'))
        logger.warning('Intento de login fallido para usuario=%s', username[:80])
        flash('Credenciales incorrectas.', 'danger')
    return render_template('login.html')


@app.route('/register', methods=['POST'])
@limiter.limit('5 per minute')
def register():
    username = (request.form.get('username') or '').strip()
    email = (request.form.get('email') or '').strip().lower()
    password = request.form.get('password') or ''
    currency = request.form.get('currency', 'GTQ')
    business_type = request.form.get('business_type', 'lender')

    if not 3 <= len(username) <= 80:
        flash('El usuario debe tener entre 3 y 80 caracteres.', 'danger')
        return redirect(url_for('login'))
    if '@' not in email or len(email) > 254:
        flash('Debe proporcionar un correo válido.', 'danger')
        return redirect(url_for('login'))
    if len(password) < 10:
        flash('La contraseña debe tener al menos 10 caracteres.', 'danger')
        return redirect(url_for('login'))
    if currency not in CURRENCIES:
        currency = 'GTQ'
    if business_type not in ORG_TYPES:
        business_type = 'lender'
    if User.query.filter((User.username == username) | (User.email == email)).first():
        flash('El usuario o correo ya está registrado.', 'danger')
        return redirect(url_for('login'))
    if not email_provider_configured():
        flash('No podemos crear la cuenta todavía: el servicio de correo de verificación no está configurado. Intenta más tarde.', 'danger')
        logger.error('Registro bloqueado: no hay proveedor de correo configurado.')
        return redirect(url_for('login'))

    org_name = (request.form.get('organization_name') or username).strip()[:160]
    slug = f"{username.lower().replace(' ', '-')}-{secrets.token_hex(4)}"
    user = User(
        username=username,
        email=email,
        password=generate_password_hash(password, method='pbkdf2:sha256:600000'),
        role='owner',
        currency=currency,
    )
    org = Organization(name=org_name or username, slug=slug, business_type=business_type, currency=currency)
    raw = secrets.token_urlsafe(48)

    try:
        db.session.add_all([user, org])
        db.session.flush()
        db.session.add(Membership(organization_id=org.id, user_id=user.id, role='owner'))
        db.session.add(EmailVerificationToken(
            user_id=user.id,
            token_hash=generate_password_hash(raw, method='pbkdf2:sha256:600000'),
            expires_at=utcnow() + timedelta(hours=24),
        ))
        audit('USER_REGISTERED', actor_user_id=user.id, organization_id=org.id,
              target_type='user', target_id=user.id)

        token = make_token({'uid': user.id, 'secret': raw}, 'email-verification')
        base_url = os.environ.get('PUBLIC_BASE_URL') or request.url_root
        verification_url = urljoin(base_url.rstrip('/') + '/', url_for('verify_email', token=token))
        sent = send_email(
            user.email,
            'Verifica tu cuenta de ManagerW.S',
            f'Verifica tu cuenta aquí:\n\n{verification_url}\n\nEl enlace vence en 24 horas.',
        )
        if not sent:
            db.session.rollback()
            flash('No se creó la cuenta porque no pudimos enviar el correo de verificación. Revisa la configuración de correo e inténtalo nuevamente.', 'danger')
            return redirect(url_for('login'))

        # Persist the company and owner only after the verification email was accepted.
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('El usuario o correo ya está registrado. Comprueba los datos e inténtalo nuevamente.', 'danger')
        return redirect(url_for('login'))
    except Exception:
        db.session.rollback()
        logger.exception('Falló el alta de empresa; la transacción fue revertida.')
        flash('No se pudo completar el registro. No se creó la empresa; inténtalo nuevamente.', 'danger')
        return redirect(url_for('login'))

    flash('Cuenta y empresa creadas. Revisa tu correo para verificar la cuenta antes de iniciar sesión.', 'success')
    return redirect(url_for('login'))


@app.route('/resend-verification', methods=['POST'])
@limiter.limit('3 per hour')
def resend_verification():
    email = (request.form.get('email') or '').strip().lower()
    user = User.query.filter(func.lower(User.email) == email).first() if email and len(email) <= 254 else None
    if user and user.is_active and not user.email_verified:
        if not email_provider_configured():
            logger.error('No se pudo reenviar verificación: no hay proveedor de correo configurado.')
        else:
            raw = secrets.token_urlsafe(48)
            try:
                # Revoke the previous token only if a new message is accepted for sending.
                EmailVerificationToken.query.filter_by(user_id=user.id, used_at=None).update(
                    {'used_at': utcnow()}, synchronize_session=False
                )
                db.session.add(EmailVerificationToken(
                    user_id=user.id,
                    token_hash=generate_password_hash(raw, method='pbkdf2:sha256:600000'),
                    expires_at=utcnow() + timedelta(hours=24),
                ))
                db.session.flush()
                token = make_token({'uid': user.id, 'secret': raw}, 'email-verification')
                base_url = os.environ.get('PUBLIC_BASE_URL') or request.url_root
                verification_url = urljoin(
                    base_url.rstrip('/') + '/',
                    url_for('verify_email', token=token),
                )
                sent = send_email(
                    user.email,
                    'Verifica tu cuenta de ManagerW.S',
                    f'Verifica tu cuenta aquí:\n\n{verification_url}\n\nEl enlace vence en 24 horas.',
                )
                if sent:
                    db.session.commit()
                else:
                    db.session.rollback()
                    logger.warning('No se pudo reenviar el correo de verificación; se conservó el estado anterior del token.')
            except Exception:
                db.session.rollback()
                logger.exception('Error al reenviar el correo de verificación.')

    # Keep the response identical for existing, unknown, active, and already verified emails.
    flash('Si la cuenta existe y necesita verificación, enviaremos un nuevo enlace.', 'success')
    return redirect(url_for('login'))


@app.route('/verify-email/<token>')
@limiter.limit('10 per hour')
def verify_email(token):
    payload = read_token(token, 'email-verification', 24 * 3600)
    if not payload:
        flash('El enlace de verificación no es válido o expiró.', 'danger')
        return redirect(url_for('login'))
    user = db.session.get(User, payload.get('uid'))
    candidates = EmailVerificationToken.query.filter_by(user_id=user.id, used_at=None).order_by(EmailVerificationToken.created_at.desc()).all() if user else []
    token_row = next((row for row in candidates if check_password_hash(row.token_hash, payload.get('secret', ''))), None)
    if not user or not token_row or token_row.expires_at <= utcnow():
        flash('El enlace de verificación no es válido o expiró.', 'danger')
        return redirect(url_for('login'))
    user.email_verified = True
    token_row.used_at = utcnow()
    audit('EMAIL_VERIFIED', actor_user_id=user.id)
    db.session.commit()
    flash('Correo verificado correctamente.', 'success')
    return redirect(url_for('login'))


@app.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit('5 per hour')
def forgot_password():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        user = User.query.filter_by(email=email).first()
        # Constant response avoids account enumeration.
        if user and user.is_active:
            try:
                # Keep existing valid links until the replacement email is accepted.
                raw = secrets.token_urlsafe(48)
                reset_row = PasswordResetToken(
                    user_id=user.id,
                    token_hash=generate_password_hash(raw, method='pbkdf2:sha256:600000'),
                    expires_at=utcnow() + timedelta(minutes=30),
                )
                db.session.add(reset_row)
                db.session.flush()
                token = make_token({'uid': user.id, 'secret': raw}, 'password-reset')
                reset_url = urljoin(
                    (os.environ.get('PUBLIC_BASE_URL') or request.host_url).rstrip('/') + '/',
                    url_for('reset_password', token=token),
                )
                sent = send_email(
                    user.email,
                    'Restablecer contraseña de ManagerW.S',
                    f'Restablece tu contraseña aquí:\\n\\n{reset_url}\\n\\nEl enlace vence en 30 minutos.',
                )
                if sent:
                    PasswordResetToken.query.filter(
                        PasswordResetToken.user_id == user.id,
                        PasswordResetToken.used_at.is_(None),
                        PasswordResetToken.id != reset_row.id,
                    ).update({'used_at': utcnow()}, synchronize_session=False)
                    db.session.commit()
                else:
                    db.session.rollback()
                    logger.warning(
                        'No se pudo enviar el correo de restablecimiento; se conservó el enlace anterior.'
                    )
            except Exception:
                db.session.rollback()
                logger.exception('Error al preparar el correo de restablecimiento de contraseña.')

        flash('Si el correo existe, recibirás instrucciones para restablecer la contraseña.', 'success')
        return redirect(url_for('login'))
    return render_template('login.html', reset_mode=True)



