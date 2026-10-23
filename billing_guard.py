import json
import os
import time
from datetime import datetime, timezone

from storage import get_state, now, set_state


class FundingUnavailable(RuntimeError):
    pass


def query(action, **queries):
    from alibabacloud_credentials.client import Client as Credentials
    from alibabacloud_credentials.models import Config as CredentialsConfig
    from alibabacloud_tea_openapi.client import Client
    from alibabacloud_tea_openapi.models import Config, Params, OpenApiRequest
    from alibabacloud_tea_util.models import RuntimeOptions

    credential = Credentials(CredentialsConfig(type='ecs_ram_role', role_name='reset-radar-coupon-reader'))
    client = Client(Config(credential=credential, endpoint='business.aliyuncs.com'))
    params = Params(action=action, version='2017-12-14', protocol='HTTPS', method='POST',
                    auth_type='AK', style='RPC', pathname='/', req_body_type='formData', body_type='json')
    try:
        payload = client.call_api(params, OpenApiRequest(query=queries),
                                  RuntimeOptions(connect_timeout=5000, read_timeout=20000, autoretry=False))['body']
    except Exception as exc:
        raise FundingUnavailable('代金券查询暂不可用') from exc
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
    bill = query('QueryBill', BillingCycle=month, ProductCode='sfm', PageSize=100, IsHideZeroCharge=False)
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
