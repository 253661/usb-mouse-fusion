"""API v1 and capability-gated J4 auxiliary extension revisions 1 and 2."""
import struct

from .errors import FusionError

API_VERSION = 1
MAX_LINE = 4096
CONTROL_QUEUE = 16
MAX_CLIENTS = 16
TTL_DEFAULT_MS = 20
TTL_MAX_MS = 100
CAP_MOVE, CAP_MODE, CAP_CONFIG, CAP_CLOCK = 0x10, 0x20, 0x40, 0x80
MOVE, CLOCK, GET_CONFIG, SET_CONFIG, SET_MODE, AUX_STATUS = range(0x10, 0x16)
MOVE_BODY = struct.Struct('<IhhI')
CONFIG_BODY = struct.Struct('<BHH')
AUX_BODY = struct.Struct('<BIIIhh')
DEVICE_ERRORS = {1: 'PROTOCOL_ERROR', 2: 'INVALID_ARGUMENT', 3: 'DEVICE_DISCONNECTED',
                 4: 'PROTOCOL_ERROR', 5: 'EXPIRED', 6: 'MODE_DISABLED',
                 7: 'STALE_SEQUENCE', 8: 'INVALID_ARGUMENT', 9: 'DEVICE_UNAVAILABLE',
                 10: 'CYCLE_CLOSED', 11: 'NOT_SUPPORTED', 12: 'BUSY', 13: 'BAD_CRC',
                 14: 'UNSAFE', 15: 'STORAGE_ERROR',16: 'STALE_PRESET',
                 17: 'BYPASS_ACTIVE',18: 'STALE_AUXILIARY',19: 'MODE_DISABLED',20:'LEFT_GATE_CLOSED'}


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise FusionError('INVALID_ARGUMENT', f'{name} must be an integer in {low}..{high}')
    return value


def config_values(config, current):
    if not isinstance(config, dict) or not config or set(config) - {'default_ttl_ms', 'max_delta'}:
        raise FusionError('INVALID_ARGUMENT', 'config accepts default_ttl_ms and max_delta only')
    ttl = integer(config.get('default_ttl_ms', current['default_ttl_ms']), 'default_ttl_ms', 1, TTL_MAX_MS)
    maximum = integer(config.get('max_delta', current['max_delta']), 'max_delta', 1, 32767)
    return ttl, maximum


def parse_info(body):
    if len(body) != 37:
        raise FusionError('PROTOCOL_ERROR', 'Wrong INFO size')
    version, firmware, board, caps, baud, idle, maximum = struct.unpack('<B16s8sIIHH', body)
    try:
        name = firmware.rstrip(b'\0').decode('ascii')
    except UnicodeError as error:
        raise FusionError('PROTOCOL_ERROR', 'Non-ASCII firmware name') from error
    return {'protocol': version, 'firmware': name, 'board_id': board.hex().upper(),
            'capabilities': caps, 'baud': baud, 'default_idle_ms': idle, 'max_payload': maximum}


def parse_status(body):
    if len(body) != 36:
        raise FusionError('PROTOCOL_ERROR', 'Wrong STATUS size')
    values = struct.unpack('<IBBHIIIIIIHH', body)
    keys = ('flags', 'host_phase', 'host_error', 'buttons', 'rx_frames', 'crc_errors',
            'format_errors', 'transport_errors', 'tx_busy_drops', 'session',
            'idle_remaining_ms', 'identify_remaining_ms')
    return dict(zip(keys, values))

