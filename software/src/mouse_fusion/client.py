"""Small synchronous SDK with independent control and realtime sockets."""
from contextlib import suppress
import json
from pathlib import Path
import socket
import threading
import time

from .contract import API_VERSION, MAX_LINE, TTL_MAX_MS, integer
from .errors import FusionError


class FusionClient:
    def __init__(self, *, endpoint='fusion-service.json', port=None, token=None, timeout=2):
        self.endpoint, self.port, self.token, self.timeout = Path(endpoint), port, token, timeout
        self._explicit_port, self._explicit_token = port, token
        self._channels = {}
        self._locks = {'control': threading.RLock(), 'realtime': threading.RLock()}
        self._id_lock = threading.Lock()
        self._request_id = self._motion_sequence = 0
        self.client_id = None
        self._auxiliary_generation=None

    def _settings(self):
        if self._explicit_port is None or self._explicit_token is None:
            try:
                descriptor = json.loads(self.endpoint.read_text(encoding='utf-8'))
            except (OSError, ValueError) as error:
                raise FusionError('NOT_CONNECTED', f'Read the local service endpoint: {self.endpoint}') from error
            if not isinstance(descriptor, dict) or descriptor.get('host') != '127.0.0.1' or descriptor.get('api_version') != API_VERSION:
                raise FusionError('API_VERSION_MISMATCH', 'Endpoint must describe a loopback API v1 service')
            self.port = self._explicit_port if self._explicit_port is not None else descriptor.get('port')
            self.token = self._explicit_token if self._explicit_token is not None else descriptor.get('token')
        integer(self.port, 'port', 1, 65535)
        if not isinstance(self.token, str) or not self.token:
            raise FusionError('AUTH_FAILED', 'A local service token is required')

    def _channel(self, lane):
        try:
            sock = socket.create_connection(('127.0.0.1', self.port), timeout=self.timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as error:
            raise FusionError('NOT_CONNECTED', 'Local service is unavailable') from error
        self._channels[lane] = (sock, sock.makefile('rb'))

    def _drop(self):
        self.client_id = None
        for sock, reader in tuple(self._channels.values()):
            with suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            reader.close()
            sock.close()
        self._channels.clear()

    def _ask(self, lane, method, params=None):
        # Freeze at public SDK invocation, before waiting for a socket lock.
        params=dict(params or {})
        produces_motion=(method in ('move','move_when_left_held','move_smoothed_when_left_held') or method=='preset_control' and params.get('command') in (0,1,2,3,4)
                         or method=='trajectory_execution' and params.get('action') in ('stop','return','switch'))
        if produces_motion and params.get('auxiliary_generation') is None and self._auxiliary_generation is not None:
            params['auxiliary_generation']=self._auxiliary_generation
        with self._locks[lane]:
            if lane not in self._channels:
                raise FusionError('NOT_CONNECTED', 'Call connect() first')
            with self._id_lock:
                self._request_id = self._request_id % 0xffffffff + 1
                request_id = self._request_id
            message = {'api_version': API_VERSION, 'id': request_id, 'token': self.token,
                       'method': method, 'params': params or {}}
            wire = (json.dumps(message, separators=(',', ':'), allow_nan=False) + '\n').encode()
            if len(wire) > MAX_LINE:
                raise FusionError('INVALID_ARGUMENT', 'IPC message exceeds 4096 bytes')
            sock, reader = self._channels[lane]
            try:
                sock.sendall(wire)  # one transmission only; never retry movement
                line = reader.readline(MAX_LINE + 1)
                if not line or len(line) > MAX_LINE:
                    raise ValueError('Missing / overlong IPC response')
                reply = json.loads(line)
                if not isinstance(reply, dict) or reply.get('api_version') != API_VERSION or reply.get('id') != request_id:
                    raise ValueError('Unmatched IPC response')
            except (OSError, ValueError) as error:
                self._drop()
                code = 'TIMEOUT' if isinstance(error, TimeoutError) else 'NOT_CONNECTED'
                raise FusionError(code, 'Local service connection lost; explicit connect() required',
                                  {'outcome_unknown': method in ('move', 'move_when_left_held','move_smoothed_when_left_held','cancel_smoothed_move', 'set_mode', 'set_config','save_simulated_presets','restore_simulated_presets','save_device_presets') or method.startswith('preset_') or method=='set_mouse_triggers',
                                   'replayed': False}) from error
            if reply.get('ok') is not True:
                error = reply.get('error', {})
                if error.get('details',{}).get('auxiliary_generation'):self._auxiliary_generation=error['details']['auxiliary_generation']
                raise FusionError(error.get('code', 'PROTOCOL_ERROR'), error.get('message', 'Invalid service response'), error.get('details'))
            if method in ('get_bypass_status','set_bypass_config'):self._auxiliary_generation=reply['result']['generation']
            elif reply['result'].get('auxiliary_generation'):self._auxiliary_generation=reply['result']['auxiliary_generation']
            return reply['result']

    def connect(self):
        self.disconnect()
        self._settings()
        try:
            self._channel('control')
            result = self._ask('control', 'connect')
            self.client_id = result['client_id']
            self._channel('realtime')
            self._ask('realtime', 'attach_realtime', {'client_id': self.client_id})
            self._motion_sequence = 0
            self._auxiliary_generation=result['device_info'].get('auxiliary_generation')
            return result['device_info']
        except (FusionError, KeyError):
            self._drop()
            raise

    def disconnect(self):
        if 'control' in self._channels and self.client_id is not None:
            with suppress(FusionError, OSError):
                self._ask('control', 'disconnect')
        self._drop()

    def get_device_info(self):
        return self._ask('control', 'get_device_info')

    def get_status(self):
        return self._ask('control', 'get_status')

    def move(self, dx, dy, *, ttl_ms=None,auxiliary_generation=None):
        sent_ns = time.perf_counter_ns()
        integer(dx, 'dx', -32768, 32767)
        integer(dy, 'dy', -32768, 32767)
        if ttl_ms is not None:
            integer(ttl_ms, 'ttl_ms', 1, TTL_MAX_MS)
        if auxiliary_generation is not None:integer(auxiliary_generation,'auxiliary_generation',1,0xffffffff)
        else:auxiliary_generation=self._auxiliary_generation
        with self._locks['realtime']:
            if self._motion_sequence == 0xffffffff:
                raise FusionError('STALE_SEQUENCE', 'Reconnect to start a new movement sequence')
            self._motion_sequence += 1
            return self._ask('realtime', 'move', {'dx': dx, 'dy': dy, 'ttl_ms': ttl_ms,
                                                  'sent_ns': sent_ns, 'sequence': self._motion_sequence,
                                                  **({'auxiliary_generation':auxiliary_generation} if auxiliary_generation is not None else {})})

    def set_mode(self, mode_id):
        integer(mode_id, 'mode_id', 0, 1)
        return self._ask('control', 'set_mode', {'mode_id': mode_id})

    def get_ai_gate_status(self):
        return self._ask('control','get_ai_gate_status')

    def move_when_left_held(self,dx,dy,*,gate,ttl_ms=40):
        from .ai_gate import normalize_gate
        sent_ns=time.perf_counter_ns()
        frozen=normalize_gate(gate)
        integer(dx,'dx',-32768,32767);integer(dy,'dy',-32768,32767)
        integer(ttl_ms,'ttl_ms',1,TTL_MAX_MS)
        generation=self._auxiliary_generation
        with self._locks['realtime']:
            if self._motion_sequence==0xffffffff:raise FusionError('STALE_SEQUENCE','Reconnect to reset movement sequence')
            self._motion_sequence+=1
            return self._ask('realtime','move_when_left_held',dict(dx=dx,dy=dy,gate=frozen,ttl_ms=ttl_ms,
                sent_ns=sent_ns,sequence=self._motion_sequence,auxiliary_generation=generation))

    def get_config(self):
        return self._ask('control', 'get_config')

    def move_smoothed_when_left_held(self,dx,dy,*,gate,duration_ms=30,ttl_ms=80):
        from .ai_gate import normalize_gate
        sent_ns=time.perf_counter_ns();frozen=normalize_gate(gate)
        integer(dx,'dx',-32768,32767);integer(dy,'dy',-32768,32767)
        integer(duration_ms,'duration_ms',20,40);integer(ttl_ms,'ttl_ms',1,100)
        generation=self._auxiliary_generation
        with self._locks['realtime']:
            if self._motion_sequence==0xffffffff:raise FusionError('STALE_SEQUENCE','Reconnect to reset movement sequence')
            self._motion_sequence+=1
            return self._ask('realtime','move_smoothed_when_left_held',dict(dx=dx,dy=dy,gate=frozen,duration_ms=duration_ms,
                ttl_ms=ttl_ms,sent_ns=sent_ns,sequence=self._motion_sequence,auxiliary_generation=generation))

    def cancel_smoothed_move(self):
        return self._ask('control','cancel_smoothed_move',dict(cutoff_ns=time.perf_counter_ns()))

    def get_preset_capabilities(self):
        return self._ask('control','get_preset_capabilities')

    def get_preset_status(self):
        return self._ask('control','get_preset_status')

    def set_run_policy(self,slot,policy):
        integer(slot,'slot',0,7)
        return self._ask('control','set_run_policy',dict(slot=slot,policy=policy))

    def get_run_policy(self,slot=0):
        return self._ask('control','get_run_policy',dict(slot=slot))

    def get_run_status(self):
        return self._ask('control','get_run_status')

    def get_mode_control_status(self):
        return self._ask('control','get_mode_control_status')

    def stop_trajectory_keep(self):
        return self._ask('control','trajectory_execution',dict(action='keep'))

    def return_trajectory(self,*,duration_ms=0,easing='smoothstep',auxiliary_generation=None):
        return self._ask('control','trajectory_execution',dict(action='return',duration_ms=duration_ms,easing=easing,auxiliary_generation=auxiliary_generation))

    def switch_trajectory(self,slot,*,duration_ms=0,easing='smoothstep',expected_crc32=None,auxiliary_generation=None):
        return self._ask('control','trajectory_execution',dict(action='switch',slot=slot,duration_ms=duration_ms,easing=easing,expected_crc32=expected_crc32,auxiliary_generation=auxiliary_generation))

    def simulate_mouse_buttons(self,mask,*,dx=0,dy=0,wheel=0):
        """Virtual physical ingress; only the offline production-C backend supports it."""
        return self._ask('control','simulate_mouse_buttons',dict(mask=mask,dx=dx,dy=dy,wheel=wheel))

    def set_bypass_config(self,config):
        from .bypass import normalize_config
        try:value=normalize_config(config)
        except ValueError as error:raise FusionError('INVALID_ARGUMENT',str(error)) from error
        return self._ask('control','set_bypass_config',dict(config=value))

    def get_bypass_status(self):
        return self._ask('control','get_bypass_status')

    def save_simulated_presets(self,*,safe_stopped):
        return self._ask('control','save_simulated_presets',dict(safe_stopped=safe_stopped))
    def get_device_persistence(self):
        return self._ask('control','get_device_persistence')
    def save_device_presets(self,*,expected_ram_revision,cookie,safe_stopped):
        return self._ask('control','save_device_presets',dict(expected_ram_revision=expected_ram_revision,cookie=cookie,safe_stopped=safe_stopped))

    def restore_simulated_presets(self):
        return self._ask('control','restore_simulated_presets')

    def set_mouse_triggers(self,bindings,*,mode_slots):
        from .trajectory import encode_triggers
        try:wire=encode_triggers(bindings,mode_slots)
        except (ValueError,TypeError) as error:raise FusionError('INVALID_ARGUMENT',str(error)) from error
        return self._ask('control','set_mouse_triggers',dict(data=wire.hex()))

    def load_trajectory(self,image,*,slot=0):
        from .trajectory import inspect_image
        integer(slot,'slot',0,7)
        if not isinstance(image,(bytes,bytearray)) or len(image)>8192:
            raise FusionError('INVALID_ARGUMENT','Executable image must fit the negotiated RAM slot (at most 8192 bytes)')
        try:info=inspect_image(image)
        except ValueError as error:raise FusionError('INVALID_ARGUMENT',str(error)) from error
        with self._locks['control']:
            caps=self.get_preset_capabilities()
            if slot>=caps['mode_slots']:raise FusionError('NOT_SUPPORTED','Trajectory slot exceeds device mode capacity')
            if info['format'] not in caps.get('supported_formats',['MFT1']):raise FusionError('NOT_SUPPORTED','Device does not support this executable format')
            if len(image)>caps['ram_slot_bytes']:raise FusionError('INVALID_ARGUMENT','Executable image exceeds device RAM slot')
            upload=self._ask('control','preset_upload_begin',dict(slot=slot,size=len(image),crc32=int(info['crc32'],16),
                             **({'format_version':2} if info['version']==2 else {})))
            token=upload['token']
            try:
                for offset in range(0,len(image),42):
                    self._ask('control','preset_upload_chunk',dict(token=token,offset=offset,data=bytes(image[offset:offset+42]).hex()))
                self._ask('control','preset_upload_commit',dict(token=token))
                deadline=time.perf_counter()+self.timeout
                while True:
                    status=self.get_preset_status()
                    if status['last_apply_token']==token and status['upload_state']==0:
                        return dict(slot=slot,persistence='RAM_ONLY',state='APPLIED',**info,source=status['source'])
                    if status['upload_state']==3:raise FusionError('BAD_CRC','Device rejected executable block data')
                    if time.perf_counter()>=deadline:raise FusionError('TIMEOUT','RAM validation did not complete',{'outcome_unknown':True,'replayed':False})
                    time.sleep(.001)
            except Exception:
                with suppress(FusionError):self._ask('control','preset_upload_abort',dict(token=token))
                raise

    def start_trajectory(self,slot=0,*,expected_crc32=None,auxiliary_generation=None):
        integer(slot,'slot',0,7)
        if expected_crc32 is not None:integer(expected_crc32,'expected_crc32',0,0xffffffff)
        return self._ask('control','preset_control',dict(command=0,slot=slot,expected_crc32=expected_crc32,auxiliary_generation=auxiliary_generation))

    def stop_trajectory(self,*,auxiliary_generation=None):
        return self._ask('control','preset_control',dict(command=1,slot=0,auxiliary_generation=auxiliary_generation))

    def unload_trajectory(self,slot=0):
        integer(slot,'slot',0,7)
        return self._ask('control','preset_control',dict(command=5,slot=slot))

    def select_trajectory_mode(self,slot=0,*,direction=0,auxiliary_generation=None):
        integer(slot,'slot',0,7);integer(direction,'direction',-1,1)
        return self._ask('control','preset_control',dict(command={0:2,1:3,-1:4}[direction],slot=slot,auxiliary_generation=auxiliary_generation))

    def set_config(self, config):
        if not isinstance(config, dict) or not config or set(config) - {'default_ttl_ms', 'max_delta'}:
            raise FusionError('INVALID_ARGUMENT', 'config accepts default_ttl_ms and max_delta only')
        for name, value in config.items():
            integer(value, name, 1, TTL_MAX_MS if name == 'default_ttl_ms' else 32767)
        return self._ask('control', 'set_config', {'config': config})

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()

