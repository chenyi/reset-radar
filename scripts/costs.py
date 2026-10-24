#!/usr/bin/env python3
"""Read-only private cost report. Run on ECS with its existing coupon-reader role."""
import argparse
import calendar
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from billing_guard import coupon_balance, query

MONEY = ['PretaxGrossAmount', 'DeductedByCoupons', 'CashAmount', 'OutstandingAmount']


def rows(action, **args):
    result, page = [], 1
    while True:
        data = query(action, PageNum=page, PageSize=100, **args)
        items = data.get('Items', {})
        items = items.get('Item', []) if isinstance(items, dict) else items
        result.extend(items)
        if len(result) >= int(data.get('TotalCount', len(result))):
            return result
        if not items:
            raise RuntimeError('Billing pagination ended before TotalCount')
        page += 1


def money(items):
    return {k: float(sum((Decimal(str(row.get(k) or 0)) for row in items), Decimal(0))) for k in MONEY}


def ledger(month):
    import psycopg
    with psycopg.connect(os.environ['DATABASE_URL'], options='-c default_transaction_read_only=on') as db:
        values = db.execute('''SELECT count(*), coalesce(sum(actual_cny),0),
          coalesce(sum(CASE WHEN actual_cny IS NULL THEN reserved_cny ELSE 0 END),0),
          coalesce(sum(coalesce(actual_cny,reserved_cny)),0)
          FROM ai_requests WHERE month=%s''', (month,)).fetchone()
    return dict(zip(['requests', 'estimated_actual_cny', 'unsettled_reserved_cny', 'occupied_cny'], values))


def report(month, current=None):
    current = current or datetime.now(timezone(timedelta(hours=8)))
    account = rows('QueryAccountBill', BillingCycle=month, IsGroupByProduct=True)
    ai = rows('QueryBill', BillingCycle=month, ProductCode='sfm', IsHideZeroCharge=False)
    ecs = rows('QueryInstanceBill', BillingCycle=month, ProductCode='ecs', IsBillingItem=True)
    sls = [r for r in account if r.get('ProductCode') in ('sls', 'slsingest', 'log') or '日志' in r.get('ProductName', '')]
    days = []
    for n in range(3, 0, -1):
        day = (current - timedelta(days=n)).date()
        if str(day)[:7] != month:
            continue
        daily = rows('QueryAccountBill', BillingCycle=month, Granularity='DAILY', BillingDate=str(day), IsGroupByProduct=True)
        daily_ecs = [r for r in daily if r.get('ProductCode') == 'ecs']
        if daily_ecs:
            days.append({'day': str(day), 'ecs_cny': money(daily_ecs)['PretaxGrossAmount']})
    forecast = None
    if days:
        per_day = max(d['ecs_cny'] for d in days)
        ecs_month = per_day * calendar.monthrange(*map(int, month.split('-')))[1]
        total = ecs_month + 50 + 5 + 10
        forecast = {'ecs_cny': round(ecs_month, 2), 'ai_allowance_cny': 50, 'sls_target_cny': 5,
                    'misc_allowance_cny': 10, 'conservative_total_cny': round(total, 2), 'margin_to_300_cny': round(300-total, 2),
                    'hard_cap': False}
    inference = [r for r in ai if r.get('CommodityCode') == 'sfm_inference_public_cn']
    sls_money = money(sls)
    return {'queried_at': current.isoformat(), 'month': month, 'currency': 'CNY',
            'billing_is_final': False, 'account': money(account),
            'products': [{'code': r.get('ProductCode'), **money([r])} for r in account],
            'ecs_items': [{'item': r.get('BillingItem') or r.get('BillingItemName'), **money([r])} for r in ecs],
            'bailian_official_inference': money(inference), 'application_ai_ledger': ledger(month),
            'coupon_available_cny': coupon_balance(query('QueryCashCoupons'), time.time()),
            'sls': {'bill_present': bool(sls), 'coupon_verified': bool(sls) and sls_money['DeductedByCoupons'] > 0
                    and sls_money['CashAmount'] == 0 and sls_money['OutstandingAmount'] == 0,
                    'verification': 'check actual deductions after first-day billing; absent bill does not mean free', **sls_money},
            'recent_complete_days': days, 'forecast': forecast}


def save_snapshot(path, value):
    path = Path(path).expanduser().resolve()
    root = Path(__file__).resolve().parent.parent
    if path == root or root in path.parents:
        raise ValueError('Private billing snapshots must be outside the repository')
    # Exclusive creation protects an existing report and never follows a symlink.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write('\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--month', default=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m'))
    parser.add_argument('--snapshot', help='New private JSON file outside the repository, mode 0600')
    args = parser.parse_args()
    datetime.strptime(args.month, '%Y-%m')
    result = report(args.month)
    if args.snapshot:
        save_snapshot(args.snapshot, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
