"""Production-C peer with a separate virtual MCU clock and persistent J4 lifetime."""
import os
from pathlib import Path
import subprocess
import time


class PresetSimulatorTransport:
    kind='SIMULATION'

    def __init__(self,executable,*,store_file=None,device_flash=False):
        self.executable=Path(executable).resolve()
        if not self.executable.is_file():raise ValueError('Build the offline preset C simulator first')
        self.process=None;self.buffer=bytearray();self.started_ns=0;self.now_us=0
        self.opened=False
        self.store_file=Path(store_file).resolve() if store_file else None
        self.device_flash=device_flash

    def _exchange(self,command):
        if self.process is None:raise OSError('C simulator is shut down')
        self.now_us=max(self.now_us,(time.perf_counter_ns()-self.started_ns)//1000)
        self.process.stdin.write(f'{self.now_us} {command}\n');self.process.stdin.flush()
        response=self.process.stdout.readline().strip()
        if response=='ERROR' or self.process.poll() is not None:raise OSError('Offline preset C simulator failed')
        self.buffer.extend(bytes.fromhex(response))

    def reopen(self):
        if self.process is None:
            flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
            command=[str(self.executable)]
            if self.device_flash:command+=['--device-flash']
            if self.store_file:
                self.store_file.parent.mkdir(parents=True,exist_ok=True);command+=['--store-file',str(self.store_file)]
            self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE,text=True,bufsize=1,creationflags=flags)
            self.started_ns=time.perf_counter_ns();self.now_us=0
        self.buffer.clear();self.opened=True;self._exchange('-')

    def write(self,data):
        if not self.opened:raise OSError('J4 simulated transport is disconnected')
        self._exchange(data.hex())

    def read(self,count):
        if not self.opened:raise OSError('J4 simulated transport is disconnected')
        if not self.buffer:self._exchange('-')
        data=bytes(self.buffer[:count]);del self.buffer[:count];return data

    def close(self):
        if self.process is not None and self.opened:self._exchange('D')
        self.opened=False;self.buffer.clear()  # MCU RAM and execution continue

    def inject_physical(self,mask,dx=0,dy=0,*,wheel=0):
        """Virtual ingress for offline tests; deliberately outside developer device API."""
        if self.process is None:raise OSError('Connect the simulator first')
        self._exchange(f'P {mask} {dx} {dy} {wheel}')

    def shutdown(self):
        self.opened=False
        if self.process is not None:
            self.process.stdin.close()
            try:self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=1)
            self.process.stdout.close();self.process.stderr.close();self.process=None
