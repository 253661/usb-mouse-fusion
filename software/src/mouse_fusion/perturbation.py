"""Integer reference for the MCU's bounded low-frequency cumulative offset.

The seed belongs to one START. Previewing with an explicit seed reproduces MCU
samples exactly; fresh preview seeds are local pseudo-random values. L1 clamping
guarantees that Euclidean deviation never exceeds ``max_deviation`` counts.
"""
import math
import secrets
import struct

CONFIG = struct.Struct('<BBBBHHHHI')
DEFAULT = dict(enabled=False, amplitude_x=3, amplitude_y=3, period_ms=250,
               smoothness=2, max_deviation=8, fixed_seed_enabled=False, seed=0)


def normalize_perturbation(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('perturbation must be an object')
    source = dict(value)
    if 'amplitude' in source and 'amplitude_x' not in source:
        amplitude = source.pop('amplitude')
        if type(amplitude) not in (int, float) or not math.isfinite(amplitude) or not 0 <= amplitude <= 1000:
            raise ValueError('legacy perturbation amplitude must be 0..1000')
        source['amplitude_x'] = source['amplitude_y'] = round(amplitude)
        source.setdefault('max_deviation', round(amplitude))
    if 'frequency_hz' in source and 'period_ms' not in source:
        frequency = source.pop('frequency_hz')
        if type(frequency) not in (int, float) or not math.isfinite(frequency) or frequency <= 0:
            raise ValueError('legacy perturbation frequency must be positive')
        source['period_ms'] = round(1000/frequency)
    if 'seed' in source and 'fixed_seed_enabled' not in source:
        source['fixed_seed_enabled'] = True
    if set(source) - set(DEFAULT):
        raise ValueError('unknown perturbation control')
    result = dict(DEFAULT, **source)
    for key in ('enabled', 'fixed_seed_enabled'):
        if type(result[key]) is not bool:
            raise ValueError(f'{key} must be boolean')
    for key, low, high in (('amplitude_x', 0, 1000), ('amplitude_y', 0, 1000),
                           ('period_ms', 40, 65535), ('smoothness', 0, 2),
                           ('max_deviation', 0, 1000), ('seed', 0, 0xffffffff)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f'{key} must be an integer {low}..{high}; legacy period outside this range cannot be exported to MCU')
    return result


normalize_config = normalize_perturbation


def encode_perturbation(config):
    c = normalize_perturbation(config)
    return CONFIG.pack(1, int(c['enabled']), int(c['fixed_seed_enabled']), c['smoothness'],
                       c['amplitude_x'], c['amplitude_y'], c['period_ms'], c['max_deviation'], c['seed'])


def decode_perturbation(data):
    if len(data) != CONFIG.size:
        raise ValueError('perturbation config must span 16 bytes')
    schema, enabled, fixed, smooth, x, y, period, maximum, seed = CONFIG.unpack(data)
    if schema != 1 or enabled > 1 or fixed > 1:
        raise ValueError('unsupported perturbation config')
    return normalize_perturbation(dict(enabled=bool(enabled), fixed_seed_enabled=bool(fixed),
        smoothness=smooth, amplitude_x=x, amplitude_y=y, period_ms=period, max_deviation=maximum, seed=seed))


def _hash32(x):
    x &= 0xffffffff
    x ^= x >> 16
    x = x * 0x7feb352d & 0xffffffff
    x ^= x >> 15
    x = x * 0x846ca68b & 0xffffffff
    return x ^ (x >> 16)


def _div(a, b):
    return -(abs(a)//b) if a < 0 else a//b


def _bound(x, y, limit):
    size = abs(x) + abs(y)
    return (_div(x*limit, size), _div(y*limit, size)) if size > limit else (x, y)


def _knot(c, seed, index):
    base = (seed + index*0x9e3779b9) & 0xffffffff
    x = _hash32(base ^ 0x68bc21eb) % (2*c['amplitude_x']+1) - c['amplitude_x']
    y = _hash32(base ^ 0x02e5be93) % (2*c['amplitude_y']+1) - c['amplitude_y']
    return _bound(x, y, c['max_deviation'])


def _eased(tick, total, shape):
    q = (tick << 16)//total
    if shape == 0:
        return q
    q2 = q*q >> 16
    q3 = q2*q >> 16
    if shape == 1:
        return 3*q2-2*q3
    q4 = q3*q >> 16
    q5 = q4*q >> 16
    return max(0, min(65536, 6*q5-15*q4+10*q3))


def _offset(c, seed, tick, total):
    if not c['enabled'] or not c['max_deviation'] or tick <= 0 or tick >= total:
        return (0, 0)
    ax, ay = _knot(c, seed, tick//c['period_ms'])
    bx, by = _knot(c, seed, tick//c['period_ms']+1)
    q = _eased(tick % c['period_ms'], c['period_ms'], c['smoothness'])
    x, y = ax*65536+(bx-ax)*q, ay*65536+(by-ay)*q
    ramp = min(total//2, c['period_ms'])
    edge = min(tick, total-tick)
    if edge < ramp:
        gate = _eased(edge, ramp, 2)
        x, y = _div(x*gate, 65536), _div(y*gate, 65536)
    def rounded(value):
        return -((-value+32768)//65536) if value < 0 else (value+32768)//65536
    return _bound(rounded(x), rounded(y), c['max_deviation'])


def perturb_offset(config, seed, tick, total):
    c = normalize_perturbation(config)
    if type(seed) is not int or not 0 <= seed <= 0xffffffff:
        raise ValueError('seed must be uint32')
    if type(tick) is not int or type(total) is not int or not 0 <= tick <= total or total < 1:
        raise ValueError('perturbation tick must be 0..positive duration')
    return _offset(c, seed, tick, total)


def perturb_motion(motion, config=None, seed=None):
    c = normalize_perturbation(config)
    values = list(motion)
    if not c['enabled']:
        return values
    if seed is None:
        seed = c['seed'] if c['fixed_seed_enabled'] else secrets.randbits(32)
    if type(seed) is not int or not 0 <= seed <= 0xffffffff:
        raise ValueError('seed must be uint32')
    last = (0, 0)
    output = []
    for tick, (x, y) in enumerate(values, 1):
        offset = _offset(c, seed, tick, len(values))
        delta = (x+offset[0]-last[0], y+offset[1]-last[1])
        if any(type(v) is not int or not -32768 <= v <= 32767 for v in delta):
            raise ValueError('perturbed sample exceeds signed16 motion range')
        output.append(delta)
        last = offset
    return output
