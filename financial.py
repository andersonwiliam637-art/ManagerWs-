"""Deterministic financial primitives for the lender engine.
No Flask/database imports; all monetary arithmetic uses Decimal and cents.
"""
from calendar import monthrange
from datetime import timedelta, date, datetime
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal('0.01')
FREQUENCY_PERIODS = {'weekly': Decimal('52'), 'biweekly': Decimal('26'), 'monthly': Decimal('12')}
FREQUENCY_DAYS = {'weekly': 7, 'biweekly': 14, 'monthly': 30}
SUPPORTED_METHODS = {'simple', 'amortized'}


def quantize_money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def add_period(dt, frequency, number=1):
    if frequency == 'weekly': return dt + timedelta(days=7 * number)
    if frequency == 'biweekly': return dt + timedelta(days=14 * number)
    if frequency == 'monthly':
        y = dt.year + (dt.month - 1 + number) // 12
        m = (dt.month - 1 + number) % 12 + 1
        return dt.replace(year=y, month=m, day=min(dt.day, monthrange(y, m)[1]))
    raise ValueError('Frecuencia no soportada.')


def build_schedule(principal, annual_rate_percent, term_count, frequency, method, first_due_date):
    principal=quantize_money(principal); rate=Decimal(str(annual_rate_percent or 0))/Decimal('100')
    if principal<=0 or int(term_count)<=0: raise ValueError('Términos financieros inválidos.')
    term_count=int(term_count)
    if frequency not in FREQUENCY_PERIODS or method not in SUPPORTED_METHODS: raise ValueError('Parámetros financieros inválidos.')
    ppy=FREQUENCY_PERIODS[frequency]; periodic=rate/ppy
    rows=[]; remaining=principal; total_interest=Decimal('0.00')
    if method=='amortized' and periodic>0:
        payment=quantize_money(principal*(periodic*(1+periodic)**term_count)/((1+periodic)**term_count-1))
    else:
        total_interest=Decimal('0.00')
        flat_interest=quantize_money(principal*rate*(Decimal(term_count)/ppy)) if method=='simple' else Decimal('0.00')
        payment=quantize_money((principal+flat_interest)/Decimal(term_count))
    for n in range(1,term_count+1):
        interest=quantize_money(remaining*periodic) if method=='amortized' else quantize_money(principal*rate/ppy)
        principal_due=quantize_money(payment-interest)
        if n==term_count:
            principal_due=remaining
            amount_due=quantize_money(principal_due+interest)
        else: amount_due=payment
        remaining=quantize_money(remaining-principal_due); total_interest+=interest
        rows.append({'number':n,'due_date':add_period(first_due_date,frequency,n-1),'principal_due':principal_due,'interest_due':interest,'late_fee_due':Decimal('0.00'),'amount_due':amount_due})
    expected=quantize_money(principal+total_interest); actual=sum((r['amount_due'] for r in rows),Decimal('0.00'))
    delta=quantize_money(expected-actual); rows[-1]['amount_due']=quantize_money(rows[-1]['amount_due']+delta); rows[-1]['principal_due']=quantize_money(rows[-1]['amount_due']-rows[-1]['interest_due'])
    return rows, expected


def late_fee(balance, amount_due, fee_type, fee_value, days_late, cap=0):
    balance=quantize_money(balance); amount_due=quantize_money(amount_due); value=Decimal(str(fee_value or 0)); days=max(0,int(days_late))
    if days<=0 or value<=0 or fee_type=='none': return Decimal('0.00')
    if fee_type=='fixed': fee=value
    elif fee_type=='percent_daily': fee=quantize_money(balance*value/Decimal('100')*Decimal(days))
    elif fee_type=='percent_installment': fee=quantize_money(amount_due*value/Decimal('100'))
    else: raise ValueError('Tipo de mora no soportado.')
    cap=quantize_money(cap or 0)
    return min(fee,cap) if cap>0 else fee


def allocate_payment(amount, buckets, order=('late_fee','interest','principal')):
    """Allocate a payment to ordered buckets; raises if it cannot be fully allocated."""
    left=quantize_money(amount); out={k:Decimal('0.00') for k in ('late_fee','interest','principal')}
    for key in order:
        due=max(Decimal('0.00'),quantize_money(buckets.get(key,0))-out.get(key,Decimal('0.00')))
        take=min(left,due); out[key]=quantize_money(out.get(key,Decimal('0.00'))+take); left=quantize_money(left-take)
    if left>0: raise ValueError('El pago excede los conceptos pendientes.')
    out['total']=quantize_money(amount-left)
    return out


def payoff_amount(installments, as_of=None):
    """Return remaining contractual balance plus unpaid accrued late fees."""
    total = Decimal('0.00')
    for item in installments:
        contractual = quantize_money(item.get('amount_due', 0)) - quantize_money(item.get('amount_paid', 0))
        late = quantize_money(item.get('late_fee_due', 0)) - quantize_money(item.get('late_fee_paid', 0))
        total += max(Decimal('0.00'), contractual) + max(Decimal('0.00'), late)
    return quantize_money(total)
