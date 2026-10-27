"""Loopback-only API v1 service with offline defaults and opt-in exclusive COM."""
import argparse
import asyncio
from contextlib import suppress
import json
from pathlib import Path
import secrets
import struct
import time

from . import _j4 as j4
from .contract import (API_VERSION, AUX_BODY, AUX_STATUS, CAP_CLOCK, CAP_CONFIG,
                       CAP_MODE, CAP_MOVE, CONFIG_BODY, GET_CONFIG, MAX_CLIENTS,
                       MAX_LINE, SET_CONFIG, SET_MODE, TTL_MAX_MS, config_values,
                       integer, parse_status)
from .errors import FusionError
from .link import DeviceLink
from .simulator import SimulatedTransport
from .candidate03 import Candidate03Transport
from .serial_transport import SerialTransport
from .preset_api import PresetApi, METHODS as PRESET_METHODS
from .preset_simulator import PresetSimulatorTransport
from .ai_gate import CAP_AI_GATE,CAP_AI_SMOOTH,GET_AI_GATE,CANCEL_SMOOTH,normalize_gate,decode_gate


class DeviceBroker:
    def __init__(self, link):
        self.link = link
        self.clients = {}
        self.connection_lock = asyncio.Lock()
        self.settings_lock = asyncio.Lock()
        self.config = None
        self.presets = PresetApi(self)

    def device_info(self):
        info = dict(self.link.info or {})
        caps = info.get('capabilities', 0)
        features = {'get_device_info': 'AVAILABLE', 'get_status': 'AVAILABLE'}
        for name, bit in (('move', CAP_MOVE | CAP_CLOCK), ('set_mode', CAP_MODE),
                          ('get_config', CAP_CONFIG), ('set_config', CAP_CONFIG),
                          ('load_trajectory',0x100),('start_trajectory',0x100),
                          ('get_preset_status',0x100),('set_mouse_triggers',0x200),
                          ('save_simulated_presets',0x400),('set_run_policy',0x800),('get_run_status',0x800),
                          ('set_bypass_config',0x1000),('get_bypass_status',0x1000),
                          ('get_ai_gate_status',CAP_AI_GATE),('move_when_left_held',CAP_AI_GATE|CAP_MOVE|CAP_CLOCK),
                          ('move_smoothed_when_left_held',CAP_AI_SMOOTH|CAP_MOVE|CAP_CLOCK),('cancel_smoothed_move',CAP_AI_SMOOTH)):
            features[name] = ('AVAILABLE_UNVERIFIED' if self.link.transport.kind == 'LIVE_COM'
                              else 'SIMULATION_ONLY') if caps & bit == bit else 'NOT_SUPPORTED'
        info.update(source=self.link.transport.kind, hardware_verified=False,
                    api_version=API_VERSION, features=features,
                    extension_revision=self.link.extension_revision)
        if caps&0x1000:info['auxiliary_generation']=self.link.auxiliary_generation
        if caps&0x80000:info['gated_move_scheduling']='CURRENT_OR_NEXT_1MS_CYCLE'
        return info

    async def connect(self):
        async with self.connection_lock:
            if len(self.clients) >= MAX_CLIENTS:
                raise FusionError('BUSY', 'Maximum number of clients reached')
            fresh = not self.link.connected
            if fresh:
                await self.link.open()
            if self.link.info['capabilities'] & CAP_CONFIG:
                if fresh or self.config is None:
                    async with self.settings_lock:
                        self.config = await self._get_config()
            else:
                self.config = None
            owner = secrets.token_hex(16)
            self.clients[owner] = {'generation': self.link.generation, 'last_sequence': 0, 'rt_attached': False}
            return {'client_id': owner, 'device_info': self.device_info()}

    def check(self, owner, *, allow_disconnected=False):
        lease = self.clients.get(owner)
        if lease is None:
            raise FusionError('NOT_CONNECTED', 'Connect a client first')
        if lease['generation'] != self.link.generation or (not self.link.connected and not allow_disconnected):
            raise FusionError('DEVICE_DISCONNECTED', 'Explicit connect() is required; moves are never replayed',
                              {'replayed': False, 'outcome_unknown': False})
        return lease

    async def disconnect(self, owner):
        async with self.connection_lock:
            await self.presets.abandon(owner)
            if self.clients.pop(owner, None) is not None:
                self.link.cancel_owner(owner)
            if not self.clients:
                await self.link.close_session()
        return {'disconnected': True}

    async def _get_config(self, owner=''):
        body = await self.link.request(GET_CONFIG, owner=owner)
        if len(body) != CONFIG_BODY.size:
            raise FusionError('PROTOCOL_ERROR', 'Wrong GET_CONFIG size')
        mode, ttl, maximum = CONFIG_BODY.unpack(body)
        if mode > 1 or not 1 <= ttl <= TTL_MAX_MS or not 1 <= maximum <= 32767:
            raise FusionError('PROTOCOL_ERROR', 'Invalid device RAM configuration')
        return {'mode_id': mode, 'default_ttl_ms': ttl, 'max_delta': maximum,
                'persistence': 'RAM_ONLY', 'source': self.link.transport.kind, 'hardware_verified': False}

    async def call(self, owner, method, params):
        lease = self.check(owner, allow_disconnected=method in ('get_status', 'get_device_info'))
        if method in PRESET_METHODS:
            return await self.presets.call(owner,method,params)
        if method == 'get_device_info':
            return self.device_info()
        if method=='get_ai_gate_status':
            self.link.require(CAP_AI_GATE,method)
            return decode_gate(await self.link.request(GET_AI_GATE,owner=owner))
        if method == 'get_status':
            result = {'connection': 'connected' if self.link.connected else 'disconnected',
                      'source': self.link.transport.kind, 'hardware_verified': False,
                      'mode_id': self.config['mode_id'] if self.config else None,
                      'error': self.link.error,
                      'service': {'clients': len(self.clients), 'control_queue_depth': len(self.link.controls),
                                  'realtime_slot_depth': int(self.link.latest_move is not None),
                                  'control_queue_limit': self.link.control_limit,
                                  'realtime_slot_limit': 1, **self.link.stats}}
            if self.link.connected:
                result['device'] = parse_status(await self.link.request(j4.STATUS, owner=owner))
                if self.link.info['capabilities'] & CAP_MOVE:
                    body = await self.link.request(AUX_STATUS, owner=owner)
                    if len(body) != AUX_BODY.size:
                        raise FusionError('PROTOCOL_ERROR', 'Wrong AUX_STATUS size')
                    values = AUX_BODY.unpack(body)
                    counter = 'accepted' if self.link.extension_revision == 2 else 'applied'
                    result['auxiliary'] = dict(zip(('mode_id', counter, 'dropped', 'last_sequence', 'last_dx', 'last_dy'), values))
            return result
        if method == 'get_config':
            self.link.require(CAP_CONFIG, method)
            async with self.settings_lock:
                self.check(owner)
                self.config = await self._get_config(owner)
                return dict(self.config)
        if method == 'set_mode':
            mode = integer(params.get('mode_id'), 'mode_id', 0, 1)
            self.link.require(CAP_MODE, method)
            async with self.settings_lock:
                self.check(owner)
                current, self.config = self.config, None
                if await self.link.request(SET_MODE, bytes([mode]), owner=owner) != bytes([mode]):
                    raise FusionError('PROTOCOL_ERROR', 'SET_MODE echo mismatch')
                if current:
                    self.config = {**current, 'mode_id': mode}
            return {'mode_id': mode, 'source': self.link.transport.kind, 'hardware_verified': False}
        if method == 'set_config':
            self.link.require(CAP_CONFIG, method)
            async with self.settings_lock:
                self.check(owner)
                current = await self._get_config(owner)
                ttl, maximum = config_values(params.get('config'), current)
                self.check(owner)
                # Device RAM may change before its ACK arrives. Cancellation
                # must not leave other clients using an obsolete default TTL.
                self.config = None
                body = await self.link.request(SET_CONFIG, struct.pack('<HH', ttl, maximum), owner=owner)
                if body != CONFIG_BODY.pack(current['mode_id'], ttl, maximum):
                    raise FusionError('PROTOCOL_ERROR', 'SET_CONFIG echo mismatch')
                self.config = {**current, 'default_ttl_ms': ttl, 'max_delta': maximum}
            return dict(self.config)
        if method=='cancel_smoothed_move':
            self.link.require(CAP_AI_SMOOTH,method)
            cutoff=integer(params.get('cutoff_ns'),'cutoff_ns',0,time.perf_counter_ns())
            lease['cancelled_before_ns']=max(lease.get('cancelled_before_ns',0),cutoff)
            self.link.cancel_owner_motion(owner)
            body=await self.link.request(CANCEL_SMOOTH,owner=owner)
            if len(body)!=4:raise FusionError('PROTOCOL_ERROR','Wrong smooth cancel ACK')
            return dict(cancelled=True,device_sequence_barrier=struct.unpack('<I',body)[0])
        if method in ('move','move_when_left_held','move_smoothed_when_left_held'):
            gate=None
            smooth_ms=None
            if method in ('move_when_left_held','move_smoothed_when_left_held'):
                self.link.require(CAP_AI_GATE,method);gate=normalize_gate(params.get('gate'))
            if method=='move_smoothed_when_left_held':
                self.link.require(CAP_AI_SMOOTH,method);smooth_ms=integer(params.get('duration_ms'),'duration_ms',20,40)
            dx = integer(params.get('dx'), 'dx', -32768, 32767)
            dy = integer(params.get('dy'), 'dy', -32768, 32767)
            self.link.require(CAP_MOVE | CAP_CLOCK, method)
            sequence = integer(params.get('sequence'), 'sequence', 1, 0xffffffff)
            ttl = params.get('ttl_ms')
            if ttl is None:
                if self.config is None and self.link.info['capabilities'] & CAP_CONFIG:
                    raise FusionError('CONFIG_UNSYNCED', 'Read get_config() before using the default TTL')
                ttl = self.config['default_ttl_ms'] if self.config else 20
            integer(ttl, 'ttl_ms', 1, TTL_MAX_MS)
            sent_ns = integer(params.get('sent_ns'), 'sent_ns', 0, 0x7fffffffffffffff)
            now = time.perf_counter_ns()
            if sent_ns > now:
                raise FusionError('INVALID_ARGUMENT', 'sent_ns must use this computer\'s QPC/perf_counter_ns clock')
            if sequence <= lease['last_sequence']:
                raise FusionError('STALE_SEQUENCE', 'Movement sequence must increase within a client connection')
            lease['last_sequence'] = sequence
            if sent_ns<=lease.get('cancelled_before_ns',0):raise FusionError('CANCELLED','Motion was invoked before the last cancel barrier',{'replayed':False})
            if self.config and (abs(dx) > self.config['max_delta'] or abs(dy) > self.config['max_delta']):
                raise FusionError('INVALID_ARGUMENT', 'Movement exceeds configured max_delta')
            guard=params.get('auxiliary_generation')
            if guard is not None:integer(guard,'auxiliary_generation',1,0xffffffff)
            result = await self.link.move(owner, dx, dy, sent_ns + ttl * 1000000,auxiliary_generation=guard,left_gate=gate,smooth_ms=smooth_ms)
            return {'sequence': sequence, **result}
        raise FusionError('INVALID_ARGUMENT', 'Unknown API method')


METHOD_PARAMS = {'get_device_info': set(), 'get_status': set(), 'get_config': set(),
                 'set_mode': {'mode_id'}, 'set_config': {'config'},
                 'move': {'dx', 'dy', 'sequence', 'sent_ns', 'ttl_ms','auxiliary_generation'}}
METHOD_PARAMS.update(PRESET_METHODS)
METHOD_PARAMS['get_ai_gate_status']=set()
METHOD_PARAMS['move_when_left_held']=METHOD_PARAMS['move']|{'gate'}
METHOD_PARAMS['move_smoothed_when_left_held']=METHOD_PARAMS['move_when_left_held']|{'duration_ms'}
METHOD_PARAMS['cancel_smoothed_move']={'cutoff_ns'}


class Service:
    def __init__(self, transport, board_id, *, token=None, control_limit=16):
        self.token = token or secrets.token_urlsafe(32)
        if not isinstance(self.token, str) or not self.token.isascii():
            raise ValueError('Local service token must be an ASCII string')
        self.link = DeviceLink(transport, board_id, control_limit=control_limit)
        self.broker = DeviceBroker(self.link)
        self.server = None
        self.port = None
        self.writers, self.handlers = set(), set()

    async def start(self, port=8765):
        integer(port, 'port', 0, 65535)
        self.server = await asyncio.start_server(self._handle, '127.0.0.1', port, limit=MAX_LINE)
        self.port = self.server.sockets[0].getsockname()[1]
        self.link.start()
        return self

    async def close(self):
        if self.server:
            self.server.close()
        for writer in tuple(self.writers):
            writer.close()
        await self.link.stop()
        if self.handlers:
            for handler in tuple(self.handlers):
                handler.cancel()
            await asyncio.gather(*tuple(self.handlers), return_exceptions=True)
        if self.server:
            await self.server.wait_closed()

    def descriptor(self):
        return {'api_version': API_VERSION, 'host': '127.0.0.1', 'port': self.port, 'token': self.token,
                'backend': self.link.transport.kind, 'hardware_verified': False}

    async def _call_connected(self, reader, owner, method, params):
        # One outstanding request per socket. Observe EOF while the broker is
        # waiting for a lock or J4 response, so abandoned work is cancelled too.
        operation = asyncio.create_task(self.broker.call(owner, method, params))
        following = asyncio.create_task(reader.read(1))
        try:
            done, _ = await asyncio.wait((operation, following), return_when=asyncio.FIRST_COMPLETED)
            if following in done:
                raise ConnectionError('Client closed its lane or sent pipelined requests')
            return await operation
        finally:
            for pending in (operation, following):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(operation, following, return_exceptions=True)

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        self.writers.add(writer)
        owner, lane = None, None
        try:
            if len(self.writers) > MAX_CLIENTS * 2:
                return
            while True:
                request_id, close_after = None, False
                try:
                    line = await reader.readline()
                    if not line:
                        break
                    if len(line) > MAX_LINE:
                        raise FusionError('INVALID_ARGUMENT', 'IPC message exceeds 4096 bytes')
                    try:
                        request = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
                    except (ValueError, UnicodeError) as error:
                        raise FusionError('INVALID_ARGUMENT', 'Malformed JSON message') from error
                    if not isinstance(request, dict):
                        raise FusionError('INVALID_ARGUMENT', 'Request must be an object')
                    request_id = integer(request.get('id'), 'id', 1, 0xffffffff)
                    if type(request.get('api_version')) is not int or request['api_version'] != API_VERSION:
                        raise FusionError('API_VERSION_MISMATCH', 'This service supports API version 1')
                    token = request.get('token')
                    if not isinstance(token, str) or not token.isascii() or not secrets.compare_digest(token, self.token):
                        close_after = True
                        raise FusionError('AUTH_FAILED', 'Invalid local service token')
                    method, params = request.get('method'), request.get('params', {})
                    if not isinstance(method, str) or not isinstance(params, dict):
                        raise FusionError('INVALID_ARGUMENT', 'method must be a string and params an object')
                    if owner is None:
                        if method == 'connect' and not params:
                            result = await self.broker.connect()
                            owner, lane = result['client_id'], 'control'
                        elif method == 'attach_realtime' and set(params) == {'client_id'} and isinstance(params['client_id'], str):
                            lease = self.broker.check(params['client_id'])
                            if lease['rt_attached']:
                                raise FusionError('BUSY', 'Realtime lane is already attached')
                            owner, lane = params['client_id'], 'realtime'
                            lease['rt_attached'] = True
                            result = {'attached': True}
                        else:
                            raise FusionError('NOT_CONNECTED', 'First request must connect or attach_realtime')
                    elif method == 'disconnect' and lane == 'control' and not params:
                        result = await self.broker.disconnect(owner)
                        close_after = True
                    elif method in METHOD_PARAMS and not set(params) - METHOD_PARAMS[method]:
                        if (method in ('move','move_when_left_held','move_smoothed_when_left_held')) != (lane == 'realtime'):
                            raise FusionError('INVALID_ARGUMENT', 'Use the correct logical channel for this method')
                        result = await self._call_connected(reader, owner, method, params)
                    else:
                        raise FusionError('INVALID_ARGUMENT', 'Unknown method or parameters')
                    response = {'api_version': API_VERSION, 'id': request_id, 'ok': True, 'result': result}
                except (ValueError, FusionError) as error:
                    if isinstance(error, FusionError):
                        failure = error
                    else:
                        failure = FusionError('INVALID_ARGUMENT', 'IPC message exceeds the framing limit')
                        close_after = True
                    response = {'api_version': API_VERSION, 'id': request_id, 'ok': False, 'error': failure.as_dict()}
                writer.write((json.dumps(response, separators=(',', ':')) + '\n').encode('utf-8'))
                await writer.drain()
                if close_after:
                    break
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            if owner is not None:
                if lane == 'control':
                    await self.broker.disconnect(owner)
                elif owner in self.broker.clients:
                    self.broker.clients[owner]['rt_attached'] = False
                    self.link.cancel_owner(owner)
            writer.close()
            with suppress(ConnectionError, OSError):
                await writer.wait_closed()
            self.writers.discard(writer)
            self.handlers.discard(task)


async def run(args):
    if args.live_com:
        transport = SerialTransport(args.live_com, hardware_authorized=args.authorize_device_access)
    elif args.preset_c_simulator:
        transport = PresetSimulatorTransport(args.preset_c_simulator,store_file=args.simulated_store_file,device_flash=args.simulate_device_flash)
    elif args.c_simulator:
        # Existing executable calls production j4_link.c. No real port is opened.
        transport = Candidate03Transport(args.c_simulator)
    else:
        transport = SimulatedTransport(profile=args.simulate, status_delay_ms=args.simulate_status_delay_ms,
                                       move_reply_delay_ms=args.simulate_move_reply_delay_ms,
                                       drop_after_moves=args.simulate_drop_after_moves, fault=args.simulate_fault)
    service = Service(transport, args.board_id)
    endpoint = Path(args.endpoint)
    try:
        await service.start(args.port)
        endpoint.parent.mkdir(parents=True, exist_ok=True)
        endpoint.write_text(json.dumps(service.descriptor(), indent=2), encoding='utf-8')
        endpoint.chmod(0o600)
        print(json.dumps({'ready': True, 'endpoint': str(endpoint.resolve()), 'port': service.port,
                          'backend': transport.kind,
                          'device_access': 'EXPLICIT_COM' if args.live_com else 'NONE',
                          'hardware': 'NOT TESTED'}), flush=True)
        await asyncio.Event().wait()
    finally:
        await service.close()
        with suppress(OSError, ValueError):
            if json.loads(endpoint.read_text(encoding='utf-8')).get('token') == service.token:
                endpoint.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--simulate', choices=('fusion', 'candidate03'), default='fusion')
    mode.add_argument('--c-simulator', '--candidate03-simulator', dest='c_simulator', type=Path)
    mode.add_argument('--preset-c-simulator', type=Path, help='New offline production-C preset peer; never opens a COM port')
    parser.add_argument('--simulated-store-file', type=Path, help='Virtual NOR image; only with --preset-c-simulator')
    parser.add_argument('--simulate-device-flash',action='store_true',help='Explicit virtual maintenance/reboot flash; only with production C simulator')
    mode.add_argument('--live-com', help='Exact COM port; requires separate hardware stage authorization')
    parser.add_argument('--authorize-device-access', action='store_true',
                        help='Use only after the hardware stage has been explicitly authorized')
    parser.add_argument('--endpoint', default='fusion-service.json')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--board-id', help='Required exact 16-digit Flash board ID for live COM')
    parser.add_argument('--simulate-status-delay-ms', type=int, default=0)
    parser.add_argument('--simulate-move-reply-delay-ms', type=int, default=0)
    parser.add_argument('--simulate-drop-after-moves', type=int)
    parser.add_argument('--simulate-fault', choices=('clock_reply', 'move_reply'))
    args = parser.parse_args(argv)
    if args.simulated_store_file and not args.preset_c_simulator:
        parser.error('--simulated-store-file requires --preset-c-simulator')
    if args.simulate_device_flash and not args.preset_c_simulator:parser.error('--simulate-device-flash requires --preset-c-simulator')
    if args.live_com:
        if not args.authorize_device_access:
            parser.error('Live COM access requires separate hardware stage authorization')
        if not args.board_id:
            parser.error('Live COM access requires an explicit --board-id')
    elif args.authorize_device_access:
        parser.error('--authorize-device-access is only valid with --live-com')
    args.board_id = args.board_id or '0000000000000000'
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, FusionError) as error:
        print(str(error), file=__import__('sys').stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
