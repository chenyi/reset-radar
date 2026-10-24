import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('radar_costs', Path(__file__).resolve().parent.parent / 'scripts/costs.py')
costs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(costs)


class CostReportTest(unittest.TestCase):
    def test_uses_gross_when_coupon_reduces_payable_to_zero(self):
        self.assertEqual(costs.money([{'PretaxGrossAmount':'12.30','PretaxAmount':0,'DeductedByCoupons':'12.30'}]),
                         {'PretaxGrossAmount':12.3,'DeductedByCoupons':12.3,'CashAmount':0.0,'OutstandingAmount':0.0})

    def test_reads_every_page(self):
        with patch.object(costs, 'query', side_effect=[{'TotalCount':101,'Items':{'Item':[{}]*100}},
                                                     {'TotalCount':101,'Items':{'Item':[{}]}}]) as call:
            self.assertEqual(len(costs.rows('QueryBill')),101)
            self.assertEqual(call.call_args.kwargs['PageNum'],2)

    def test_missing_sls_bill_is_pending_and_estimate_has_no_hard_cap(self):
        from datetime import datetime, timezone
        def rows(action, **args):
            if action == 'QueryAccountBill':
                return [{'ProductCode':'ecs','PretaxGrossAmount':'6.7','DeductedByCoupons':'6.7'}]
            return []
        with patch.object(costs,'rows',side_effect=rows), patch.object(costs,'ledger',return_value={}), patch.object(costs,'query',return_value={}):
            result=costs.report('2026-10',datetime(2026,10,24,tzinfo=timezone.utc))
        self.assertFalse(result['sls']['bill_present'])
        self.assertFalse(result['sls']['coupon_verified'])
        self.assertAlmostEqual(result['forecast']['ecs_cny'],207.7)
        self.assertFalse(result['forecast']['hard_cap'])

    def test_snapshot_is_private_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bill.json'
            costs.save_snapshot(path,{'private':True})
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            with self.assertRaises(FileExistsError):costs.save_snapshot(path,{})
        with self.assertRaises(ValueError):costs.save_snapshot(Path(costs.__file__).parent/'bill.json',{})
