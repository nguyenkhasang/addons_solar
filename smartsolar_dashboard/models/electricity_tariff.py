"""Monthly domestic electricity tariff (rates exclude VAT)."""
from decimal import Decimal, ROUND_HALF_UP

TIER_LIMITS = (100, 200, 400, 700)
DEFAULT_RATES = (1901, 2289, 2894, 3456, 3844)
# Closed monthly ledger snapshots still contain the previous six-tier rates.
LEGACY_TIER_LIMITS = (50, 100, 200, 300, 400)


def validate_tariff(tariff):
    if tariff['mode'] not in ('flat', 'tiered'):
        raise ValueError('Cách tính giá điện không hợp lệ.')
    vat = Decimal(str(tariff['vat_pct']))
    prices = [Decimal(str(value)) for value in tariff['rates']]
    flat = Decimal(str(tariff['flat_price']))
    if not vat.is_finite() or not 0 <= vat <= 100:
        raise ValueError('Thuế GTGT phải từ 0 đến 100%.')
    if len(prices) not in (5, 6) or any(not p.is_finite() or p < 0 for p in prices) or not flat.is_finite() or flat < 0:
        raise ValueError('Đơn giá điện phải là số không âm và có đủ 5 bậc (hoặc 6 bậc cho biểu giá lịch sử).')


def electricity_bill(kwh, tariff):
    """One meter, one calendar month; zero usage is a valid zero bill."""
    if kwh is None:
        return None
    validate_tariff(tariff)
    usage = Decimal(str(kwh))
    if not usage.is_finite() or usage < 0:
        raise ValueError('Điện năng phải là số không âm.')
    subtotal = Decimal(0)
    if tariff['mode'] == 'flat':
        subtotal = usage * Decimal(str(tariff['flat_price']))
    else:
        lower = Decimal(0)
        limits = LEGACY_TIER_LIMITS if len(tariff['rates']) == 6 else TIER_LIMITS
        for limit, rate in zip((*limits, None), tariff['rates']):
            upper = Decimal(limit) if limit is not None else usage
            units = max(Decimal(0), min(usage, upper) - lower)
            subtotal += units * Decimal(str(rate))
            lower = upper
    subtotal = subtotal.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    tax = (subtotal * Decimal(str(tariff['vat_pct'])) / 100).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP)
    return {'subtotal': int(subtotal), 'tax': int(tax), 'total': int(subtotal + tax)}
