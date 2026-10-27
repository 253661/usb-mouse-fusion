"""Shipped byte-level J4 reference peer. Never represents a hardware/HID result.

The extension is deliberately absent from candidate03. This model exercises the
same framing, actor, IPC and SDK used by the service, including delayed replies
and physical unplug/replug. It does not emulate RP2040 timing or USB Host/Device.
"""
import heapq
import struct
import time

from . import _j4 as j4
from .contract import (AUX_BODY, AUX_STATUS, CLOCK, CONFIG_BODY, GET_CONFIG, MOVE,
                       MOVE_BODY, SET_CONFIG, SET_MODE)


class SimulatedTransport:
    kind = 'SIMULATION'

    def __init__(self, *, profile='fusion', board_id='0000000000000000', info_protocol=1,
                 status_delay_ms=0, move_reply_delay_ms=0, drop_after_moves=None, fault=None):
        if profile not in ('fusion', 'candidate03'):
            raise ValueError('Simulation profile must be fusion or candidate03')
        self.profile, self.board_id, self.info_protocol = profile, board_id, info_protocol
        self.status_delay_ms, self.drop_after_moves = status_delay_ms, drop_after_moves
        if fault not in (None, 'clock_reply', 'move_reply'):
            raise ValueError('Unknown simulator fault')
        self.move_reply_delay_ms, self.fault = move_reply_delay_ms, fault
        self._boot_ns = time.perf_counter_ns()
        self._reader = j4.FrameReader()
        self._responses, self._buffer = [], bytearray()
        self._reply_order = 0
        self._plugged = self._opened = True
        self._drop_on_next_io = False
        self.session = self.sequence = self.last_motion_sequence = 0
        self._last_request = self._last_reply = None
        self.idle_ms, self._last_valid_ns = 2000, time.perf_counter_ns()
        self.mode_id, self.default_ttl_ms, self.max_delta = 0, 20, 32767
        self.applied = self.dropped = self.last_dx = self.last_dy = 0
        self._last_cycle = -1
        self.status_requests = self.hello_requests = self.rx_frames = 0
        self._identify_until_ns = 0

    def device_us(self):
        return ((time.perf_counter_ns() - self._boot_ns) // 1000) & 0xffffffff

    def unplug(self):
        self._plugged = False
        self._reset_session()

    def plug_in(self):
        self._plugged = True
        self._drop_on_next_io = False
        self.drop_after_moves = None

    def _reset_session(self):
        self.session = self.sequence = self.last_motion_sequence = 0
        self._last_request = self._last_reply = None
        self._identify_until_ns = 0
        self.idle_ms = 2000
        self._responses.clear()
        self._buffer.clear()
        self._reader = j4.FrameReader()

    def reopen(self):
        if not self._plugged:
            raise OSError('Simulated J4 is unplugged')
        self._opened = True
        self._responses.clear()
        self._buffer.clear()
        self._reader = j4.FrameReader()

    def close(self):
        self._opened = False
        self._reset_session()

    def _check(self):
        if self._drop_on_next_io and not self._buffer and not self._responses:
            self.unplug()
        if not self._plugged or not self._opened:
            raise OSError('Simulated J4 is unplugged or closed')
        if self.session and time.perf_counter_ns() - self._last_valid_ns >= self.idle_ms * 1000000:
            self._reset_session()

    def read(self, count):
        self._check()
        now = time.perf_counter_ns()
        while self._responses and self._responses[0][0] <= now:
            _, _, wire = heapq.heappop(self._responses)
            self._buffer.extend(wire)
        data = bytes(self._buffer[:count])
        del self._buffer[:count]
        return data

    def write(self, data):
        self._check()
        for frame in self._reader.feed(data):
            self._dispatch(frame)

    def _reply(self, frame, code=0, body=b'', *, session=None, cache=False, delay_ms=0):
        reply = j4.encode(j4.Frame(frame.opcode | 0x80, frame.sequence,
                                  frame.session if session is None else session, bytes([code]) + body))
        if cache:
            self.sequence = frame.sequence
            self._last_request, self._last_reply = frame, reply
        self._reply_order += 1
        heapq.heappush(self._responses, (time.perf_counter_ns() + delay_ms * 1000000, self._reply_order, reply))
        if code == 0 and (frame.opcode != j4.INFO or frame.session):
            self._last_valid_ns = time.perf_counter_ns()

    def _dispatch(self, frame):
        self.rx_frames += 1
        op, data = frame.opcode, frame.payload
        if op == j4.INFO:
            if data:
                return self._reply(frame, 2)
            if frame.session and frame.session != self.session:
                return self._reply(frame, 3)
            caps = 15 if self.profile == 'candidate03' else 255
            firmware = b'0.2.0-j4-proto' if self.profile == 'candidate03' else b'sim-api-1'
            body = struct.pack('<B16s8sIIHH', self.info_protocol, firmware,
                               bytes.fromhex(self.board_id), caps, 115200, 2000, 48)
            return self._reply(frame, body=body)
        if op == j4.HELLO:
            if frame.session or len(data) != 4 or not any(data):
                return self._reply(frame, 2, session=0)
            self._reset_session()
            self.hello_requests += 1
            self.session = struct.unpack('<I', data)[0]
            return self._reply(frame, body=data, session=self.session, cache=True)
        if not self.session or frame.session != self.session:
            return self._reply(frame, 3)
        # Independent realtime sequence, no replay cache or delayed execution.
        if op == MOVE and self.profile == 'fusion':
            return self._move(frame)
        if frame == self._last_request:
            self._reply_order += 1
            heapq.heappush(self._responses, (time.perf_counter_ns(), self._reply_order, self._last_reply))
            self._last_valid_ns = time.perf_counter_ns()
            return
        if frame.sequence != (1 if self.sequence == 65535 else self.sequence + 1):
            return self._reply(frame, 4)
        body, code, delay = b'', 0, 0
        if op == j4.PING:
            code = 2 if data else 0
        elif op == j4.STATUS:
            if data:
                code = 2
            else:
                self.status_requests += 1
                delay = self.status_delay_ms
                identify_ms = max(0, (self._identify_until_ns - time.perf_counter_ns() + 999999) // 1000000)
                body = struct.pack('<IBBHIIIIIIHH', 7, 3, 0, 0, self.rx_frames, 0, 0, 0, 0,
                                   self.session, self.idle_ms, identify_ms)
        elif op == j4.IDENTIFY:
            if len(data) != 2 or struct.unpack('<H', data)[0] > 5000:
                code = 2
            else:
                self._identify_until_ns = time.perf_counter_ns() + struct.unpack('<H', data)[0] * 1000000
                body = data
        elif op == j4.SET_IDLE:
            if len(data) != 2 or not 500 <= struct.unpack('<H', data)[0] <= 5000:
                code = 2
            else:
                self.idle_ms = struct.unpack('<H', data)[0]
                body = data
        elif op == j4.CLOSE:
            if data:
                code = 2
            else:
                self._reset_session()
        elif self.profile != 'fusion':
            code = 1
        elif op == CLOCK:
            code = 2 if data else 0
            body = struct.pack('<BI', 1, self.device_us()) if not code else b''
            if self.fault == 'clock_reply':
                body = b''
        elif op == GET_CONFIG:
            code = 2 if data else 0
            body = CONFIG_BODY.pack(self.mode_id, self.default_ttl_ms, self.max_delta) if not code else b''
        elif op == SET_CONFIG:
            if len(data) != 4:
                code = 2
            else:
                ttl, maximum = struct.unpack('<HH', data)
                if not 1 <= ttl <= 100 or not 1 <= maximum <= 32767:
                    code = 2
                else:
                    self.default_ttl_ms, self.max_delta = ttl, maximum
                    body = CONFIG_BODY.pack(self.mode_id, ttl, maximum)
        elif op == SET_MODE:
            if len(data) != 1 or data[0] > 1:
                code = 2
            else:
                self.mode_id = data[0]
                body = data
        elif op == AUX_STATUS:
            code = 2 if data else 0
            body = AUX_BODY.pack(self.mode_id, self.applied, self.dropped, self.last_motion_sequence,
                                 self.last_dx, self.last_dy) if not code else b''
        else:
            code = 1
        self._reply(frame, code, body, cache=code == 0, delay_ms=delay)

    def _move(self, frame):
        if len(frame.payload) != MOVE_BODY.size:
            return self._reply(frame, 2)
        sequence, dx, dy, deadline = MOVE_BODY.unpack(frame.payload)
        remaining = (deadline - self.device_us()) & 0xffffffff
        if not sequence or sequence <= self.last_motion_sequence:
            return self._reply(frame, 7)
        # A rejected new sequence is consumed too: it can never become a later move.
        self.last_motion_sequence = sequence
        if not 0 < remaining <= 100000:
            self.dropped += 1
            return self._reply(frame, 5)
        if self.mode_id != 1:
            self.dropped += 1
            return self._reply(frame, 6)
        if abs(dx) > self.max_delta or abs(dy) > self.max_delta:
            self.dropped += 1
            return self._reply(frame, 8)
        cycle = (time.perf_counter_ns() - self._boot_ns) // 1000000
        if cycle == self._last_cycle:
            self.dropped += 1
            return self._reply(frame, 10)
        self._last_cycle = cycle
        self.applied += 1
        self.last_dx, self.last_dy = dx, dy
        body = struct.pack('<IB', sequence, 1) if self.fault != 'move_reply' else b'\xff'
        self._reply(frame, body=body, delay_ms=self.move_reply_delay_ms)
        if self.drop_after_moves is not None and self.applied >= self.drop_after_moves:
            self._drop_on_next_io = True

