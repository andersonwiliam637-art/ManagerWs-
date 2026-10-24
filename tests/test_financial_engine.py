import unittest
from decimal import Decimal
from datetime import datetime
from financial import build_schedule, quantize_money, late_fee, allocate_payment, add_period

class FinancialEngineTests(unittest.TestCase):
    def test_zero_interest_schedule_reconciles(self):
        rows,total=build_schedule(1000,0,3,'monthly','simple',datetime(2026,10,1))
        self.assertEqual(total,Decimal('1000.00')); self.assertEqual(sum(r['amount_due'] for r in rows),Decimal('1000.00')); self.assertEqual(sum(r['principal_due'] for r in rows),Decimal('1000.00'))

    def test_flat_interest(self):
        rows,total=build_schedule(1000,12,12,'monthly','simple',datetime(2026,10,1))
        self.assertEqual(total,Decimal('1120.00')); self.assertEqual(sum(r['amount_due'] for r in rows),Decimal('1120.00')); self.assertEqual(sum(r['interest_due'] for r in rows),Decimal('120.00'))

    def test_amortized_schedule_reconciles(self):
        rows,total=build_schedule(1000,12,12,'monthly','amortized',datetime(2026,10,1))
        self.assertEqual(sum(r['amount_due'] for r in rows),total); self.assertEqual(sum(r['principal_due'] for r in rows),Decimal('1000.00')); self.assertTrue(all(r['amount_due']>0 for r in rows))

    def test_month_end_rolls_safely(self):
        self.assertEqual(add_period(datetime(2026,1,31),'monthly'),datetime(2026,2,28))
        self.assertEqual(add_period(datetime(2028,1,31),'monthly'),datetime(2028,2,29))

    def test_late_fee_modes_and_cap(self):
        self.assertEqual(late_fee(500,550,'fixed',10,5),Decimal('10.00'))
        self.assertEqual(late_fee(500,550,'percent_daily',1,2),Decimal('10.00'))
        self.assertEqual(late_fee(500,550,'percent_daily',1,20,15),Decimal('15.00'))
        self.assertEqual(late_fee(500,550,'none',100,20),Decimal('0.00'))


    def test_amortized_zero_rate_is_principal_only(self):
        rows,total=build_schedule(1234.56,0,7,'monthly','amortized',datetime(2026,10,1))
        self.assertEqual(total,Decimal('1234.56'))
        self.assertEqual(sum(r['principal_due'] for r in rows),Decimal('1234.56'))

    def test_partial_payment_never_overallocates_bucket(self):
        result=allocate_payment(Decimal('40'),{'late_fee':10,'interest':20,'principal':100})
        self.assertEqual(result['late_fee'],Decimal('10.00'))
        self.assertEqual(result['interest'],Decimal('20.00'))
        self.assertEqual(result['principal'],Decimal('10.00'))

    def test_monthly_schedule_preserves_valid_end_dates(self):
        rows,total=build_schedule(1000,0,3,'monthly','simple',datetime(2026,1,31))
        self.assertEqual([r['due_date'].date().isoformat() for r in rows],['2026-01-31','2026-02-28','2026-03-31'])

    def test_payment_allocation_order(self):
        result=allocate_payment(Decimal('125'),{'late_fee':25,'interest':50,'principal':200},('late_fee','interest','principal'))
        self.assertEqual(result['late_fee'],Decimal('25.00')); self.assertEqual(result['interest'],Decimal('50.00')); self.assertEqual(result['principal'],Decimal('50.00')); self.assertEqual(result['total'],Decimal('125.00'))

    def test_payment_allocation_rejects_overpayment(self):
        with self.assertRaises(ValueError): allocate_payment(101,{'late_fee':0,'interest':50,'principal':50})

if __name__=='__main__': unittest.main()
