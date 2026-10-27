"""One transport owner; bounded, independently scheduled J4 logical lanes."""
import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
import re
import secrets
import struct
import time

from . import _j4 as j4
from .contract import (CAP_CLOCK, CAP_CONFIG, CAP_MODE, CAP_MOVE, CLOCK,
                       CONTROL_QUEUE, DEVICE_ERRORS, MOVE, MOVE_BODY, parse_info)
from .errors import FusionError
from .bypass import CAP_BYPASS,GET_BYPASS,SET_BYPASS,decode_status
from .ai_gate import CAP_AI_GATE,CAP_AI_SMOOTH,MOVE_LEFT_HELD,MOVE_SMOOTH,CANCEL_SMOOTH,REALTIME_MOVES


@dataclass
class Request:
    opcode: int
    payload: bytes = b''
    future: object = None
    owner: str = ''
    deadline_ns: int = 0
    sequence: int = 0
    session: int = 0
    reply_session: int = 0
    sent: bool = False
    motion_sequence: int = 0
    expires_ns: int = 0
    write_ticket: object = None
    auxiliary_generation: int = 0
    left_gate: object = None
    smooth_ms: object = None


class DeviceLink:
    def __init__(self, transport, board_id, *, control_limit=CONTROL_QUEUE, timeout=.4):
        if not re.fullmatch(r'[0-9a-fA-F]{16}', board_id or ''):
            raise ValueError('Expected exact 16-digit Flash board ID')
        self.transport, self.board_id = transport, board_id.upper()
        self.control_limit, self.timeout_ns = control_limit, int(timeout * 1000000000)
        self.controls = deque()
        self.latest_move = self.pending_control = self.pending_move = None
        self.reader = j4.FrameReader()
        self.wake = asyncio.Event()
        self.task = None
        self.active = self.connected = False
        self.session = self.sequence = self.generation = self.motion_sequence = 0
        self.info = None
        self.extension_revision = None
        self.auxiliary_generation = 0
        self.clock_us = self.clock_received_ns = self.last_reply_ns = 0
        self.error = None
        self.stats = {'moves_sent': 0, 'moves_acknowledged': 0, 'expired': 0, 'superseded': 0,'moves_rejected':{}}

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name='single-j4-owner')

    @staticmethod
    def _finish(request, *, result=None, error=None):
        if request is not None and request.future is not None and not request.future.done():
            if error is not None:
                request.future.set_exception(error)
            else:
                request.future.set_result(result)

    def _fail(self, error):
        self.connected = self.active = False
        self.session = 0
        self.error = error.as_dict()
        for request in (self.pending_control, self.pending_move, self.latest_move, *self.controls):
            uncertain = bool(request and request.sent)
            failure = FusionError(error.code, str(error), {'outcome_unknown': uncertain, 'replayed': False})
            self._finish(request, error=failure)
        self.pending_control = self.pending_move = self.latest_move = None
        self.controls.clear()
        self.clock_received_ns = 0
        with suppress(OSError):
            self.transport.close()

    async def open(self):
        if self.connected:
            return self.info
        self.reader = j4.FrameReader()
        self.session = self.sequence = self.motion_sequence = 0
        self.extension_revision = None
        self.error = None
        try:
            self.transport.reopen()
            self.active = True
            info = parse_info(await self.request(j4.INFO))
            if info['board_id'] != self.board_id:
                raise FusionError('DEVICE_ID_MISMATCH', 'Device Flash board ID does not match')
            if (info['protocol'] != j4.VERSION or info['capabilities'] & 15 != 15
                    or info['baud'] != j4.BAUD or info['max_payload'] != j4.PAYLOAD_MAX):
                raise FusionError('PROTOCOL_MISMATCH', 'Unsupported J4 version or framing contract')
            self.info = info
            nonce = secrets.randbits(32) or 1
            payload = struct.pack('<I', nonce)
            if await self.request(j4.HELLO, payload) != payload:
                raise FusionError('PROTOCOL_ERROR', 'HELLO nonce mismatch')
            self.connected = True
            if info['capabilities'] & CAP_CLOCK:
                await self.request(CLOCK)
            if info['capabilities'] & CAP_BYPASS:
                await self.request(GET_BYPASS)
            self.generation += 1
            return info
        except (OSError, ValueError) as error:
            failure = FusionError('DEVICE_DISCONNECTED', str(error), {'replayed': False})
            self._fail(failure)
            raise failure from error
        except FusionError as error:
            self._fail(error)
            raise

    async def request(self, opcode, payload=b'', *, owner='',auxiliary_generation=None):
        if not self.active:
            raise FusionError('DEVICE_DISCONNECTED', 'Reconnect the device before sending commands')
        if len(payload) > j4.PAYLOAD_MAX:
            raise FusionError('INVALID_ARGUMENT', 'J4 payload exceeds 48 bytes')
        if len(self.controls) >= self.control_limit:
            raise FusionError('QUEUE_FULL', 'Control queue is full')
        future = asyncio.get_running_loop().create_future()
        request = Request(opcode, payload, future, owner, time.perf_counter_ns() + self.timeout_ns)
        request.auxiliary_generation=self.auxiliary_generation if auxiliary_generation is None else auxiliary_generation
        self.controls.append(request)
        self.wake.set()
        try:
            return await future
        finally:
            if future.cancelled():
                self._cancel_submission(request)

    async def move(self, owner, dx, dy, expires_ns,*,auxiliary_generation=None,left_gate=None,smooth_ms=None):
        if not self.connected:
            raise FusionError('DEVICE_DISCONNECTED', 'Reconnect before sending a new move')
        self.require(CAP_MOVE | CAP_CLOCK, 'move')
        if left_gate is not None:self.require(CAP_AI_GATE,'move_when_left_held')
        if smooth_ms is not None:self.require(CAP_AI_SMOOTH,'move_smoothed_when_left_held')
        if expires_ns <= time.perf_counter_ns():
            self.stats['expired'] += 1
            raise FusionError('EXPIRED', 'Movement expired before entering the J4 scheduler')
        future = asyncio.get_running_loop().create_future()
        opcode=MOVE_SMOOTH if smooth_ms is not None else MOVE_LEFT_HELD if left_gate is not None else MOVE
        request = Request(opcode, struct.pack('<hh', dx, dy), future, owner, expires_ns)
        request.left_gate=left_gate
        request.smooth_ms=smooth_ms
        request.auxiliary_generation = self.auxiliary_generation if auxiliary_generation is None else auxiliary_generation
        if self.latest_move is not None:
            self.stats['superseded'] += 1
            self._finish(self.latest_move, error=FusionError('SUPERSEDED', 'A newer move replaced the pending move'))
        self.latest_move = request
        self.wake.set()
        try:
            return await future
        finally:
            if future.cancelled():
                if self.latest_move is request:
                    self.latest_move = None
                self._cancel_submission(request)

    def require(self, capability, operation):
        if self.info is None or self.info['capabilities'] & capability != capability:
            raise FusionError('NOT_SUPPORTED', f'{operation} is not supported by this firmware',
                              {'firmware': self.info['firmware'] if self.info else None})

    def cancel_owner(self, owner):
        for request in (*self.controls, self.latest_move, self.pending_control, self.pending_move):
            if request is not None and request.owner == owner:
                self._cancel_submission(request)
                self._finish(request, error=FusionError('NOT_CONNECTED', 'Client disconnected',
                                                       {'outcome_unknown': request.sent, 'replayed': False}))
        if self.latest_move is not None and self.latest_move.owner == owner:
            self.latest_move = None

    def cancel_owner_motion(self,owner):
        for request in (self.latest_move,self.pending_move):
            if request is not None and request.owner==owner:
                self._cancel_submission(request)
                self._finish(request,error=FusionError('CANCELLED','Newer target state cancelled pending motion',{'replayed':False}))
        if self.latest_move is not None and self.latest_move.owner==owner:self.latest_move=None

    def _cancel_submission(self, request):
        cancel = getattr(self.transport, 'cancel_write', None)
        if request.sent and cancel is not None and cancel(request.write_ticket):
            request.sent = False
            if self.pending_move is request:
                self.pending_move = None
            if self.pending_control is request:
                # No bytes reached the device, so the next control request
                # reuses the unconsumed J4 sequence. Started writes retain
                # their pending response ownership instead.
                self.pending_control = None
            self.wake.set()

    async def close_session(self):
        if self.connected:
            with suppress(FusionError):
                await self.request(j4.CLOSE)
        self._fail(FusionError('DEVICE_DISCONNECTED', 'No clients are connected'))

    async def stop(self):
        self._fail(FusionError('DEVICE_DISCONNECTED', 'Local service stopped'))
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        shutdown = getattr(self.transport, 'shutdown', None)
        if shutdown is not None:
            shutdown()

    def _write(self, request):
        # Never retransmit. A lost response may mean the command executed.
        wire = b'\0' + j4.encode(j4.Frame(request.opcode, request.sequence, request.session, request.payload))
        request.sent = True
        if request.opcode in REALTIME_MOVES and hasattr(self.transport, 'write_deadline'):
            request.write_ticket = self.transport.write_deadline(wire, request.expires_ns)
        else:
            request.write_ticket = self.transport.write(wire)
        self.wake.set()

    def _send_control(self, request):
        if request.opcode==CANCEL_SMOOTH:request.payload=struct.pack('<I',self.motion_sequence)
        if self.info and self.info['capabilities'] & CAP_BYPASS:
            if request.opcode==0x24 and request.payload[0] in (0,1,2,3,4):
                p=request.payload
                request.payload=bytes([1,p[0],p[1],len(p)==6])+(p[2:] if len(p)==6 else b'\0'*4)+struct.pack('<I',request.auxiliary_generation)
            elif request.opcode==0x2c and request.payload[0] in (0,2,3):
                request.payload+=struct.pack('<I',request.auxiliary_generation)
        if request.opcode == j4.INFO:
            request.sequence, request.session, request.reply_session = 1, 0, 0
        elif request.opcode == j4.HELLO:
            request.sequence, request.session = 1, 0
            request.reply_session = struct.unpack('<I', request.payload)[0]
        else:
            request.sequence = 1 if self.sequence == 65535 else self.sequence + 1
            request.session = request.reply_session = self.session
        self.pending_control = request
        self._write(request)

    def _send_move(self, request, now):
        if request.deadline_ns <= now:
            self.stats['expired'] += 1
            return self._finish(request, error=FusionError('EXPIRED', 'Movement expired in the latest-only slot'))
        if not self.clock_received_ns or now - self.clock_received_ns > 1000000000:
            return self._finish(request, error=FusionError('CLOCK_UNSYNCED', 'Device deadline clock is stale'))
        if self.motion_sequence == 0xffffffff:
            return self._finish(request, error=FusionError('STALE_SEQUENCE', 'Reconnect to start a new movement sequence'))
        self.motion_sequence += 1
        request.motion_sequence = self.motion_sequence
        dx, dy = struct.unpack('<hh', request.payload)
        # RX-time clock anchor is conservative: time spent receiving a CLOCK
        # reply shortens validity. Absolute device deadline includes wire delay.
        deadline = (self.clock_us + (request.deadline_ns - self.clock_received_ns) // 1000) & 0xffffffff
        request.payload = MOVE_BODY.pack(self.motion_sequence, dx, dy, deadline)
        if self.info['capabilities'] & CAP_BYPASS or request.left_gate is not None:
            request.payload += struct.pack('<I',request.auxiliary_generation)
        if request.left_gate is not None:request.payload+=struct.pack('<4I',*request.left_gate)
        if request.smooth_ms is not None:request.payload+=struct.pack('<H',request.smooth_ms)
        request.sequence = (self.motion_sequence - 1) % 65535 + 1
        request.session = request.reply_session = self.session
        request.expires_ns = request.deadline_ns
        request.deadline_ns = now + 100000000  # ACK deadline; independent of motion validity
        self.pending_move = request
        self.stats['moves_sent'] += 1
        self._write(request)

    def _response(self, frame, now):
        request = self.pending_move if frame.opcode in tuple(op|0x80 for op in REALTIME_MOVES) else self.pending_control
        if request is None or (frame.opcode, frame.sequence, frame.session) != (
                request.opcode | 0x80, request.sequence, request.reply_session):
            return  # late replies / wrong sessions cannot satisfy a new request
        if not frame.payload:
            raise FusionError('PROTOCOL_ERROR', 'Empty J4 result')
        code, body = frame.payload[0], frame.payload[1:]
        if self.info and self.info['capabilities'] & CAP_BYPASS:
            if code in (17,18):
                if len(body)!=4 or not struct.unpack('<I',body)[0]:
                    raise FusionError('PROTOCOL_ERROR','Wrong auxiliary generation error body')
                self.auxiliary_generation=struct.unpack('<I',body)[0]
            elif code==0 and request.opcode in (SET_BYPASS,GET_BYPASS):
                try:self.auxiliary_generation=decode_status(body)['generation']
                except ValueError as error:raise FusionError('PROTOCOL_ERROR',str(error)) from error
        # Keep ownership until the complete response is validated, so a bad ACK
        # fails its caller instead of leaving an unowned Future waiting forever.
        if code == 0 and request.opcode in REALTIME_MOVES:
            if len(body) != 5:
                raise FusionError('PROTOCOL_ERROR', 'Wrong movement ACK size')
            sequence, disposition = struct.unpack('<IB', body)
            if sequence != request.motion_sequence or disposition != self.extension_revision:
                raise FusionError('PROTOCOL_ERROR', 'Wrong movement ACK sequence or disposition')
        if code == 0 and request.opcode == CLOCK:
            if (len(body) != 5 or body[0] not in (1, 2)
                    or self.extension_revision not in (None, body[0])):
                raise FusionError('PROTOCOL_MISMATCH', 'Unsupported or changed extension revision / CLOCK size')
        if request.opcode in REALTIME_MOVES:
            self.pending_move = None
        else:
            self.pending_control = None
        if code:
            if request.opcode in REALTIME_MOVES:
                name=DEVICE_ERRORS.get(code,'PROTOCOL_ERROR')
                counts=self.stats['moves_rejected'];counts[name]=counts.get(name,0)+1
            error = FusionError(DEVICE_ERRORS.get(code, 'PROTOCOL_ERROR'), f'Device rejected opcode {request.opcode:#x}',
                                {'device_result': code, 'outcome_unknown': False, 'replayed': False,
                                 **({'auxiliary_generation':self.auxiliary_generation} if code in (17,18) else {})})
            self._finish(request, error=error)
            if code in (3, 4):
                self._fail(error)
            return
        self.last_reply_ns = now
        if request.opcode in REALTIME_MOVES:
            sequence, disposition = struct.unpack('<IB', body)
            self.stats['moves_acknowledged'] += 1
            self._finish(request, result={'device_sequence': sequence,
                                          'disposition': {1: 'simulated_applied', 2: 'accepted_for_cycle'}[disposition],
                                          'source': self.transport.kind, 'delivery_confirmed': False,
                                          'hardware_verified': False})
        else:
            if request.opcode == j4.HELLO:
                self.session, self.sequence = request.reply_session, request.sequence
            elif request.opcode != j4.INFO:
                self.sequence = request.sequence
            if request.opcode == CLOCK:
                self.extension_revision = body[0]
                self.clock_us = struct.unpack_from('<I', body, 1)[0]
                self.clock_received_ns = now
            self._finish(request, result=body)

    def _transport_expiry(self, error):
        """A worker can prove a queued MOVE was never written. Retire only it."""
        request = self.pending_move
        details = error.details
        if (error.code != 'EXPIRED' or request is None or details.get('outcome_unknown') is not False
                or (details.get('opcode'), details.get('sequence'), details.get('session')) !=
                (request.opcode, request.sequence, request.session)):
            return False
        self.pending_move = None
        self.stats['expired'] += 1
        request.sent = False
        self._finish(request, error=error)
        return True

    async def _run(self):
        while True:
            if self.active:
                try:
                    now = time.perf_counter_ns()
                    try:
                        data = self.transport.read(128)
                    except FusionError as error:
                        if not self._transport_expiry(error):
                            raise
                        data = b''
                    for frame in self.reader.feed(data):
                        self._response(frame, time.perf_counter_ns())
                    for request in (self.pending_control, self.pending_move):
                        if request is not None and now >= request.deadline_ns:
                            error = FusionError('TIMEOUT', 'J4 response timed out; explicit reconnect required',
                                                {'outcome_unknown': request.sent, 'replayed': False})
                            self._fail(error)
                            break
                    if self.active:
                        if self.pending_move is None and self.latest_move is not None:
                            request, self.latest_move = self.latest_move, None
                            if not request.future.done():
                                self._send_move(request, now)
                        if self.pending_control is None:
                            request = None
                            if self.connected and self.info['capabilities'] & CAP_CLOCK and self.clock_received_ns and now - self.clock_received_ns >= 250000000:
                                request = Request(CLOCK, deadline_ns=now + self.timeout_ns)
                            else:
                                while self.controls:
                                    candidate = self.controls.popleft()
                                    if candidate.future.done():
                                        continue
                                    if now >= candidate.deadline_ns:
                                        self._finish(candidate, error=FusionError('TIMEOUT', 'Control request expired before transmission'))
                                        continue
                                    request = candidate
                                    break
                                if request is None and self.connected and now - self.last_reply_ns >= 300000000:
                                    request = Request(j4.PING, deadline_ns=now + self.timeout_ns)
                            if request is not None:
                                self._send_control(request)
                except (OSError, RuntimeError, ValueError) as error:
                    failure = error if isinstance(error, FusionError) else FusionError('DEVICE_DISCONNECTED', str(error))
                    self._fail(failure)
            # New IPC work and an immediately available simulator reply wake
            # the actor directly; the polling timer is only for idle RX/ticks.
            if self.wake.is_set():
                self.wake.clear()
                await asyncio.sleep(0)
            else:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.wake.wait(), timeout=.001)
                self.wake.clear()

