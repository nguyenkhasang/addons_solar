"""Shared, explicit summary semantics; no Odoo dependency."""
from datetime import timedelta
import math

VERSION = 4
MAX_GAP_SECONDS = 300
METRICS = {
    'grid_tie_inverter': {
        'output_power': 'output_power', 'grid_import_power': 'limiter_power',
        'total_load_power': None, 'dc_voltage': 'dc_voltage',
        'ac_voltage': 'ac_voltage', 'inverter_temp': 'temperature'},
    'charge_power': {
        'pv_input': 'charge_power', 'pv_voltage': 'pv_voltage',
        'pv_current': 'pv_current', 'bat_voltage': 'bat_voltage',
        'bat_current': 'bat_current', 'charger_temp': 'temperature'},
}
POWER_KEYS = {'output_power', 'grid_import_power', 'total_load_power', 'pv_input'}
COUNTERS = {
    'grid_tie_inverter': {'energy_total': 'grid_import_power', 'limiter_total': 'output_power'},
    'charge_power': {'total_kwh': 'pv_input'},
}

def finite(value):
    try:
        return value is not None and not isinstance(value, bool) and math.isfinite(float(value))
    except (ValueError, TypeError):
        return False

def counter_is_valid(row, field):
    value = row.get(field)
    declared = (row.get('counter_validity') or {}).get(field)
    return finite(value) and value >= 0 and declared is not False and (value != 0 or declared is True)

def metric_value(row, field):
    if field is None:
        parts = [row.get('output_power'), row.get('limiter_power')]
        return sum(parts) if all(finite(value) for value in parts) else None
    value = row.get(field)
    return float(value) if finite(value) else None

def calculate(rows, table, start, end):
    """Single-device bucket, including adjacent rows/seeds outside the bucket."""
    # A repeated timestamp is one observation; choose the latest received row.
    unique = {row['record_date']: row for row in sorted(rows, key=lambda r: r['id'])}
    rows = [unique[at] for at in sorted(unique)]
    inside = [row for row in rows if start <= row['record_date'] < end]
    duration = (end - start).total_seconds()
    extrema, power, quality, counters, statistics = {}, {}, {}, {}, {}
    for key, field in METRICS[table].items():
        points = [(row['record_date'], metric_value(row, field)) for row in rows]
        valid = [(at, value) for at, value in points if start <= at < end and value is not None]
        if valid:
            statistics[key] = {'count': len(valid), 'sum': sum(value for at, value in valid),
                               'min': min(value for at, value in valid), 'max': max(value for at, value in valid),
                               'first': valid[0][1], 'last': valid[-1][1],
                               'first_at': valid[0][0].isoformat(), 'last_at': valid[-1][0].isoformat()}
            peak = min(valid, key=lambda item: (-item[1], item[0]))
            low = min(valid, key=lambda item: (item[1], item[0]))
            extrema[key] = {'max': {'value': peak[1], 'observed_at': peak[0].isoformat()},
                            'min': {'value': low[1], 'observed_at': low[0].isoformat()}}
        if key not in POWER_KEYS:
            continue
        energy, seconds = 0.0, 0.0
        for (at0, value0), (at1, value1) in zip(points, points[1:]):
            span = (at1 - at0).total_seconds()
            if value0 is None or value1 is None or not 0 < span <= MAX_GAP_SECONDS:
                continue
            left, right = max(at0, start), min(at1, end)
            covered = (right - left).total_seconds()
            if covered <= 0:
                continue
            p0 = value0 + (value1 - value0) * (left - at0).total_seconds() / span
            p1 = value0 + (value1 - value0) * (right - at0).total_seconds() / span
            energy += (p0 + p1) / 2 * covered / 3600000
            seconds += covered
        power[key] = {'value': energy if seconds else None, 'covered_seconds': seconds,
                      'duration_seconds': duration, 'coverage_pct': seconds / duration * 100,
                      'estimated': True, 'method': 'trapezoidal_power',
                      'max_gap_seconds': MAX_GAP_SECONDS}
    for field, power_key in COUNTERS[table].items():
        valid = [row for row in rows if counter_is_valid(row, field)]
        readings = [row[field] for row in inside if counter_is_valid(row, field)]
        delta, resets = 0.0, 0
        for before, after in zip(valid, valid[1:]):
            if not start <= after['record_date'] < end:
                continue
            step = after[field] - before[field]
            if step < 0:
                resets += 1
                step = after[field]
            delta += step
        invalid = len(inside) - len(readings)
        constant = bool(readings) and min(readings) == max(readings)
        nonzero_power = any(abs(metric_value(row, METRICS[table][power_key]) or 0) > 1 for row in inside)
        quality[field] = {'valid_samples': len(readings), 'invalid_samples': invalid,
                          'ambiguous_zero_samples': sum(row.get(field) == 0 and
                              (row.get('counter_validity') or {}).get(field) is not True for row in inside),
                          'min': min(readings) if readings else None,
                          'max': max(readings) if readings else None,
                          'constant': constant, 'reset_count': resets,
                          'reset_interpretation': 'assumed_on_decrease' if resets else None,
                          'stalled_with_power': constant and nonzero_power,
                          'reliable': bool(readings) and not invalid and not (constant and nonzero_power)}
        counters[field] = {'energy': delta, 'first': readings[0] if readings else None,
                           'last': readings[-1] if readings else None, 'resets': resets}
    return {'power_energy': power, 'metric_extrema': extrema, 'metric_statistics': statistics,
            'counter_quality': quality, 'counters': counters}
