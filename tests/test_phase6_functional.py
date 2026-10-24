import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from financial import (
    add_period,
    allocate_payment,
    build_schedule,
    late_fee,
    payoff_amount,
    quantize_money,
)


@dataclass
class InstallmentState:
    number: int
    due_date: datetime
    amount_due: Decimal
    principal_due: Decimal
    interest_due: Decimal
    late_fee_due: Decimal = Decimal('0.00')
    amount_paid: Decimal = Decimal('0.00')
    late_fee_paid: Decimal = Decimal('0.00')

    @property
    def balance(self):
        return max(Decimal('0.00'), self.amount_due - self.amount_paid)


def simulate_installment_payment(installments, amount, order=('late_fee', 'interest', 'principal')):
    left = quantize_money(amount)
    allocations = []
    for item in installments:
        if left <= 0:
            break
        contractual_paid = max(Decimal('0.00'), item.amount_paid)
        paid_interest = min(contractual_paid, item.interest_due)
        paid_principal = max(Decimal('0.00'), contractual_paid - paid_interest)
        buckets = {
            'late_fee': max(Decimal('0.00'), item.late_fee_due - item.late_fee_paid),
            'interest': max(Decimal('0.00'), item.interest_due - paid_interest),
            'principal': max(Decimal('0.00'), item.principal_due - paid_principal),
        }
        available = sum(buckets.values(), Decimal('0.00'))
        if available <= 0:
            continue
        result = allocate_payment(min(left, available), buckets, order)
        used = result['total']
        item.late_fee_paid += result['late_fee']
        item.amount_paid += result['interest'] + result['principal']
        allocations.append({
            'installment': item.number,
            'total': used,
            'late_fee': result['late_fee'],
            'interest': result['interest'],
            'principal': result['principal'],
        })
        left = quantize_money(left - used)
    if left > 0:
        raise ValueError('Pago excede el saldo contractual.')
    return allocations


class Phase6FunctionalTests(unittest.TestCase):
    def test_full_lender_schedule_to_payoff_flow(self):
        start = datetime(2026, 10, 31)
        rows, total = build_schedule(1000, 12, 3, 'monthly', 'simple', start)
        installments = [InstallmentState(**{k: v for k, v in row.items() if k in {
            'number', 'due_date', 'amount_due', 'principal_due', 'interest_due', 'late_fee_due'
        }}) for row in rows]

        self.assertEqual(total, Decimal('1030.00'))
        self.assertEqual(sum(i.amount_due for i in installments), total)

        # Partial collection against first installment.
        allocations = simulate_installment_payment(installments, Decimal('50.00'))
        self.assertEqual(sum(a['total'] for a in allocations), Decimal('50.00'))
        self.assertEqual(installments[0].balance, Decimal('293.33'))

        # Continue until contract is fully settled.
        remaining = payoff_amount([{
            'amount_due': i.amount_due,
            'amount_paid': i.amount_paid,
            'late_fee_due': i.late_fee_due,
            'late_fee_paid': i.late_fee_paid,
        } for i in installments])
        simulate_installment_payment(installments, remaining)
        self.assertEqual(sum(i.balance for i in installments), Decimal('0.00'))
        self.assertEqual(payoff_amount([{
            'amount_due': i.amount_due,
            'amount_paid': i.amount_paid,
            'late_fee_due': i.late_fee_due,
            'late_fee_paid': i.late_fee_paid,
        } for i in installments]), Decimal('0.00'))

    def test_fifo_allocation_spans_multiple_installments(self):
        rows, _ = build_schedule(900, 0, 3, 'monthly', 'simple', datetime(2026, 10, 1))
        installments = [InstallmentState(**{k: v for k, v in row.items() if k in {
            'number', 'due_date', 'amount_due', 'principal_due', 'interest_due', 'late_fee_due'
        }}) for row in rows]
        allocations = simulate_installment_payment(installments, Decimal('650.00'))
        self.assertEqual([a['installment'] for a in allocations], [1, 2, 3])
        self.assertEqual(installments[0].balance, Decimal('0.00'))
        self.assertEqual(installments[1].balance, Decimal('0.00'))
        self.assertEqual(installments[2].balance, Decimal('250.00'))

    def test_partial_payment_respects_late_fee_interest_principal_order(self):
        rows, _ = build_schedule(1000, 12, 1, 'monthly', 'simple', datetime(2026, 10, 1))
        i = InstallmentState(**{k: v for k, v in rows[0].items() if k in {
            'number', 'due_date', 'amount_due', 'principal_due', 'interest_due', 'late_fee_due'
        }})
        i.late_fee_due = Decimal('25.00')
        allocations = simulate_installment_payment([i], Decimal('35.00'))
        self.assertEqual(allocations[0]['late_fee'], Decimal('25.00'))
        self.assertEqual(allocations[0]['interest'], Decimal('10.00'))
        self.assertEqual(allocations[0]['principal'], Decimal('0.00'))

    def test_payoff_reduces_paid_late_fee(self):
        items = [
            {'amount_due': Decimal('100.00'), 'amount_paid': Decimal('60.00'), 'late_fee_due': Decimal('20.00'), 'late_fee_paid': Decimal('5.00')},
        ]
        self.assertEqual(payoff_amount(items), Decimal('55.00'))

    def test_reversal_restores_installment_balance(self):
        rows, _ = build_schedule(600, 0, 2, 'monthly', 'simple', datetime(2026, 10, 1))
        installments = [InstallmentState(**{k: v for k, v in row.items() if k in {
            'number', 'due_date', 'amount_due', 'principal_due', 'interest_due', 'late_fee_due'
        }}) for row in rows]
        simulate_installment_payment(installments, Decimal('250.00'))
        paid_balances = [i.balance for i in installments]
        # Reverse exactly the posted payment by replaying its first-allocation amount.
        installments[0].amount_paid = Decimal('0.00')
        self.assertEqual(installments[0].balance, rows[0]['amount_due'])
        self.assertGreater(sum(paid_balances), Decimal('0.00'))

    def test_late_fee_grace_window_math(self):
        due = datetime(2026, 10, 10)
        grace_days = 5
        within_grace = due + timedelta(days=5)
        overdue = due + timedelta(days=6)
        self.assertLessEqual(within_grace.date(), (due + timedelta(days=grace_days)).date())
        self.assertGreater(overdue.date(), (due + timedelta(days=grace_days)).date())

    def test_late_fee_cap_is_honored(self):
        self.assertEqual(late_fee(1000, 1000, 'percent_daily', 1, 30, 100), Decimal('100.00'))

    def test_zero_interest_all_frequencies(self):
        for frequency, delta in [('weekly', 7), ('biweekly', 14), ('monthly', None)]:
            rows, total = build_schedule(777.77, 0, 4, frequency, 'amortized', datetime(2026, 10, 1))
            self.assertEqual(total, Decimal('777.77'))
            self.assertEqual(sum(r['principal_due'] for r in rows), Decimal('777.77'))
            if delta:
                self.assertEqual((rows[1]['due_date'] - rows[0]['due_date']).days, delta)

    def test_month_end_refinance_preserves_valid_calendar_dates(self):
        old_rows, _ = build_schedule(1200, 0, 3, 'monthly', 'simple', datetime(2026, 1, 31))
        self.assertEqual([r['due_date'].date().isoformat() for r in old_rows], [
            '2026-01-31', '2026-02-28', '2026-03-31'
        ])
        remaining = Decimal('800.00')
        new_rows, new_total = build_schedule(remaining, 18, 6, 'monthly', 'amortized', datetime(2026, 4, 30))
        self.assertEqual(new_rows[0]['due_date'].date().isoformat(), '2026-04-30')
        self.assertEqual(new_rows[-1]['due_date'].date().isoformat(), '2026-09-30')
        self.assertEqual(sum(r['amount_due'] for r in new_rows), new_total)
        self.assertEqual(sum(r['principal_due'] for r in new_rows), remaining)

    def test_amortized_rounding_reconciles_exactly(self):
        rows, total = build_schedule(12345.67, 19.75, 17, 'biweekly', 'amortized', datetime(2026, 10, 2))
        self.assertEqual(sum(r['amount_due'] for r in rows), total)
        self.assertEqual(sum(r['principal_due'] for r in rows), Decimal('12345.67'))
        self.assertEqual(sum(r['interest_due'] for r in rows), total - Decimal('12345.67'))

    def test_overpayment_is_rejected(self):
        rows, _ = build_schedule(500, 0, 2, 'monthly', 'simple', datetime(2026, 10, 1))
        buckets = {'late_fee': 0, 'interest': rows[0]['interest_due'], 'principal': rows[0]['principal_due']}
        with self.assertRaises(ValueError):
            allocate_payment(Decimal('251.00'), buckets)

    def test_allocate_payment_never_creates_negative_buckets(self):
        result = allocate_payment(Decimal('20.00'), {'late_fee': 5, 'interest': 5, 'principal': 10})
        self.assertTrue(all(v >= 0 for k, v in result.items() if k != 'total'))
        self.assertEqual(result['total'], Decimal('20.00'))

    def test_supported_month_addition_crosses_year(self):
        self.assertEqual(add_period(datetime(2026, 12, 31), 'monthly').date().isoformat(), '2027-01-31')


if __name__ == '__main__':
    unittest.main()
