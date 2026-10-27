"""Lifecycle adapter around the existing production-C offline J4 simulator."""
import time

from ._j4 import SimulatorTransport


class Candidate03Transport:
    kind = 'SIMULATION'

    def __init__(self, executable):
        self.executable = executable
        self.peer = None
        self.started_ns = 0

    def reopen(self):
        if self.peer is None:
            self.peer = SimulatorTransport(self.executable)
            self.started_ns = time.perf_counter_ns()
        else:
            self.peer.reopen()

    def write(self, data):
        if self.peer is None:
            raise OSError('Offline C simulator is closed')
        # On Python <=3.12 Windows monotonic_ns uses coarse GetTickCount64.
        # The device's 1ms model needs QPC, matching SDK/source expiry clocks.
        self.peer.now_us = (time.perf_counter_ns() - self.started_ns) // 1000 + 1000
        self.peer.write(data)
        # The legacy tester retains a transcript for assertions; a service
        # keeps only a bounded diagnostic tail, never an unbounded history.
        del self.peer.written[:-64]

    def read(self, count):
        if self.peer is None:
            raise OSError('Offline C simulator is closed')
        return self.peer.read(count)

    def close(self):
        if self.peer is not None:
            self.peer.close()
            self.peer = None

