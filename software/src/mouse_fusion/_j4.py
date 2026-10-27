"""Minimal J4 v1 tester. Default is offline; live COM access requires explicit authorization."""
import argparse
import binascii
import ctypes
from dataclasses import dataclass
import json
from pathlib import Path
import re
import secrets
import struct
import subprocess
import sys
import time

VERSION, BAUD, PAYLOAD_MAX, WIRE_MAX = 1, 115200, 48, 62
INFO, HELLO, PING, STATUS, IDENTIFY, CLOSE, SET_IDLE = range(1, 8)
NO_SESSION = 3
HEADER = struct.Struct('<BBHIH')

@dataclass(frozen=True)
class Frame:
    opcode: int
    sequence: int
    session: int
    payload: bytes = b''

def encode(frame):
    if not 0 <= frame.opcode <= 255 or not 1 <= frame.sequence <= 65535 or not 0 <= frame.session <= 0xffffffff or len(frame.payload) > PAYLOAD_MAX:
        raise ValueError('Invalid frame fields')
    raw = HEADER.pack(VERSION, frame.opcode, frame.sequence, frame.session, len(frame.payload)) + frame.payload
    raw += struct.pack('<H', binascii.crc_hqx(raw, 0xffff))
    output = bytearray(b'\0')
    mark, code = 0, 1
    for byte in raw:
        if byte == 0:
            output[mark] = code
            mark = len(output)
            output.append(0)
            code = 1
        else:
            output.append(byte)
            code += 1
    output[mark] = code
    output.append(0)
    return bytes(output)

def decode(encoded):
    if not 13 <= len(encoded) < WIRE_MAX:
        raise ValueError('Invalid encoded length')
    raw, pos = bytearray(), 0
    while pos < len(encoded):
        code = encoded[pos]
        pos += 1
        if code == 0 or pos + code - 1 > len(encoded):
            raise ValueError('Invalid COBS block')
        block = encoded[pos:pos + code - 1]
        if 0 in block:
            raise ValueError('Embedded COBS delimiter')
        raw.extend(block)
        pos += code - 1
        if code != 255 and pos < len(encoded):
            raw.append(0)
    if not 12 <= len(raw) <= 60:
        raise ValueError('Invalid raw length')
    version, opcode, sequence, session, size = HEADER.unpack_from(raw)
    if version != VERSION or not sequence or size > PAYLOAD_MAX or len(raw) != size + 12:
        raise ValueError('Invalid frame header')
    if struct.unpack_from('<H', raw, len(raw) - 2)[0] != binascii.crc_hqx(raw[:-2], 0xffff):
        raise ValueError('CRC mismatch')
    return Frame(opcode, sequence, session, bytes(raw[10:-2]))

class FrameReader:
    def __init__(self):
        self.buffer = bytearray()
        self.discarding = False

    def feed(self, data):
        frames = []
        for byte in data:
            if byte:
                if self.discarding:
                    continue
                if len(self.buffer) == WIRE_MAX - 1:
                    self.buffer.clear()
                    self.discarding = True
                else:
                    self.buffer.append(byte)
            else:
                if self.buffer and not self.discarding:
                    try:
                        frames.append(decode(self.buffer))
                    except ValueError:
                        pass
                self.buffer.clear()
                self.discarding = False
        return frames

class ProtocolError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f'Device result {code}')

class Client:
    def __init__(self, transport, board_id, timeout=0.4):
        if not re.fullmatch(r'[0-9A-Fa-f]{16}', board_id or ''):
            raise ValueError('Expected 16-digit Flash board ID')
        self.transport, self.board_id, self.timeout = transport, board_id.upper(), timeout
        self.session = self.sequence = self.last_nonce = 0
        self.reader = FrameReader()

    def _exchange(self, opcode, sequence, session, payload=b'', reply_session=None):
        expected_session = session if reply_session is None else reply_session
        wire = b'\0' + encode(Frame(opcode, sequence, session, payload))
        for _ in range(2):
            try:
                self.transport.write(wire)  # identical retry; never replay an old control after recover
            except OSError:
                self.session = 0
                raise
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                try:
                    data = self.transport.read(64)
                except OSError:
                    self.session = 0
                    raise
                for frame in self.reader.feed(data):
                    if (frame.opcode, frame.sequence, frame.session) != (opcode | 0x80, sequence, expected_session):
                        continue
                    if not frame.payload:
                        raise ValueError('Empty result payload')
                    if frame.payload[0]:
                        if frame.payload[0] == NO_SESSION:
                            self.session = 0
                        raise ProtocolError(frame.payload[0])
                    return frame.payload[1:]
                if not data:
                    time.sleep(0.001)
        self.session = 0
        raise TimeoutError('J4 response timeout; recover before sending new controls')

    def info(self):
        # Out-of-session INFO does not consume a control-session sequence number.
        data = self._exchange(INFO, 1, 0)
        if len(data) != 37:
            raise ValueError('Wrong INFO size')
        version, firmware, board, caps, baud, idle, maximum = struct.unpack('<B16s8sIIHH', data)
        return {'protocol': version, 'firmware': firmware.rstrip(b'\0').decode('ascii'),
                'board_id': board.hex().upper(), 'capabilities': caps, 'baud': baud,
                'default_idle_ms': idle, 'max_payload': maximum, 'auxiliary_input': bool(caps & 16)}

    def open(self):
        self.session = self.sequence = 0
        info = self.info()
        if info['board_id'] != self.board_id:
            raise ValueError(f"Wrong board: expected {self.board_id}, got {info['board_id']}")
        if info['protocol'] != VERSION or info['capabilities'] & 15 != 15:
            raise ValueError('Unsupported J4 device capabilities')
        nonce = secrets.randbits(32) or 1
        if nonce == self.last_nonce:
            nonce = (nonce + 1) & 0xffffffff or 1
        reply = self._exchange(HELLO, 1, 0, struct.pack('<I', nonce), nonce)
        if reply != struct.pack('<I', nonce):
            raise ValueError('HELLO nonce mismatch')
        self.session, self.sequence, self.last_nonce = nonce, 1, nonce
        return info

    def recover(self):
        self.session = 0
        self.transport.reopen()
        self.reader = FrameReader()
        return self.open()

    def request(self, opcode, payload=b''):
        if not self.session:
            raise RuntimeError('Open/recover the J4 session first')
        sequence = 1 if self.sequence == 65535 else self.sequence + 1
        result = self._exchange(opcode, sequence, self.session, payload)
        self.sequence = sequence
        if opcode == CLOSE:
            self.session = 0
        return result

    def ping(self):
        return self.request(PING)

    def status(self):
        data = self.request(STATUS)
        if len(data) != 36:
            raise ValueError('Wrong STATUS size')
        values = struct.unpack('<IBBHIIIIIIHH', data)
        keys = ('flags', 'host_phase', 'host_error', 'buttons', 'rx_frames', 'crc_errors',
                'format_errors', 'transport_errors', 'tx_busy_drops', 'session',
                'idle_remaining_ms', 'identify_remaining_ms')
        return dict(zip(keys, values))

    def identify(self, milliseconds):
        if not 0 <= milliseconds <= 5000:
            raise ValueError('Identify duration must be 0..5000ms')
        payload = struct.pack('<H', milliseconds)
        if self.request(IDENTIFY, payload) != payload:
            raise ValueError('IDENTIFY response mismatch')

    def set_idle(self, milliseconds):
        if not 500 <= milliseconds <= 5000:
            raise ValueError('Idle timeout must be 500..5000ms')
        payload = struct.pack('<H', milliseconds)
        if self.request(SET_IDLE, payload) != payload:
            raise ValueError('SET_IDLE response mismatch')

class SimulatorTransport:
    kind = 'SIMULATION'

    def __init__(self, executable):
        executable = Path(executable).resolve()
        if not executable.is_file():
            raise ValueError('Build the offline C j4_simulator first')
        self.process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, bufsize=1)
        self.buffer = bytearray()
        self.written = []
        self.now_us = 1000

    def write(self, data):
        self.written.append(bytes(data))
        self.now_us += 1000
        self.process.stdin.write(f'{self.now_us} {data.hex()}\n')
        self.process.stdin.flush()
        response = self.process.stdout.readline().strip()
        if response == 'ERROR' or self.process.poll() is not None:
            raise RuntimeError('Offline C simulator failed')
        self.buffer.extend(bytes.fromhex(response))

    def read(self, count):
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data

    def advance_ms(self, milliseconds):
        self.now_us += milliseconds * 1000

    def reopen(self):
        self.buffer.clear()  # same simulated MCU continues running

    def close(self):
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=1)
        self.process.stdout.close()
        self.process.stderr.close()

class DCB(ctypes.Structure):
    _fields_ = [('length', ctypes.c_uint32), ('baud', ctypes.c_uint32), ('flags', ctypes.c_uint32),
                ('reserved', ctypes.c_uint16), ('xon_limit', ctypes.c_uint16), ('xoff_limit', ctypes.c_uint16),
                ('byte_size', ctypes.c_uint8), ('parity', ctypes.c_uint8), ('stop_bits', ctypes.c_uint8),
                ('xon', ctypes.c_char), ('xoff', ctypes.c_char), ('error', ctypes.c_char),
                ('eof', ctypes.c_char), ('event', ctypes.c_char), ('reserved1', ctypes.c_uint16)]

class Timeouts(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in ('read_interval', 'read_multiplier', 'read_constant',
                                                  'write_multiplier', 'write_constant')]

class Win32Serial:
    kind = 'LIVE_COM'

    def __init__(self, port, hardware_authorized=False, *, nonblocking_read=False):
        if not hardware_authorized:
            raise PermissionError('J4 device access requires separate stage authorization')
        if sys.platform != 'win32' or not re.fullmatch(r'COM[1-9][0-9]*', port or '', re.I):
            raise ValueError('Supply an exact Windows COM port')
        self.port, self.handle = port.upper(), None
        self.nonblocking_read = nonblocking_read
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        handle, dword, pointer = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p
        signatures = {
            'CreateFileW': ([ctypes.c_wchar_p, dword, dword, pointer, dword, dword, handle], handle),
            'CloseHandle': ([handle], ctypes.c_int),
            'GetCommState': ([handle, ctypes.POINTER(DCB)], ctypes.c_int),
            'SetCommState': ([handle, ctypes.POINTER(DCB)], ctypes.c_int),
            'SetCommTimeouts': ([handle, ctypes.POINTER(Timeouts)], ctypes.c_int),
            'PurgeComm': ([handle, dword], ctypes.c_int),
            'ReadFile': ([handle, pointer, dword, ctypes.POINTER(dword), pointer], ctypes.c_int),
            'WriteFile': ([handle, pointer, dword, ctypes.POINTER(dword), pointer], ctypes.c_int),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.reopen()

    @staticmethod
    def checked(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())

    @staticmethod
    def _timeouts(nonblocking_read=False):
        return (Timeouts(0xffffffff, 0, 0, 0, 100) if nonblocking_read
                else Timeouts(0xffffffff, 0xffffffff, 20, 0, 100))

    def reopen(self):
        self.close()
        self.handle = self.kernel.CreateFileW('\\\\.\\' + self.port, 0xc0000000, 0, None, 3, 0, None)
        if self.handle in (None, ctypes.c_void_p(-1).value):
            self.handle = None
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if ctypes.sizeof(DCB) != 28:
                raise ValueError('Invalid Win32 DCB ABI')
            config = DCB()
            config.length = ctypes.sizeof(DCB)
            self.checked(self.kernel.GetCommState(self.handle, ctypes.byref(config)))
            config.baud, config.flags, config.byte_size, config.parity, config.stop_bits = BAUD, 1, 8, 0, 0
            config.xon, config.xoff = b'\x11', b'\x13'
            self.checked(self.kernel.SetCommState(self.handle, ctypes.byref(config)))
            limits = self._timeouts(self.nonblocking_read)
            self.checked(self.kernel.SetCommTimeouts(self.handle, ctypes.byref(limits)))
            self.checked(self.kernel.PurgeComm(self.handle, 12))
        except Exception:
            self.close()
            raise

    def read(self, count):
        data, received = ctypes.create_string_buffer(count), ctypes.c_uint32()
        self.checked(self.kernel.ReadFile(self.handle, data, count, ctypes.byref(received), None))
        return data.raw[:received.value]

    def write(self, data):
        buffer, written = ctypes.create_string_buffer(data, len(data)), ctypes.c_uint32()
        self.checked(self.kernel.WriteFile(self.handle, buffer, len(data), ctypes.byref(written), None))
        if written.value != len(data):
            raise OSError('Short COM write; recover before sending new controls')

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

def self_test(client):
    info = client.open()
    initial = client.status()
    client.identify(500)
    identified = client.status()
    if identified['identify_remaining_ms'] == 0:
        raise ValueError('Identify control had no reported effect')
    client.identify(0)
    if client.status()['identify_remaining_ms'] != 0:
        raise ValueError('Identify cancel did not clear state')
    client.set_idle(500)
    client.ping()
    if isinstance(client.transport, SimulatorTransport):
        client.transport.advance_ms(600)
    else:
        time.sleep(0.6)
    try:
        client.status()
    except ProtocolError as error:
        if error.code != NO_SESSION:
            raise
    else:
        raise ValueError('Session did not expire')
    old = client.last_nonce
    client.recover()
    recovered = client.status()
    if client.session == old or recovered['identify_remaining_ms']:
        raise ValueError('Recovery reused old control/session')
    client.request(CLOSE)
    return {'info': info, 'initial': initial, 'identify': identified, 'recovered': recovered,
            'checks': 'INFO/STATUS/IDENTIFY/cancel/SET_IDLE/PING/expiry/reopen/new HELLO/CLOSE PASS'}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true')
    mode.add_argument('--simulate', type=Path)
    parser.add_argument('--port')
    parser.add_argument('--board-id')
    parser.add_argument('--hardware-authorized', action='store_true')
    parser.add_argument('--command', choices=('info', 'status', 'identify', 'ping', 'self-test'), default='info')
    parser.add_argument('--identify-ms', type=int, default=500)
    transport = None
    try:
        args = parser.parse_args(argv)
        if not args.live and not args.simulate:
            print(json.dumps({'mode': 'CHECK', 'device_access': 'NONE', 'hardware': 'NOT TESTED',
                              'baud': BAUD, 'protocol': VERSION, 'commands': ['info', 'status', 'identify', 'ping', 'self-test']}, indent=2))
            return 0
        if args.live:
            if not args.hardware_authorized or not args.port or not re.fullmatch(r'[0-9A-Fa-f]{16}', args.board_id or ''):
                raise ValueError('Live J4 requires separate stage authorization, exact --port and 16-digit --board-id')
            transport = Win32Serial(args.port, hardware_authorized=True)
            board = args.board_id
        else:
            transport = SimulatorTransport(args.simulate)
            board = args.board_id or '0000000000000000'
        client = Client(transport, board)
        if args.command == 'self-test':
            result = self_test(client)
        elif args.command == 'info':
            result = client.info()
            if result['board_id'] != client.board_id:
                raise ValueError('Wrong board ID')
        else:
            info = client.open()
            if args.command == 'identify':
                client.identify(args.identify_ms)
            elif args.command == 'ping':
                client.ping()
            result = {'info': info, 'status': client.status()}
        print(json.dumps({'source': transport.kind, 'hardware': 'NOT TESTED' if args.simulate else 'LIVE_COMMAND_RESULTS',
                          'result': result}, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    finally:
        if transport:
            transport.close()

if __name__ == '__main__':
    raise SystemExit(main())
