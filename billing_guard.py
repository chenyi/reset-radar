import json
import os
import subprocess
import time
from datetime import datetime, timezone

from storage import get_state, now, set_state


class FundingUnavailable(RuntimeError):
    pass


def query(action, *args):
    command = [os.environ.get('ALIYUN_CLI', '/opt/reset-radar-tools/aliyun/aliyun'),
               '--config-path', '/etc/reset-radar/billing-cli.json', '--profile', 'reset-radar-billing',
               'bssopenapi', action, *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=35)
    if result.returncode:
        raise FundingUnavailable('代金券查询暂不可用')
    payload = json.loads(result.stdout)
    if payload.get('Success') is not True:
        raise FundingUnavailable('代金券查询未成功')
    return payload.get('Data', {})


def coupon_balance(data, current):
    total = 0.0
    for coupon in data.get('CashCoupon', []):
        expiry = datetime.fromisoformat(coupon['ExpiryTime'].replace('Z', '+00:00')).timestamp()
        effective = datetime.fromisoformat(coupon['EffectiveTime'].replace('Z', '+00:00')).timestamp()
        if (coupon.get('Status') == 'Available' and effective <= current < expiry
                and coupon.get('ApplicableProducts', '').startswith('通用')
                and '按量付费账单' in coupon.get('ApplicableScenarios', '')):
            total += float(coupon['Balance'])
    return total


def verify(db):
    stamp = get_state(db, 'coupon_check', {})
    if time.time() - stamp.get('checked_at', 0) < 60:
        return
    coupons = query('QueryCashCoupons')
    balance = coupon_balance(coupons, time.time())
    # Keep a buffer for ECS, OSS and the billing system's delayed reports.
    if balance < 100:
        raise FundingUnavailable('可用代金券不足安全余量，AI 已停止')
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    bill = query('QueryBill', '--BillingCycle', month, '--ProductCode', 'sfm', '--PageSize', '100')
    items = bill.get('Items', {})
    items = items.get('Item', []) if isinstance(items, dict) else items
    inference = [x for x in items if x.get('CommodityCode') == 'sfm_inference_public_cn']
    if any(float(x.get('CashAmount', 0)) > 0 or float(x.get('OutstandingAmount', 0)) > 0 for x in inference):
        raise FundingUnavailable('模型账单出现非券金额，AI 已停止')
    proof = get_state(db, 'coupon_product_verified', False)
    if any(float(x.get('DeductedByCoupons', 0)) > 0 for x in inference):
        proof = True
    if not proof:
        raise FundingUnavailable('等待模型代金券抵扣凭证')
    with db:
        set_state(db, 'coupon_product_verified', True)
        set_state(db, 'coupon_check', {'checked_at': time.time(), 'balance': balance, 'at': now()})
