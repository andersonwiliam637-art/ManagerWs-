import os
import secrets
import logging
import smtplib
from datetime import datetime, timedelta, date
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from functools import wraps
from urllib.parse import urljoin
import base64
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
storage_uri = os.environ.get('REDIS_URL', 'memory://')
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
if IS_PRODUCTION and not os.environ.get('SMTP_HOST'):
    raise RuntimeError('SMTP_HOST debe estar definido en producción para verificación y recuperación de cuentas.')
if IS_PRODUCTION and not os.environ.get('REDIS_URL'):
    raise RuntimeError('REDIS_URL debe estar definida en producción para rate limiting compartido entre instancias.')

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///managerws_gt.db'
# Production must target the existing private Supabase schema, never silently create
# a parallel public-schema database. PostgreSQL's search_path keeps legacy ORM table
# names bound to managerws while migrations continue to qualify managerws explicitly.
if db_url and db_url.startswith(('postgresql://', 'postgresql+psycopg2://')):
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {'options': '-csearch_path=managerws,public'},
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }
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
    'paypal_email': os.environ.get('PAYPAL_EMAIL', ''),
    'paypal_mode': os.environ.get('PAYPAL_MODE', 'sandbox'),
    'paypal_client_id': os.environ.get('PAYPAL_CLIENT_ID', ''),
    'wise': os.environ.get('WISE_ACCOUNT', ''),
    'bank_gt': os.environ.get('BANK_GT', ''),
    'whatsapp': os.environ.get('WHATSAPP', ''),
}

PLAN_NAMES = {'Gratis', 'pro', 'business', 'enterprise'}
DUMMY_PASSWORD_HASH = generate_password_hash('managerws-dummy-password', method='pbkdf2:sha256:600000')
SUBSCRIPTION_STATUSES = {'approval_pending', 'trialing', 'active', 'past_due', 'canceled', 'unpaid', 'expired'}
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


def send_email(to, subject, body):
    host = os.environ.get('SMTP_HOST')
    if not host:
        logger.warning('SMTP no configurado; correo para %s no enviado.', to)
        return False
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = os.environ.get('SMTP_FROM', 'no-reply@managerws.local')
    msg['To'] = to
    msg.set_content(body)
    port = int(os.environ.get('SMTP_PORT', '587'))
    username = os.environ.get('SMTP_USERNAME')
    password = os.environ.get('SMTP_PASSWORD')
    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.starttls()
            if username:
                smtp.login(username, password or '')
            smtp.send_message(msg)
        return True
    except Exception:
        logger.exception('No se pudo enviar correo a %s', to)
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
    # Present in managerws.subscription; required by the PayPal checkout flow below.
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
    id = db.Column(db.BigInteger, primary_key=True)
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
    id = db.Column(db.BigInteger, primary_key=True)
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
    id = db.Column(db.BigInteger, primary_key=True)
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
    db.session.add_all([user, org])
    db.session.flush()
    db.session.add(Membership(organization_id=org.id, user_id=user.id, role='owner'))
    # Only the newest verification token remains usable.
    EmailVerificationToken.query.filter_by(user_id=user.id, used_at=None).update({'used_at': utcnow()}, synchronize_session=False)
    raw = secrets.token_urlsafe(48)
    db.session.add(EmailVerificationToken(
        user_id=user.id,
        token_hash=generate_password_hash(raw, method='pbkdf2:sha256:600000'),
        expires_at=utcnow() + timedelta(hours=24),
    ))
    audit('USER_REGISTERED', actor_user_id=user.id, organization_id=org.id, target_type='user', target_id=user.id)
    db.session.commit()

    token = make_token({'uid': user.id, 'secret': raw}, 'email-verification')
    verification_url = urljoin(os.environ.get('PUBLIC_BASE_URL', request.host_url), url_for('verify_email', token=token))
    sent = send_email(user.email, 'Verifica tu cuenta de ManagerW.S', f'Verifica tu cuenta aquí:\n\n{verification_url}\n\nEl enlace vence en 24 horas.')
    if not sent and not IS_PRODUCTION:
        logger.info('DEV email verification URL: %s', verification_url)
    flash('Cuenta creada. Revisa tu correo para verificarla antes de usar la plataforma.', 'success')
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
            # Revoke older outstanding reset links before issuing a new one.
            PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).update({'used_at': utcnow()}, synchronize_session=False)
            raw = secrets.token_urlsafe(48)
            db.session.add(PasswordResetToken(
                user_id=user.id,
                token_hash=generate_password_hash(raw, method='pbkdf2:sha256:600000'),
                expires_at=utcnow() + timedelta(minutes=30),
            ))
            db.session.commit()
            token = make_token({'uid': user.id, 'secret': raw}, 'password-reset')
            reset_url = urljoin(os.environ.get('PUBLIC_BASE_URL', request.host_url), url_for('reset_password', token=token))
            sent = send_email(user.email, 'Restablecer contraseña de ManagerW.S', f'Restablece tu contraseña aquí:\n\n{reset_url}\n\nEl enlace vence en 30 minutos.')
            if not sent and not IS_PRODUCTION:
                logger.info('DEV password reset URL: %s', reset_url)
        flash('Si el correo existe, recibirás instrucciones para restablecer la contraseña.', 'success')
        return redirect(url_for('login'))
    return render_template('login.html', reset_mode=True)


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
@limiter.limit('10 per hour')
def reset_password(token):
    payload = read_token(token, 'password-reset', 30 * 60)
    if not payload:
        flash('El enlace no es válido o expiró.', 'danger')
        return redirect(url_for('login'))
    user = db.session.get(User, payload.get('uid'))
    candidates = PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).order_by(PasswordResetToken.created_at.desc()).all() if user else []
    token_row = next((row for row in candidates if check_password_hash(row.token_hash, payload.get('secret', ''))), None)
    if not user or not token_row or token_row.expires_at <= utcnow():
        flash('El enlace no es válido o expiró.', 'danger')
        return redirect(url_for('login'))
    if request.method == 'POST':
        password = request.form.get('password') or ''
        if len(password) < 10:
            flash('La contraseña debe tener al menos 10 caracteres.', 'danger')
            return render_template('login.html', reset_mode=True)
        user.password = generate_password_hash(password, method='pbkdf2:sha256:600000')
        PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).update({'used_at': utcnow()}, synchronize_session=False)
        audit('PASSWORD_RESET', actor_user_id=user.id)
        db.session.commit()
        flash('Contraseña actualizada. Ya puedes iniciar sesión.', 'success')
        return redirect(url_for('login'))
    return render_template('login.html', reset_mode=True, reset_token=token)


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    uid = session.get('user_id')
    audit('LOGOUT', actor_user_id=uid)
    db.session.commit()
    session.clear()
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for('login'))
    membership = current_membership(user)
    if not membership: abort(403)
    credits = Credit.query.filter_by(organization_id=membership.organization_id).options(selectinload(Credit.payments)).all()
    clients_count = Client.query.filter_by(organization_id=membership.organization_id, is_active=True).count()
    subscription = (Subscription.query.filter_by(organization_id=membership.organization_id)
                    .order_by(Subscription.created_at.desc()).first())
    has_active_subscription = bool(subscription and subscription.is_current)
    total_lent = sum((Decimal(str(c.total)) for c in credits), Decimal('0.00'))
    total_paid = sum((Decimal(str(c.paid)) for c in credits), Decimal('0.00'))
    pending = max(Decimal('0.00'), total_lent - total_paid)

    labels, data = [], []
    now = utcnow()
    months = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
    for i in range(5, -1, -1):
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        labels.append(months[m - 1])
        month_total = sum((Decimal(str(p.amount)) for c in credits for p in c.payments
                           if p.status == 'posted' and p.date and p.date.month == m and p.date.year == y), Decimal('0.00'))
        data.append(float(month_total))

    for credit in credits:
        refresh_overdue(credit)
    db.session.commit()
    statuses = {
        'Al día': sum(1 for c in credits if c.status == 'active'),
        'Pagado': sum(1 for c in credits if c.status == 'paid'),
        'Vencido': sum(1 for c in credits if c.status == 'overdue'),
    }
    return render_template('dashboard.html', user=user, money=money, total_lent=total_lent,
                           total_paid=total_paid, pending=pending, labels=labels, data=data,
                           statuses=statuses, clients_count=clients_count, has_active_subscription=has_active_subscription, subscription=subscription, pay=PAYMENT_CONFIG)


@app.route('/clients', methods=['GET', 'POST'])
@login_required
@subscription_active
def clients():
    user = current_user()
    membership = current_membership(user)
    if not membership:
        abort(403)
    if request.method == 'POST':
        require_permission('clients.write')
    elif 'clients.read' not in ROLE_PERMISSIONS.get(membership.role, set()):
        abort(403)
    org_id = membership.organization_id
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        phone = (request.form.get('phone') or '').strip()
        if not name or len(name) < 2 or len(name) > 120:
            flash('El nombre del cliente es obligatorio.', 'danger')
            return redirect(url_for('clients'))
        client = Client(organization_id=org_id, user_id=user.id, name=name, phone=phone or None)
        db.session.add(client)
        audit('CLIENT_CREATED', actor_user_id=user.id, organization_id=org_id, target_type='client', target_id='new')
        db.session.commit()
        flash('Cliente registrado correctamente.', 'success')
        return redirect(url_for('clients'))

    clients_list = Client.query.filter_by(organization_id=org_id, is_active=True).order_by(Client.name).all()
    credits_list = (Credit.query.filter_by(organization_id=org_id)
                    .options(selectinload(Credit.client), selectinload(Credit.payments))
                    .order_by(Credit.created_at.desc()).limit(200).all())
    return render_template('clients.html', clients=clients_list, credits=credits_list,
                           money=money, user=user, pay=PAYMENT_CONFIG)


@app.route('/prestamos/solicitudes', methods=['GET', 'POST'])
@login_required
@subscription_active
def loan_applications():
    user, membership = require_permission('loans.read')
    oid=membership.organization_id
    if request.method == 'POST':
        require_permission('loans.write')
        client_id=request.form.get('client_id', type=int)
        amount=safe_decimal(request.form.get('requested_amount'))
        term_raw=request.form.get('requested_term','')
        try: term=int(term_raw)
        except (TypeError,ValueError): term=0
        frequency=request.form.get('frequency','monthly')
        method=request.form.get('interest_method','simple')
        rate=safe_decimal(request.form.get('interest_rate'),'0.00')
        purpose=(request.form.get('purpose') or '').strip()[:255]
        client=Client.query.filter_by(id=client_id, organization_id=oid, is_active=True).first()
        if not client or amount<=0 or not (1<=term<=120) or rate>100 or frequency not in FREQUENCY_DAYS or method not in {'simple','amortized'}:
            flash('Datos de solicitud no válidos.', 'danger')
            return redirect(url_for('loan_applications'))
        a=LoanApplication(organization_id=oid,client_id=client.id,requested_amount=amount,requested_term=term,
                          frequency=frequency,interest_rate=rate,interest_method=method,purpose=purpose,created_by=user.id)
        db.session.add(a); db.session.flush()
        audit('LOAN_APPLICATION_CREATED',actor_user_id=user.id,organization_id=oid,target_type='loan_application',target_id=a.id,
              metadata={'amount':str(amount),'term':term,'frequency':frequency})
        db.session.commit(); flash('Solicitud de crédito creada.', 'success')
        return redirect(url_for('loan_applications'))
    apps=LoanApplication.query.filter_by(organization_id=oid).order_by(LoanApplication.created_at.desc()).limit(300).all()
    clients=Client.query.filter_by(organization_id=oid,is_active=True).order_by(Client.name).all()
    return render_template('loan_applications.html', applications=apps, clients=clients, user=user, money=money)

@app.route('/prestamos/solicitudes/<int:application_id>/decision', methods=['POST'])
@login_required
@subscription_active
def loan_application_decision(application_id):
    user,membership=require_permission('loans.review')
    a=LoanApplication.query.filter_by(id=application_id,organization_id=membership.organization_id).with_for_update().first_or_404()
    if a.status!='pending':
        flash('La solicitud ya fue procesada.', 'warning'); return redirect(url_for('loan_applications'))
    decision=request.form.get('decision')
    if decision not in {'approved','rejected'}: abort(400)
    a.status=decision; a.reviewed_by=user.id; a.reviewed_at=utcnow(); a.decision_note=(request.form.get('decision_note') or '').strip()[:500]
    audit('LOAN_APPLICATION_'+decision.upper(),actor_user_id=user.id,organization_id=membership.organization_id,target_type='loan_application',target_id=a.id)
    db.session.commit(); flash('Decisión registrada.', 'success')
    return redirect(url_for('loan_applications'))

@app.route('/prestamos/solicitudes/<int:application_id>/desembolsar', methods=['POST'])
@login_required
@subscription_active
def loan_application_disburse(application_id):
    user,membership=require_permission('loans.write'); oid=membership.organization_id
    a=LoanApplication.query.filter_by(id=application_id,organization_id=oid).with_for_update().first_or_404()
    if a.status!='approved':
        flash('Solo una solicitud aprobada puede desembolsarse.', 'danger'); return redirect(url_for('loan_applications'))
    if Credit.query.filter_by(application_id=a.id,organization_id=oid).first():
        flash('Esta solicitud ya fue desembolsada.', 'warning'); return redirect(url_for('loan_applications'))
    raw=(request.form.get('first_due_date') or '').strip()
    try: first_due=datetime.strptime(raw,'%Y-%m-%d') if raw else add_period(utcnow(),a.frequency)
    except ValueError:
        flash('Fecha de primera cuota inválida.','danger'); return redirect(url_for('loan_applications'))
    try:
        schedule,total=build_schedule(a.requested_amount,a.interest_rate,a.requested_term,a.frequency,a.interest_method,first_due)
        credit=Credit(organization_id=oid,user_id=user.id,client_id=a.client_id,application_id=a.id,principal=a.requested_amount,total=total,
                      interest_rate=a.interest_rate,interest_method=a.interest_method,term_count=a.requested_term,frequency=a.frequency,
                      disbursed_at=utcnow(),first_due_date=first_due,due_date=schedule[-1]['due_date'],status='active')
        db.session.add(credit); db.session.flush()
        for row in schedule: db.session.add(LoanInstallment(organization_id=oid,credit_id=credit.id,**row))
        post_ledger(credit,'disbursement',a.requested_amount,user.currency,reference=f'LOAN-{credit.id}',metadata={'application_id':a.id,'total':str(total)})
        audit('LOAN_DISBURSED',actor_user_id=user.id,organization_id=oid,target_type='credit',target_id=credit.id,
              metadata={'application_id':a.id,'principal':str(a.requested_amount),'total':str(total)})
        db.session.commit()
    except Exception:
        db.session.rollback(); logger.exception('Error desembolsando préstamo'); flash('No fue posible desembolsar el préstamo.','danger'); return redirect(url_for('loan_applications'))
    flash(f'Préstamo desembolsado. Total programado: {money(total,user.currency)}.','success')
    return redirect(url_for('loan_detail',id=credit.id))

@app.route('/prestamos/cobranza')
@login_required
@subscription_active
def collections():
    user, membership = require_permission('collections.read')
    oid = membership.organization_id
    today = utcnow()
    policy = db.session.get(CollectionPolicy, oid)
    if not policy:
        policy = CollectionPolicy(organization_id=oid)
    assigned_collector = request.args.get('collector_id', type=int)
    unassigned_only = request.args.get('unassigned') == '1'
    portfolio = (db.session.query(Credit)
                 .filter(Credit.organization_id == oid, Credit.status.notin_(['paid', 'canceled']))
                 .options(selectinload(Credit.client), selectinload(Credit.installments))
                 .order_by(Credit.created_at.desc()).limit(500).all())
    active_assignments = {a.credit_id: a for a in CollectionAssignment.query.filter_by(organization_id=oid, active=True).all()}
    rows = []
    thresholds = [(policy.overdue_4_days, 'critical'), (policy.overdue_3_days, 'critical'),
                  (policy.overdue_2_days, 'overdue'), (policy.overdue_1_days, 'overdue')]
    for credit in portfolio:
        assignment = active_assignments.get(credit.id)
        if assigned_collector and (not assignment or assignment.collector_user_id != assigned_collector):
            continue
        if unassigned_only and assignment:
            continue
        outstanding = credit.total_due
        overdue = credit.overdue_amount
        overdue_days = []
        for installment in credit.installments:
            grace_cutoff = installment.due_date + timedelta(days=max(0, credit.grace_days))
            if (today.date() > grace_cutoff.date()
                    and (installment.amount_due + installment.late_fee_due
                         - installment.amount_paid - installment.late_fee_paid) > 0):
                overdue_days.append((today.date() - grace_cutoff.date()).days)
        days_overdue = max(overdue_days, default=0)
        priority = 'current'
        if days_overdue >= policy.overdue_4_days:
            priority = 'critical'
        elif days_overdue >= policy.overdue_3_days:
            priority = 'critical'
        elif days_overdue >= policy.overdue_2_days:
            priority = 'overdue'
        elif days_overdue >= policy.overdue_1_days:
            priority = 'overdue'
        rows.append({'credit': credit, 'outstanding': outstanding, 'overdue': overdue,
                     'priority': priority, 'days_overdue': days_overdue, 'assignment': assignment})
    rows.sort(key=lambda r: ({'critical': 0, 'overdue': 1, 'current': 2}[r['priority']], -float(r['overdue']), r['credit'].id))
    activities = (CollectionActivity.query.filter_by(organization_id=oid)
                  .order_by(CollectionActivity.created_at.desc()).limit(100).all())
    collectors = (User.query.join(Membership, Membership.user_id == User.id)
                  .filter(Membership.organization_id == oid, Membership.is_active == True, User.is_active == True,
                          Membership.role.in_(['collector','seller','manager']))
                  .order_by(User.username.asc()).all())
    return render_template('collections.html', rows=rows, activities=activities, collectors=collectors,
                           can_assign='collections.assign' in ROLE_PERMISSIONS.get(membership.role, set()),
                           user=user, money=money, today=today, policy=policy)


@app.route('/prestamos/cobranza/asignar', methods=['POST'])
@login_required
@subscription_active
def collection_assign():
    user, membership = require_permission('collections.assign')
    oid = membership.organization_id
    credit_id = request.form.get('credit_id', type=int)
    collector_id = request.form.get('collector_id', type=int)
    note = (request.form.get('note') or '').strip()[:500] or None
    credit = Credit.query.filter_by(id=credit_id, organization_id=oid).first_or_404()
    collector_membership = Membership.query.filter_by(organization_id=oid, user_id=collector_id, is_active=True).first()
    collector = db.session.get(User, collector_id)
    if not collector_membership or not collector or not collector.is_active or collector_membership.role not in ('collector','seller','manager'):
        abort(400)
    now = utcnow()
    active = CollectionAssignment.query.filter_by(organization_id=oid, credit_id=credit.id, active=True).first()
    if active and active.collector_user_id == collector_id:
        active.note = note
    else:
        if active:
            active.active = False; active.unassigned_at = now
        db.session.add(CollectionAssignment(organization_id=oid, credit_id=credit.id, collector_user_id=collector_id,
                                            assigned_by=user.id, active=True, assigned_at=now, note=note))
    audit('COLLECTION_ASSIGNED', actor_user_id=user.id, organization_id=oid, target_type='credit', target_id=credit.id,
          metadata={'collector_user_id': collector_id, 'previous_collector_user_id': active.collector_user_id if active else None})
    db.session.commit()
    flash('Cobranza asignada correctamente.', 'success')
    return redirect(url_for('collections'))


@app.route('/prestamos/cobranza/desasignar', methods=['POST'])
@login_required
@subscription_active
def collection_unassign():
    user, membership = require_permission('collections.assign')
    oid = membership.organization_id
    credit_id = request.form.get('credit_id', type=int)
    assignment = CollectionAssignment.query.filter_by(organization_id=oid, credit_id=credit_id, active=True).first()
    if assignment:
        assignment.active = False; assignment.unassigned_at = utcnow()
        audit('COLLECTION_UNASSIGNED', actor_user_id=user.id, organization_id=oid, target_type='credit', target_id=credit_id,
              metadata={'collector_user_id': assignment.collector_user_id})
        db.session.commit()
    return redirect(url_for('collections'))


@app.route('/prestamos/cobranza/actividad', methods=['POST'])
@login_required
@subscription_active
def collection_activity_create():
    user, membership = require_permission('collections.write')
    oid = membership.organization_id
    client_id = request.form.get('client_id', type=int)
    credit_id = request.form.get('credit_id', type=int)
    action_type = (request.form.get('action_type') or '').strip().lower()
    outcome = (request.form.get('outcome') or '').strip()[:30] or None
    note = (request.form.get('note') or '').strip()[:2000] or None
    promised_amount = safe_decimal(request.form.get('promised_amount'), Decimal('0.00'))
    promised_at_raw = (request.form.get('promised_at') or '').strip()
    next_action_raw = (request.form.get('next_action_at') or '').strip()
    allowed = {'call','visit','whatsapp','sms','email','promise','payment','other'}
    if action_type not in allowed or promised_amount < 0:
        abort(400)
    client = Client.query.filter_by(id=client_id, organization_id=oid, is_active=True).first()
    if not client:
        abort(404)
    credit = None
    if credit_id:
        credit = Credit.query.filter_by(id=credit_id, organization_id=oid, client_id=client.id).first()
        if not credit:
            abort(404)
    def parse_dt(raw):
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            abort(400)
    promised_at = parse_dt(promised_at_raw)
    next_action_at = parse_dt(next_action_raw)
    if action_type == 'promise' and promised_amount <= 0:
        flash('Una promesa de pago debe incluir un monto mayor que cero.', 'danger')
        return redirect(url_for('collections'))
    activity = CollectionActivity(organization_id=oid, client_id=client.id, credit_id=credit.id if credit else None,
                                  user_id=user.id, action_type=action_type, outcome=outcome, note=note,
                                  promised_amount=promised_amount, promised_at=promised_at, next_action_at=next_action_at)
    db.session.add(activity)
    audit('COLLECTION_ACTIVITY_CREATED', actor_user_id=user.id, organization_id=oid, target_type='collection_activity',
          target_id='new', metadata={'client_id': client.id, 'credit_id': credit.id if credit else None, 'action_type': action_type,
                                     'promised_amount': str(promised_amount)})
    db.session.commit()
    flash('Actividad de cobranza registrada.', 'success')
    return redirect(url_for('collections'))


@app.route('/credit/new', methods=['POST'])
@login_required
@subscription_active
def new_credit():
    user, membership = require_permission('loans.write')
    client_id = request.form.get('client_id')
    principal = safe_decimal(request.form.get('principal') or request.form.get('total'))
    rate = safe_decimal(request.form.get('interest_rate'), Decimal('0.00'))
    term_count_raw = request.form.get('term_count', '1')
    frequency = request.form.get('frequency', 'monthly')
    method = request.form.get('interest_method', 'simple')
    first_due_raw = (request.form.get('first_due_date') or '').strip()
    try:
        term_count = int(term_count_raw)
    except (TypeError, ValueError):
        term_count = 0
    if principal <= 0 or term_count < 1 or term_count > 120 or rate > Decimal('100'):
        flash('Los términos del crédito no son válidos.', 'danger')
        return redirect(url_for('clients'))
    if frequency not in FREQUENCY_DAYS or method not in {'simple', 'amortized'}:
        flash('Frecuencia o método de interés no válido.', 'danger')
        return redirect(url_for('clients'))
    try:
        first_due = datetime.strptime(first_due_raw, '%Y-%m-%d') if first_due_raw else add_period(utcnow(), frequency)
        client = Client.query.filter_by(id=client_id, organization_id=membership.organization_id, is_active=True).first()
        if not client:
            flash('Cliente no válido.', 'danger')
            return redirect(url_for('clients'))
        schedule, total = build_schedule(principal, rate, term_count, frequency, method, first_due)
        credit = Credit(organization_id=membership.organization_id, user_id=user.id, client_id=client.id,
                        principal=principal, total=total, interest_rate=rate, interest_method=method,
                        term_count=term_count, frequency=frequency, disbursed_at=utcnow(),
                        first_due_date=first_due, due_date=schedule[-1]['due_date'])
        db.session.add(credit)
        db.session.flush()
        for row in schedule:
            db.session.add(LoanInstallment(organization_id=membership.organization_id, credit_id=credit.id, **row))
        post_ledger(credit, 'disbursement', principal, user.currency, reference=f'LOAN-{credit.id}',
                    metadata={'principal': str(principal), 'total_receivable': str(total)})
        audit('CREDIT_CREATED', actor_user_id=user.id, organization_id=membership.organization_id,
              target_type='credit', target_id=credit.id, metadata={'principal': str(principal), 'total': str(total), 'term_count': term_count, 'frequency': frequency, 'interest_method': method})
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('Error creando crédito')
        flash('No fue posible crear el crédito.', 'danger')
        return redirect(url_for('clients'))
    flash(f'Crédito creado. Total programado: {money(total, user.currency)}.', 'success')
    return redirect(url_for('clients'))


@app.route('/payment/new', methods=['POST'])
@login_required
@subscription_active
def new_payment():
    user, membership = require_permission('payments.write')
    amount=safe_decimal(request.form.get('amount'))
    credit_id=request.form.get('credit_id', type=int)
    method=(request.form.get('payment_method') or 'cash').strip().lower()
    idem=(request.form.get('idempotency_key') or secrets.token_urlsafe(24)).strip()[:80]
    if amount<=0 or method not in {'cash','bank','transfer','card','mobile','other'}:
        flash('Datos de pago no válidos.','danger'); return redirect(url_for('clients'))
    try:
        existing=Payment.query.filter_by(idempotency_key=idem,organization_id=membership.organization_id).first()
        if existing:
            flash(f'Pago ya registrado como recibo {existing.receipt_number}.','info'); return redirect(url_for('loan_detail',id=existing.credit_id))
        credit=(Credit.query.filter_by(id=credit_id,organization_id=membership.organization_id).with_for_update().first())
        if not credit: flash('Crédito no válido.','danger'); return redirect(url_for('clients'))
        refresh_overdue(credit)
        total_due=credit.total_due
        if amount>total_due:
            flash(f'El pago no puede superar el saldo total pendiente ({money(total_due,user.currency)}).','danger'); return redirect(url_for('clients'))
        receipt=f'R-{datetime.utcnow().strftime("%Y%m%d")}-{secrets.token_hex(4).upper()}'
        payment=Payment(organization_id=membership.organization_id,credit_id=credit.id,amount=amount,payment_method=method,collector_user_id=user.id,idempotency_key=idem,receipt_number=receipt)
        db.session.add(payment)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            existing=Payment.query.filter_by(idempotency_key=idem,organization_id=membership.organization_id).first()
            if existing:
                flash(f'Pago ya registrado como recibo {existing.receipt_number}.','info')
                return redirect(url_for('loan_detail',id=existing.credit_id))
            raise
        _allocate_payment(credit,payment,amount)
        post_ledger(credit,'payment',-amount,user.currency,payment=payment,reference=f'PAY-{payment.id}',metadata={'receipt':receipt,'payment_method':method})
        if method=='cash':
            cs=CashSession.query.filter_by(organization_id=membership.organization_id,collector_user_id=user.id,status='open').with_for_update().first()
            if not cs:
                raise ValueError('Para pagos en efectivo debes abrir tu caja primero.')
            db.session.add(CashMovement(organization_id=membership.organization_id,cash_session_id=cs.id,user_id=user.id,payment_id=payment.id,movement_type='payment',amount=amount,note=f'Recibo {receipt}'))
        audit('PAYMENT_POSTED',actor_user_id=user.id,organization_id=membership.organization_id,target_type='payment',target_id=payment.id,metadata={'amount':str(amount),'credit_id':credit.id,'receipt':receipt,'payment_method':method})
        db.session.commit()
    except Exception:
        db.session.rollback(); logger.exception('Error registrando pago'); flash('No fue posible registrar el pago.','danger'); return redirect(url_for('clients'))
    flash(f'Pago registrado. Recibo {receipt}.','success'); return redirect(url_for('loan_detail',id=credit.id))


@app.route('/payment/<int:id>/reverse', methods=['POST'])
@login_required
@subscription_active
def reverse_payment(id):
    user,membership=require_permission('payments.reverse'); reason=(request.form.get('reason') or '').strip()[:255]
    try:
        payment=(Payment.query.filter_by(id=id,organization_id=membership.organization_id).with_for_update().first())
        if not payment or payment.status!='posted': flash('El pago no existe o ya fue revertido.','warning'); return redirect(url_for('clients'))
        credit=Credit.query.filter_by(id=payment.credit_id,organization_id=membership.organization_id).with_for_update().first()
        allocations=PaymentAllocation.query.filter_by(payment_id=payment.id).with_for_update().all()
        for a in allocations:
            inst=LoanInstallment.query.filter_by(id=a.installment_id,organization_id=membership.organization_id).with_for_update().first()
            if inst:
                inst.amount_paid=quantize_money(max(Decimal('0.00'),Decimal(str(inst.amount_paid))-Decimal(str(a.principal_amount))-Decimal(str(a.interest_amount))))
                inst.late_fee_paid=quantize_money(max(Decimal('0.00'),Decimal(str(inst.late_fee_paid))-Decimal(str(a.late_fee_amount))))
                inst.paid_at=None
                grace_cutoff = inst.due_date + timedelta(days=max(0, credit.grace_days))
                inst.status='overdue' if utcnow().date() > grace_cutoff.date() else ('partial' if inst.amount_paid>0 or inst.late_fee_paid>0 else 'pending')
        payment.status='reversed'; payment.reversed_at=utcnow(); payment.reversal_reason=reason or 'Reversión administrativa'
        credit.paid=quantize_money(max(Decimal('0.00'),Decimal(str(credit.paid))-sum((Decimal(str(a.principal_amount))+Decimal(str(a.interest_amount)) for a in allocations),Decimal('0.00'))))
        credit.paid_late_fees=quantize_money(max(Decimal('0.00'),Decimal(str(credit.paid_late_fees))-sum((Decimal(str(a.late_fee_amount)) for a in allocations),Decimal('0.00'))))
        refresh_overdue(credit)
        post_ledger(credit,'reversal',payment.amount,user.currency,payment=payment,reference=f'REV-{payment.id}',metadata={'reason':payment.reversal_reason})
        if payment.payment_method == 'cash':
            cs = (CashSession.query.filter_by(organization_id=membership.organization_id, collector_user_id=user.id, status='open')
                  .with_for_update().first())
            if not cs:
                raise ValueError('Para revertir un pago en efectivo debes tener una caja abierta. La reversión no se ha aplicado.')
            db.session.add(CashMovement(organization_id=membership.organization_id, cash_session_id=cs.id, user_id=user.id,
                                        payment_id=payment.id, movement_type='adjustment', amount=-payment.amount,
                                        note=f'Reversión del recibo {payment.receipt_number}'))
        audit('PAYMENT_REVERSED',actor_user_id=user.id,organization_id=membership.organization_id,target_type='payment',target_id=payment.id,metadata={'amount':str(payment.amount),'reason':payment.reversal_reason})
        db.session.commit()
    except Exception:
        db.session.rollback(); logger.exception('Error revirtiendo pago'); flash('No fue posible revertir el pago.','danger'); return redirect(url_for('clients'))
    flash('Pago revertido mediante una operación auditable.','success'); return redirect(url_for('loan_detail',id=credit.id))


@app.route('/loans/<int:id>')
@login_required
@subscription_active
def loan_detail(id):
    user, membership = require_permission('loans.read')
    credit = (Credit.query.filter_by(id=id, organization_id=membership.organization_id)
              .options(selectinload(Credit.client), selectinload(Credit.installments), selectinload(Credit.payments), selectinload(Credit.ledger_entries)).first()) if membership else None
    if not credit:
        abort(404)
    credit.display_overdue_preview = credit.overdue_amount
    return render_template('loan_detail.html', credit=credit, user=user, money=money)


@app.route('/prestamos/pago/<int:id>/recibo')
@login_required
@subscription_active
def payment_receipt(id):
    user,membership=require_permission('payments.read')
    payment=Payment.query.filter_by(id=id,organization_id=membership.organization_id).first_or_404()
    return render_template('payment_receipt.html',payment=payment,user=user,money=money)

@app.route('/prestamos/loans/<int:id>/liquidacion')
@login_required
@subscription_active
def loan_payoff(id):
    user,membership=require_permission('loans.read')
    credit=Credit.query.filter_by(id=id,organization_id=membership.organization_id).first_or_404()
    credit.display_overdue_preview = credit.overdue_amount
    return render_template('loan_payoff.html',credit=credit,user=user,money=money)

@app.route('/prestamos/loans/<int:id>/refinanciar',methods=['POST'])
@login_required
@subscription_active
def refinance_loan(id):
    user,membership=require_permission('loans.write'); oid=membership.organization_id
    old=Credit.query.filter_by(id=id,organization_id=oid).with_for_update().first_or_404()
    if old.status=='paid': flash('Un préstamo pagado no se puede refinanciar.','warning'); return redirect(url_for('loan_detail',id=id))
    refresh_overdue(old); db.session.flush()
    remaining=old.pending
    if remaining<=0: flash('No existe saldo contractual para refinanciar.','warning'); return redirect(url_for('loan_detail',id=id))
    term=request.form.get('term_count',type=int) or old.term_count
    rate=safe_decimal(request.form.get('interest_rate'),old.interest_rate)
    frequency=request.form.get('frequency') or old.frequency
    method=request.form.get('interest_method') or old.interest_method
    if not 1<=term<=120 or rate<0 or rate>100 or frequency not in FREQUENCY_DAYS or method not in {'simple','amortized'}: abort(400)
    first_due_raw=(request.form.get('first_due_date') or '').strip()
    first_due=datetime.strptime(first_due_raw,'%Y-%m-%d') if first_due_raw else add_period(utcnow(),frequency)
    schedule,total=build_schedule(remaining,rate,term,frequency,method,first_due)
    new=Credit(organization_id=oid,user_id=user.id,client_id=old.client_id,principal=remaining,total=total,interest_rate=rate,interest_method=method,term_count=term,frequency=frequency,disbursed_at=utcnow(),first_due_date=first_due,due_date=schedule[-1]['due_date'],status='active',parent_credit_id=old.id,grace_days=old.grace_days,late_fee_type=old.late_fee_type,late_fee_value=old.late_fee_value,late_fee_cap=old.late_fee_cap,payment_order=old.payment_order)
    db.session.add(new); db.session.flush()
    for row in schedule: db.session.add(LoanInstallment(organization_id=oid,credit_id=new.id,**row))
    old.status='restructured'; old.version+=1
    audit('LOAN_REFINANCED',actor_user_id=user.id,organization_id=oid,target_type='credit',target_id=new.id,metadata={'old_credit_id':old.id,'carried_principal':str(remaining)})
    db.session.commit(); flash(f'Préstamo refinanciado. Nuevo crédito #{new.id}.','success'); return redirect(url_for('loan_detail',id=new.id))

@app.route('/prestamos/reportes')
@login_required
@subscription_active
def loan_reports():
    user,membership=require_permission('reports.read'); oid=membership.organization_id
    credits=Credit.query.filter_by(organization_id=oid).all()
    total_principal=sum((Decimal(str(c.principal)) for c in credits if c.status!='canceled'),Decimal('0.00'))
    outstanding=sum((c.pending for c in credits if c.status not in {'paid','canceled'}),Decimal('0.00'))
    overdue=sum((c.overdue_amount for c in credits),Decimal('0.00'))
    paid=sum((Decimal(str(c.paid)) for c in credits),Decimal('0.00'))
    return render_template('loan_reports.html',user=user,money=money,total_principal=total_principal,outstanding=outstanding,overdue=overdue,paid=paid,credits=credits)

@app.route('/prestamos/caja/abrir',methods=['POST'])
@login_required
@subscription_active
def cash_open():
    user,membership=require_permission('payments.write'); oid=membership.organization_id
    existing=CashSession.query.filter_by(organization_id=oid,collector_user_id=user.id,status='open').first()
    if existing: flash('Ya tienes una caja abierta.','warning'); return redirect(url_for('collections'))
    opening=safe_decimal(request.form.get('opening_balance'))
    cs=CashSession(organization_id=oid,collector_user_id=user.id,opening_balance=opening)
    db.session.add(cs); db.session.flush(); db.session.add(CashMovement(organization_id=oid,cash_session_id=cs.id,user_id=user.id,movement_type='opening',amount=opening,note='Apertura de caja'))
    audit('CASH_OPENED',actor_user_id=user.id,organization_id=oid,target_type='cash_session',target_id=cs.id,metadata={'opening':str(opening)}); db.session.commit()
    flash('Caja abierta correctamente.','success'); return redirect(url_for('collections'))

@app.route('/prestamos/caja/cerrar',methods=['POST'])
@login_required
@subscription_active
def cash_close():
    user,membership=require_permission('payments.write'); oid=membership.organization_id
    cs=CashSession.query.filter_by(organization_id=oid,collector_user_id=user.id,status='open').with_for_update().first()
    if not cs: flash('No tienes una caja abierta.','warning'); return redirect(url_for('collections'))
    counted=safe_decimal(request.form.get('counted_balance'))
    inflow=db.session.query(func.coalesce(func.sum(CashMovement.amount),0)).filter(CashMovement.cash_session_id==cs.id,CashMovement.amount>0).scalar() or 0
    outflow=db.session.query(func.coalesce(func.sum(CashMovement.amount),0)).filter(CashMovement.cash_session_id==cs.id,CashMovement.amount<0).scalar() or 0
    expected=quantize_money(Decimal(str(inflow))+Decimal(str(outflow)))
    cs.counted_balance=counted; cs.closing_balance=expected; cs.difference=quantize_money(counted-expected); cs.closed_at=utcnow(); cs.status='closed'
    db.session.add(CashMovement(organization_id=oid,cash_session_id=cs.id,user_id=user.id,movement_type='closing',amount=Decimal('0.00'),note=f'Esperado={expected}; contado={counted}; diferencia={cs.difference}'))
    audit('CASH_CLOSED',actor_user_id=user.id,organization_id=oid,target_type='cash_session',target_id=cs.id,metadata={'expected':str(expected),'counted':str(counted),'difference':str(cs.difference)}); db.session.commit()
    flash(f'Caja cerrada. Diferencia: {money(cs.difference,user.currency)}.','success'); return redirect(url_for('collections'))

def paypal_base_url():
    return 'https://api-m.paypal.com' if os.environ.get('PAYPAL_MODE','sandbox').lower() == 'live' else 'https://api-m.sandbox.paypal.com'

def paypal_credentials_configured():
    return bool(os.environ.get('PAYPAL_CLIENT_ID') and os.environ.get('PAYPAL_CLIENT_SECRET'))

# PayPal Subscriptions only supports specific currencies. Do not infer a PayPal
# billing currency from the visitor's country or silently convert local prices.
PAYPAL_SUBSCRIPTION_CURRENCIES = {
    'AUD', 'CAD', 'CHF', 'CZK', 'DKK', 'EUR', 'GBP', 'HKD', 'HUF', 'ILS',
    'JPY', 'MXN', 'NOK', 'NZD', 'PHP', 'PLN', 'SEK', 'SGD', 'THB', 'TWD', 'USD'
}
PAYPAL_PLAN_TIERS = ('pro', 'business', 'enterprise')


def paypal_currency():
    return (os.environ.get('PAYPAL_CURRENCY', 'USD') or 'USD').strip().upper()


def paypal_plan_env_key(plan, currency):
    return f"PAYPAL_PLAN_{plan.upper()}_{currency.upper()}"


def paypal_price_env_key(plan, currency):
    return f"PAYPAL_PRICE_{plan.upper()}_{currency.upper()}"


def paypal_plan_id(plan, currency):
    return os.environ.get(paypal_plan_env_key(plan, currency), '').strip()


def paypal_price(plan, currency):
    raw = (os.environ.get(paypal_price_env_key(plan, currency), '') or '').strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if not value.is_finite() or value <= 0:
        return None
    return value.quantize(Decimal('0.01'))


def paypal_checkout_configured(plan, currency=None):
    currency = (currency or paypal_currency()).upper()
    if os.environ.get('PAYPAL_ENABLED', 'false').strip().lower() not in {'1', 'true', 'yes', 'on'}:
        return False
    if currency not in PAYPAL_SUBSCRIPTION_CURRENCIES:
        return False
    if IS_PRODUCTION and os.environ.get('PAYPAL_MODE', 'sandbox').lower() != 'live':
        return False
    return bool(
        paypal_credentials_configured()
        and os.environ.get('PAYPAL_WEBHOOK_ID', '').strip()
        and paypal_plan_id(plan, currency)
        and paypal_price(plan, currency) is not None
    )


def paypal_validate_plan(plan_id, currency, expected_amount):
    """Fail closed unless PayPal confirms an active, monthly plan at our displayed price."""
    token = paypal_access_token()
    response = requests.get(
        paypal_base_url() + '/v1/billing/plans/' + plan_id,
        headers={'Accept': 'application/json', 'Authorization': f'Bearer {token}'},
        timeout=15,
    )
    response.raise_for_status()
    details = response.json()
    if details.get('status') != 'ACTIVE':
        raise ValueError('PayPal plan is not active')
    cycles = details.get('billing_cycles') or []
    regular_cycles = [cycle for cycle in cycles if cycle.get('tenure_type') == 'REGULAR']
    if not regular_cycles:
        raise ValueError('PayPal plan has no regular billing cycle')
    for cycle in regular_cycles:
        frequency = cycle.get('frequency') or {}
        pricing = cycle.get('pricing_scheme') or {}
        fixed = pricing.get('fixed_price') or {}
        if (frequency.get('interval_unit') != 'MONTH'
                or int(frequency.get('interval_count', 0)) != 1
                or (fixed.get('currency_code') or '').upper() != currency
                or Decimal(str(fixed.get('value', '0'))) != expected_amount):
            raise ValueError('PayPal plan currency, monthly interval, or amount does not match configured price')
    return details

def paypal_access_token():
    if not paypal_credentials_configured():
        raise RuntimeError('PayPal no está configurado: faltan PAYPAL_CLIENT_ID/PAYPAL_CLIENT_SECRET.')
    raw = f"{os.environ['PAYPAL_CLIENT_ID']}:{os.environ['PAYPAL_CLIENT_SECRET']}".encode()
    auth = base64.b64encode(raw).decode()
    r = requests.post(paypal_base_url() + '/v1/oauth2/token', headers={
        'Accept':'application/json','Accept-Language':'en_US','Authorization':f'Basic {auth}',
        'Content-Type':'application/x-www-form-urlencoded'}, data={'grant_type':'client_credentials'}, timeout=15)
    r.raise_for_status()
    return r.json()['access_token']

def paypal_get_subscription_details(subscription_id):
    token = paypal_access_token()
    response = requests.get(
        paypal_base_url() + '/v1/billing/subscriptions/' + subscription_id,
        headers={'Accept': 'application/json', 'Authorization': f'Bearer {token}'},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def paypal_create_subscription(*, plan_id, custom_id, return_url, cancel_url):
    token = paypal_access_token()
    payload = {
        'plan_id': plan_id,
        'custom_id': custom_id,
        'application_context': {
            'brand_name': 'ManagerW.S',
            'user_action': 'SUBSCRIBE_NOW',
            'return_url': return_url,
            'cancel_url': cancel_url,
        },
    }
    r = requests.post(paypal_base_url() + '/v1/billing/subscriptions',
                       headers={'Accept':'application/json','Content-Type':'application/json','Authorization':f'Bearer {token}',
                                'PayPal-Request-Id': secrets.token_urlsafe(24)},
                       json=payload, timeout=20)
    r.raise_for_status()
    return r.json()

def paypal_link(data, rel):
    return next((x.get('href') for x in data.get('links',[]) if x.get('rel') == rel), None)


@app.route('/planes')
def pricing():
    country = detect_country()
    plan = PLANS_PPP.get(country, PLANS_PPP['GT'])
    pp_currency = paypal_currency()
    paypal_ready = {name: paypal_checkout_configured(name, pp_currency) for name in PAYPAL_PLAN_TIERS}
    paypal_amounts = {name: paypal_price(name, pp_currency) for name in PAYPAL_PLAN_TIERS}
    return render_template('pricing.html', plan=plan, country=country, pay=PAYMENT_CONFIG,
                           paypal_ready=paypal_ready, paypal_amounts=paypal_amounts,
                           paypal_currency=pp_currency, logged='user_id' in session)


@app.route('/billing/paypal/subscribe', methods=['POST'])
@login_required
@limiter.limit('10 per hour')
def paypal_subscribe():
    user = current_user()
    membership = current_membership(user)
    plan = (request.form.get('plan') or '').lower()
    if plan not in ('pro','business','enterprise'):
        abort(400)
    country = detect_country()
    currency = paypal_currency()
    amount = paypal_price(plan, currency)
    pp_plan_id = paypal_plan_id(plan, currency)
    if not paypal_checkout_configured(plan, currency) or amount is None or not pp_plan_id:
        flash('La suscripción PayPal todavía no está habilitada o no tiene una configuración completa.', 'warning')
        return redirect(url_for('pricing'))

    # Avoid accidental double subscriptions for organizations already billed by PayPal.
    existing_active = (Subscription.query.filter_by(
        organization_id=membership.organization_id, provider='paypal'
    ).filter(Subscription.status.in_(['trialing', 'active', 'past_due', 'unpaid'])).first())
    pending_recent = (Subscription.query.filter_by(
        organization_id=membership.organization_id, provider='paypal', status='approval_pending'
    ).filter(Subscription.created_at >= utcnow() - timedelta(minutes=15)).first())
    if existing_active:
        flash('Esta organización ya tiene una suscripción PayPal existente. Contacta al administrador para cambiar de plan.', 'warning')
        return redirect(url_for('pricing'))
    if pending_recent:
        flash('Ya hay un checkout PayPal pendiente para esta organización. Espera unos minutos antes de volver a intentarlo.', 'warning')
        return redirect(url_for('pricing'))

    try:
        paypal_validate_plan(pp_plan_id, currency, amount)
        data = paypal_create_subscription(
            plan_id=pp_plan_id, custom_id=f"org:{membership.organization_id}",
            return_url=url_for('paypal_subscribe_return', _external=True),
            cancel_url=url_for('pricing', _external=True))
    except requests.RequestException:
        logger.exception('PayPal subscription validation or creation failed')
        flash('PayPal no respondió correctamente. No se inició una nueva suscripción; revisa la configuración e inténtalo otra vez.', 'danger')
        return redirect(url_for('pricing'))
    except (RuntimeError, ValueError, InvalidOperation):
        logger.exception('PayPal plan configuration failed closed')
        flash('El plan PayPal no coincide con el precio mensual configurado. No se inició el cobro.', 'danger')
        return redirect(url_for('pricing'))
    pp_id = data.get('id')
    if not pp_id:
        flash('PayPal no devolvió un identificador de suscripción.', 'danger')
        return redirect(url_for('pricing'))
    sub = Subscription(user_id=user.id, organization_id=membership.organization_id, provider='paypal',
                       provider_plan_id=pp_plan_id, provider_subscription_id=pp_id, plan=plan,
                       currency=currency, amount=amount, interval='month', status='approval_pending',
                       metadata_json={'country':country, 'paypal_currency':currency, 'paypal_price':str(amount)})
    db.session.add(sub)
    db.session.flush()
    db.session.add(SubscriptionEvent(organization_id=membership.organization_id, subscription_id=sub.id, provider='paypal',
                                     event_type='CHECKOUT.SUBSCRIPTION.CREATED', provider_event_id=pp_id,
                                     new_status='approval_pending', metadata_json={'plan':plan,'currency':currency}))
    audit('PAYPAL_SUBSCRIPTION_STARTED', actor_user_id=user.id, organization_id=membership.organization_id,
          target_type='subscription', target_id=pp_id, metadata={'plan':plan,'currency':currency})
    db.session.commit()
    approve_url = paypal_link(data, 'approve')
    if not approve_url:
        flash('PayPal creó la suscripción pero no devolvió el enlace de aprobación.', 'danger')
        return redirect(url_for('pricing'))
    return redirect(approve_url)


def paypal_verify_webhook(payload):
    webhook_id = os.environ.get('PAYPAL_WEBHOOK_ID')
    if not webhook_id or not paypal_credentials_configured():
        return False
    required = {
        'transmission_id': request.headers.get('PAYPAL-TRANSMISSION-ID'),
        'transmission_time': request.headers.get('PAYPAL-TRANSMISSION-TIME'),
        'cert_url': request.headers.get('PAYPAL-CERT-URL'),
        'auth_algo': request.headers.get('PAYPAL-AUTH-ALGO'),
        'transmission_sig': request.headers.get('PAYPAL-TRANSMISSION-SIG'),
        'webhook_id': webhook_id,
        'webhook_event': payload,
    }
    if not all(required[k] for k in required if k != 'webhook_event'):
        return False
    try:
        token = paypal_access_token()
        r = requests.post(paypal_base_url() + '/v1/notifications/verify-webhook-signature',
                           headers={'Accept':'application/json','Content-Type':'application/json','Authorization':f'Bearer {token}'},
                           json=required, timeout=15)
        r.raise_for_status()
        return r.json().get('verification_status') == 'SUCCESS'
    except requests.RequestException:
        logger.exception('PayPal webhook verification failed')
        return False


def _paypal_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z','+00:00')).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def process_paypal_event(event, webhook_row):
    event_type = event.get('event_type','')
    resource = event.get('resource') or {}
    event_id = event.get('id')
    pp_sub_id = resource.get('id') if event_type.startswith('BILLING.SUBSCRIPTION.') else (resource.get('billing_agreement_id') or resource.get('subscription_id'))
    sub = Subscription.query.filter_by(provider='paypal', provider_subscription_id=pp_sub_id).with_for_update().first() if pp_sub_id else None
    if not sub:
        webhook_row.status = 'ignored'
        webhook_row.processed_at = utcnow()
        db.session.commit()
        return
    previous = sub.status
    billing_info = resource.get('billing_info') or {}
    if event_type == 'BILLING.SUBSCRIPTION.ACTIVATED':
        sub.status = 'active'
        sub.current_period_start = _paypal_dt(resource.get('start_time')) or sub.current_period_start
        sub.current_period_end = _paypal_dt(billing_info.get('next_billing_time')) or sub.current_period_end
    elif event_type == 'BILLING.SUBSCRIPTION.UPDATED':
        sub.current_period_end = _paypal_dt(billing_info.get('next_billing_time')) or sub.current_period_end
        if resource.get('status') == 'ACTIVE': sub.status = 'active'
    elif event_type == 'BILLING.SUBSCRIPTION.PAYMENT.FAILED':
        sub.status = 'past_due'; sub.failed_payment_count = (sub.failed_payment_count or 0) + 1
        sub.grace_period_end = utcnow() + timedelta(days=int(os.environ.get('BILLING_GRACE_DAYS','3')))
    elif event_type == 'BILLING.SUBSCRIPTION.SUSPENDED':
        sub.status = 'unpaid'
    elif event_type == 'BILLING.SUBSCRIPTION.CANCELLED':
        sub.status = 'canceled'; sub.canceled_at = utcnow(); sub.cancel_at_period_end = False
    elif event_type == 'BILLING.SUBSCRIPTION.EXPIRED':
        sub.status = 'expired'
    elif event_type == 'PAYMENT.SALE.COMPLETED':
        amount_obj = resource.get('amount') or {}
        amount = Decimal(str(amount_obj.get('total','0')))
        tx_id = resource.get('id')
        existing = BillingTransaction.query.filter_by(provider='paypal', provider_transaction_id=tx_id).first() if tx_id else None
        if not existing and tx_id:
            tx = BillingTransaction(organization_id=sub.organization_id, subscription_id=sub.id, provider='paypal',
                                    provider_transaction_id=tx_id, provider_event_id=event_id, transaction_type='subscription_charge',
                                    status='completed', amount=amount, currency=amount_obj.get('currency','USD'),
                                    occurred_at=_paypal_dt(event.get('create_time')) or utcnow(), metadata_json={'event_type':event_type})
            db.session.add(tx)
        # Sale webhooks often do not contain next_billing_time; refresh the authoritative
        # subscription details so a successful renewal does not leave an expired period.
        provider_subscription = paypal_get_subscription_details(sub.provider_subscription_id)
        refreshed_billing = provider_subscription.get('billing_info') or {}
        sub.current_period_end = _paypal_dt(refreshed_billing.get('next_billing_time')) or sub.current_period_end
        if provider_subscription.get('status') == 'ACTIVE':
            sub.status='active'
        sub.failed_payment_count=0; sub.last_payment_at=utcnow(); sub.last_payment_provider_id=tx_id
        sub.grace_period_end=None
    elif event_type in ('PAYMENT.SALE.REFUNDED','PAYMENT.SALE.REVERSED'):
        amount_obj = resource.get('amount') or {}
        tx_id = resource.get('id')
        if tx_id and not BillingTransaction.query.filter_by(provider='paypal', provider_transaction_id=tx_id).first():
            db.session.add(BillingTransaction(organization_id=sub.organization_id, subscription_id=sub.id, provider='paypal',
                provider_transaction_id=tx_id, provider_event_id=event_id, transaction_type='refund' if 'REFUNDED' in event_type else 'reversal',
                status='refunded' if 'REFUNDED' in event_type else 'reversed', amount=Decimal(str(amount_obj.get('total','0'))),
                currency=amount_obj.get('currency','USD'), occurred_at=utcnow(), metadata_json={'event_type':event_type}))
        if event_type == 'PAYMENT.SALE.REVERSED':
            # A reversed/charged-back subscription payment removes entitlement until reviewed.
            sub.status = 'unpaid'
            sub.grace_period_end = None
    else:
        webhook_row.status = 'ignored'
    prior_subscription_event = SubscriptionEvent.query.filter_by(provider='paypal', provider_event_id=event_id).first()
    if prior_subscription_event is None:
        db.session.add(SubscriptionEvent(organization_id=sub.organization_id, subscription_id=sub.id, provider='paypal',
            event_type=event_type, provider_event_id=event_id, previous_status=previous, new_status=sub.status,
            metadata_json={'resource_id': resource.get('id')}))
    webhook_row.status='processed'; webhook_row.processed_at=utcnow()
    db.session.commit()


@app.route('/webhooks/paypal', methods=['POST'])
@csrf.exempt
@limiter.exempt
def paypal_webhook():
    payload = request.get_json(silent=True) or {}
    event_id = payload.get('id')
    event_type = payload.get('event_type','')
    if not event_id or not event_type:
        return {'error':'invalid webhook'}, 400
    if not paypal_verify_webhook(payload):
        return {'error':'invalid signature'}, 400
    existing = BillingWebhookEvent.query.filter_by(provider='paypal', provider_event_id=event_id).first()
    if existing and existing.status in {'processed', 'ignored'}:
        return {'status':'already_processed'}, 200
    try:
        if existing is None:
            existing = BillingWebhookEvent(provider='paypal', provider_event_id=event_id, event_type=event_type,
                resource_id=(payload.get('resource') or {}).get('id'), payload=payload)
            db.session.add(existing)
            db.session.flush()
        process_paypal_event(payload, existing)
        final_row = BillingWebhookEvent.query.filter_by(provider='paypal', provider_event_id=event_id).first()
        return {'status': final_row.status if final_row else 'processed'}, 200
    except IntegrityError:
        db.session.rollback()
        row = BillingWebhookEvent.query.filter_by(provider='paypal', provider_event_id=event_id).first()
        if row and row.status in {'processed', 'ignored'}:
            return {'status':'already_processed'}, 200
        logger.exception('PayPal webhook idempotency conflict')
        return {'error':'processing_failed'}, 500
    except Exception as exc:
        db.session.rollback()
        row = BillingWebhookEvent.query.filter_by(provider='paypal', provider_event_id=event_id).first()
        if row:
            row.status='failed'; row.error_message=str(exc)[:1000]; db.session.commit()
        logger.exception('PayPal webhook processing failed')
        return {'error':'processing_failed'}, 500


@app.route('/billing/paypal/return')
@login_required
def paypal_subscribe_return():
    flash('PayPal recibió tu solicitud. La activación definitiva se hará cuando llegue y se verifique el webhook de PayPal.', 'info')
    return redirect(url_for('dashboard'))


@app.route('/checkout', methods=['POST'])
@login_required
@limiter.limit('10 per hour')
def checkout():
    user = current_user()
    membership = Membership.query.filter_by(user_id=user.id, is_active=True).first()
    if not membership:
        abort(403)
    plan_data = PLANS_PPP.get(detect_country(), PLANS_PPP['GT'])
    plan = request.form.get('plan')
    method = request.form.get('method')
    reference = (request.form.get('reference') or '').strip()[:100]
    if plan not in ('pro', 'business', 'enterprise') or method not in ('wise', 'bank'):
        flash('Solicitud de pago no válida.', 'danger')
        return redirect(url_for('pricing'))
    pr = PaymentRequest(user_id=user.id, organization_id=membership.organization_id, plan=plan,
                        method=method, amount=Decimal(str(plan_data[plan])), currency=plan_data['currency'],
                        reference=reference or None)
    db.session.add(pr)
    audit('MANUAL_BILLING_REQUESTED', actor_user_id=user.id, organization_id=membership.organization_id,
          target_type='payment_request', target_id='new', metadata={'plan': plan, 'method': method})
    db.session.commit()
    flash('Solicitud enviada. El administrador verificará el pago. El billing automático se habilitará en la siguiente fase.', 'success')
    return redirect(url_for('pricing'))

# ---------------------------------------------------------------------------
# Admin — every state-changing action is POST + CSRF
# ---------------------------------------------------------------------------
@app.route('/admin')
@admin_required
def admin_dashboard():
    user = current_user()
    membership = current_membership(user)
    oid = membership.organization_id
    member_ids = db.session.query(Membership.user_id).filter_by(organization_id=oid, is_active=True).subquery()
    users = User.query.filter(User.id.in_(member_ids)).order_by(User.created_at.desc()).limit(300).all()
    payments = (PaymentRequest.query.filter_by(status='pending', organization_id=oid)
                .options(selectinload(PaymentRequest.user))
                .order_by(PaymentRequest.created_at.desc()).all())
    current = (Subscription.query.filter_by(organization_id=oid)
               .order_by(Subscription.created_at.desc()).first())
    active = int(bool(current and current.is_current))
    return render_template('admin.html', users=users, payments=payments, active=active,
                           total=len(users), inactive=len(users) - active, pay=PAYMENT_CONFIG)


@app.route('/admin/users/<int:id>/toggle', methods=['POST'])
@admin_required
def toggle_user(id):
    _, membership = require_permission('team.manage')
    user = (User.query.join(Membership, Membership.user_id == User.id)
            .filter(User.id == id, Membership.organization_id == membership.organization_id, Membership.is_active.is_(True))
            .first())
    if not user or user.id == session.get('user_id'):
        flash('Usuario no válido.', 'danger')
        return redirect(url_for('admin_dashboard'))
    user.is_active = not user.is_active
    audit('USER_ENABLED' if user.is_active else 'USER_DISABLED', actor_user_id=session.get('user_id'), target_type='user', target_id=id)
    db.session.commit()
    flash('Estado del usuario actualizado.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/plan/<int:id>', methods=['POST'])
@admin_required
def change_plan(id):
    _, membership = require_permission('team.manage')
    user = (User.query.join(Membership, Membership.user_id == User.id)
            .filter(User.id == id, Membership.organization_id == membership.organization_id, Membership.is_active.is_(True))
            .first())
    plan = request.form.get('plan', 'Gratis')
    if not user or plan not in PLAN_NAMES:
        flash('Plan no válido.', 'danger')
        return redirect(url_for('admin_dashboard'))
    oid = membership.organization_id
    current = Subscription.query.filter_by(organization_id=oid).order_by(Subscription.created_at.desc()).first()
    if not current:
        current = Subscription(user_id=user.id, organization_id=oid)
        db.session.add(current)
    current.plan = plan
    current.updated_at = utcnow()
    audit('PLAN_CHANGED', actor_user_id=session.get('user_id'), organization_id=oid, target_type='subscription', target_id=current.id if current.id else 'new', metadata={'plan': plan})
    db.session.commit()
    flash('Plan actualizado.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/payments/<int:id>/approve', methods=['POST'])
@admin_required
def approve_payment(id):
    _, membership = require_permission('team.manage')
    pr = PaymentRequest.query.filter_by(id=id, organization_id=membership.organization_id).first()
    if not pr or pr.status != 'pending':
        flash('Esta solicitud ya fue procesada o no existe.', 'warning')
        return redirect(url_for('admin_dashboard'))
    now = utcnow()
    current = (Subscription.query.filter_by(organization_id=pr.organization_id)
               .order_by(Subscription.created_at.desc()).first())
    if not current:
        current = Subscription(user_id=pr.user_id, organization_id=pr.organization_id)
        db.session.add(current)
    current.provider = 'manual'
    current.plan = pr.plan
    current.currency = pr.currency
    current.amount = pr.amount
    current.status = 'active'
    current.current_period_start = now
    current.current_period_end = now + timedelta(days=30)
    current.cancel_at_period_end = False
    pr.status = 'approved'
    # Subscription is authoritative. Legacy user flags are intentionally not used for access control.
    audit('MANUAL_SUBSCRIPTION_APPROVED', actor_user_id=session.get('user_id'), organization_id=pr.organization_id,
          target_type='subscription', target_id=current.id, metadata={'payment_request_id': id, 'period_end': current.current_period_end.isoformat()})
    db.session.commit()
    flash('Pago aprobado y suscripción activada por 30 días.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/payments/<int:id>/reject', methods=['POST'])
@admin_required
def reject_payment(id):
    _, membership = require_permission('team.manage')
    pr = PaymentRequest.query.filter_by(id=id, organization_id=membership.organization_id).first()
    if not pr or pr.status != 'pending':
        flash('Esta solicitud ya fue procesada o no existe.', 'warning')
        return redirect(url_for('admin_dashboard'))
    pr.status = 'rejected'
    audit('MANUAL_PAYMENT_REJECTED', actor_user_id=session.get('user_id'), organization_id=pr.organization_id,
          target_type='payment_request', target_id=id)
    db.session.commit()
    flash('Pago rechazado.', 'warning')
    return redirect(url_for('admin_dashboard'))


@app.route('/terminos')
def terms():
    return render_template('terms.html', now=utcnow())


@app.route('/privacidad')
def privacy():
    return render_template('privacy.html')


@app.route('/sw.js')
def sw():
    response = app.send_static_file('sw.js')
    response.headers['Cache-Control'] = 'no-store'
    return response

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
with app.app_context():
    # Phase 1 is intentionally not doing destructive migrations. On the first deployment,
    # use the migration command documented in README. create_all is kept for local bootstrap only.
    # create_all is permitted only for local SQLite development. Production schema is
    # managed by reviewed migrations; never let startup create a parallel schema.
    auto_create = os.environ.get('AUTO_CREATE_DB', 'true' if not IS_PRODUCTION else 'false').lower() == 'true'
    if auto_create and not IS_PRODUCTION and (not db_url or db_url.startswith('sqlite:')):
        db.create_all()
    elif auto_create and IS_PRODUCTION:
        raise RuntimeError('AUTO_CREATE_DB no puede estar habilitado en producción; aplique migraciones revisadas.')

    admin_user = os.environ.get('ADMIN_USERNAME')
    admin_pass = os.environ.get('ADMIN_PASSWORD')
    admin_email = os.environ.get('ADMIN_EMAIL')
    if admin_user and admin_pass and admin_email and not User.query.filter_by(username=admin_user).first():
        admin = User(username=admin_user, email=admin_email.lower(),
                     password=generate_password_hash(admin_pass, method='pbkdf2:sha256:600000'),
                     role='admin', is_active=True, email_verified=True,
                     is_active_subscription=True, plan='OWNER')
        db.session.add(admin)
        db.session.flush()
        org = Organization(name='ManagerW.S Administration', slug=f'admin-{secrets.token_hex(4)}', business_type='hybrid')
        db.session.add(org)
        db.session.flush()
        db.session.add(Membership(organization_id=org.id, user_id=admin.id, role='owner'))
        audit('ADMIN_CREATED', actor_user_id=admin.id, organization_id=org.id, target_type='user', target_id=admin.id)
        db.session.commit()
    elif IS_PRODUCTION and not (admin_user and admin_pass and admin_email):
        logger.warning('ADMIN_USERNAME/ADMIN_PASSWORD/ADMIN_EMAIL no están configurados; no se crea administrador automáticamente.')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
