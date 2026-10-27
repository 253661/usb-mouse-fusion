"""Nonblocking actor facade over one exclusive Win32 serial owner.

Constructing the facade never opens a port. Only an explicitly authorized
``reopen`` requests a handle; all blocking serial calls belong to one worker.
An I/O failure stops that worker until another explicit reopen. Bytes and
commands from a retired generation are discarded, never retried or replayed.
"""
from dataclasses import dataclass
import math
import re
import threading
import time

from . import _j4 as j4
from .contract import MOVE
from .ai_gate import REALTIME_MOVES
from .errors import FusionError


@dataclass(frozen=True)
class _Write:
    wire: bytes
    frame: j4.Frame
    expires_ns: int


class SerialTransport:
    kind = 'LIVE_COM'
    hardware_verified = False

    def __init__(self, port, hardware_authorized=False, *, rx_limit=4096,
                 poll_interval=.001, _serial_factory=None):
        self.port, self.hardware_authorized = port, hardware_authorized
        self.port = self._validate_access()
        if type(rx_limit) is not int or not 1 <= rx_limit <= 4096:
            raise ValueError('Serial RX limit must be an integer in 1..4096')
        if (isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float))
                or not math.isfinite(poll_interval) or not 0 < poll_interval <= .1):
            raise ValueError('Serial polling interval must be in (0, 0.1] seconds')
        self._rx_limit, self._poll_interval = rx_limit, poll_interval
        self._serial_factory = _serial_factory
        self._lock, self._wake = threading.Lock(), threading.Event()
        self._worker = None
        self._generation = 0
        self._wanted_open = False
        self._open_port = self.port
        self._rx = bytearray()
        # At most one unsent frame per logical lane and one expiry event.
        self._control = self._move = self._event = self._error = None

    def _validate_access(self):
        if self.hardware_authorized is not True:
            raise PermissionError('J4 device access requires separate stage authorization')
        if not isinstance(self.port, str) or not re.fullmatch(r'COM[1-9][0-9]*', self.port, re.I):
            raise ValueError('Supply an exact Windows COM port')
        return self.port.upper()

    def reopen(self):
        port = self._validate_access()
        with self._lock:
            self._generation += 1
            self._wanted_open = True
            self.port = self._open_port = port
            self._clear_buffers()
            self._error = None
            if self._worker is None:
                self._worker = threading.Thread(target=self._run,
                                                name='mouse-fusion-serial-' + port, daemon=True)
                self._worker.start()
            self._wake.set()

    def close(self):
        with self._lock:
            self._generation += 1
            self._wanted_open = False
            self._clear_buffers()
            self._wake.set()

    def _clear_buffers(self):
        self._rx.clear()
        self._control = self._move = self._event = None

    def _check_open(self):
        if not self._wanted_open:
            raise OSError('Serial transport is closed; explicitly reopen before new work')
        if self._error is not None:
            # Raising a fresh error keeps repeated polling from retaining an
            # ever-growing traceback on the sticky worker failure.
            raise OSError(*self._error.args)

    def read(self, count):
        if type(count) is not int or count < 0:
            raise ValueError('Read count must be a nonnegative integer')
        with self._lock:
            self._check_open()
            if self._event is not None:
                error, self._event = self._event, None
                raise error
            data = bytes(self._rx[:count])
            del self._rx[:count]
            return data

    def write(self, data):
        return self.write_deadline(data)

    def write_deadline(self, data, expires_ns=0):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise ValueError('Write one bounded J4 wire frame at a time')
        size = data.nbytes if isinstance(data, memoryview) else len(data)
        if not 1 <= size <= j4.WIRE_MAX + 1:
            raise ValueError('Write one bounded J4 wire frame at a time')
        if type(expires_ns) is not int or expires_ns < 0:
            raise ValueError('Host expiry must be a nonnegative monotonic nanosecond timestamp')
        wire = bytes(data)
        if not wire.endswith(b'\0'):
            raise ValueError('J4 wire frame requires a delimiter')
        frame = j4.decode(wire.strip(b'\0'))
        submission = _Write(wire, frame, expires_ns)
        with self._lock:
            self._check_open()
            if frame.opcode in REALTIME_MOVES:
                if self._move is not None or self._event is not None:
                    raise FusionError('QUEUE_FULL', 'Serial movement slot is occupied',
                                      {'outcome_unknown': False, 'replayed': False})
                self._move = submission
            else:
                if self._control is not None:
                    raise FusionError('QUEUE_FULL', 'Serial control slot is occupied',
                                      {'outcome_unknown': False, 'replayed': False})
                self._control = submission
            self._wake.set()
        return submission

    def cancel_write(self, ticket):
        """Retire that exact unsent submission, never an in-progress OS write.

        Object identity also isolates generations: reopen clears both slots,
        and a new frame, even with identical bytes, has a different ticket.
        """
        with self._lock:
            if ticket is not None and self._move is ticket:
                self._move = None
            elif ticket is not None and self._control is ticket:
                self._control = None
            else:
                return False
            self._wake.set()
            return True

    def _current(self, generation):
        return self._wanted_open and self._error is None and self._generation == generation

    def _expired(self, submission, generation):
        if (submission.frame.opcode not in REALTIME_MOVES or not submission.expires_ns
                or time.perf_counter_ns() < submission.expires_ns):
            return False
        with self._lock:
            if self._current(generation):
                frame = submission.frame
                self._event = FusionError('EXPIRED', 'Movement expired before the serial write',
                                          {'opcode': frame.opcode, 'sequence': frame.sequence,
                                           'session': frame.session, 'outcome_unknown': False,
                                           'replayed': False})
        return True

    def _failed(self, error, generation):
        with self._lock:
            if self._generation == generation and self._wanted_open:
                self._error = error if isinstance(error, OSError) else OSError(str(error))
                self._clear_buffers()

    def _run(self):
        serial, generation = None, -1
        while True:
            with self._lock:
                wanted = self._wanted_open and self._error is None
                requested_generation = self._generation
                self._wake.clear()
            if not wanted or generation != requested_generation:
                if serial is not None:
                    old, serial = serial, None
                    try:
                        old.close()
                    except Exception as error:
                        # An uncertain handle retirement must never open a
                        # replacement handle, even if reopen raced with close.
                        with self._lock:
                            failed_generation = self._generation
                        self._failed(error, failed_generation)
                with self._lock:
                    if not self._wanted_open or self._error is not None:
                        self._worker = None
                        return
                    if self._generation != requested_generation:
                        continue
                    port = self._open_port
                generation = requested_generation
                try:
                    factory = self._serial_factory or j4.Win32Serial
                    serial = factory(port, hardware_authorized=True, nonblocking_read=True)
                except Exception as error:
                    self._failed(error, generation)
                # Recheck after an open, including a close requested while the
                # OS call was blocked. No queued write precedes that check.
                continue
            try:
                with self._lock:
                    if not self._current(generation):
                        continue
                    if self._move is not None:
                        submission, self._move = self._move, None
                    else:
                        submission, self._control = self._control, None
                if submission is not None:
                    with self._lock:
                        if not self._current(generation):
                            continue
                    if self._expired(submission, generation):
                        continue
                    serial.write(submission.wire)
                with self._lock:
                    if not self._current(generation):
                        continue
                data = serial.read(min(256, self._rx_limit))
                with self._lock:
                    if self._current(generation):
                        if len(self._rx) + len(data) > self._rx_limit:
                            raise OSError('Serial RX buffer overflow; explicit reopen required')
                        self._rx.extend(data)
                if submission is None and not data:
                    self._wake.wait(self._poll_interval)
            except Exception as error:
                self._failed(error, generation)
