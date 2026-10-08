"""Monthly domestic electricity tariff (rates exclude VAT)."""
from decimal import Decimal, ROUND_HALF_UP

TIER_LIMITS = (50, 100, 200, 300, 400)
DEFAULT_RATES = (1984, 2050, 2380, 2998, 3350, 3460)


def validate_tariff(tariff):
    if tariff['mode'] not in ('flat', 'tiered'):
        raise ValueError('Cách tính giá điện không hợp lệ.')
    vat = Decimal(str(tariff['vat_pct']))
    prices = [Decimal(str(value)) for value in tariff['rates']]
    flat = Decimal(str(tariff['flat_price']))
    if not vat.is_finite() or not 0 <= vat <= 100:
        raise ValueError('Thuế GTGT phải từ 0 đến 100%.')
    if len(prices) != 6 or any(not p.is_finite() or p < 0 for p in prices) or not flat.is_finite() or flat < 0:
        raise ValueError('Đơn giá điện phải là số không âm và có đủ 6 bậc.')


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
        for limit, rate in zip((*TIER_LIMITS, None), tariff['rates']):
            upper = Decimal(limit) if limit is not None else usage
            units = max(Decimal(0), min(usage, upper) - lower)
            subtotal += units * Decimal(str(rate))
            lower = upper
    subtotal = subtotal.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    tax = (subtotal * Decimal(str(tariff['vat_pct'])) / 100).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP)
    return {'subtotal': int(subtotal), 'tax': int(tax), 'total': int(subtotal + tax)}
