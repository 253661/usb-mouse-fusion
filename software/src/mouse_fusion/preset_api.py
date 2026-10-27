"""Capability-gated preset control, ownership of bounded RAM uploads."""
import asyncio
import secrets
import struct
import time
from .contract import integer
from .errors import FusionError
from .bypass import CAP_BYPASS,SET_BYPASS,GET_BYPASS,encode_config,decode_status
from .run_policy import (CAP_RUN_POLICY,SET_RULE,GET_RULE,EXEC_CONTROL,RUN_STATUS,
                         STATUS as RUN_STATUS_BODY,encode_policy,decode_policy,PHASES,REASONS,ENUMS)
from .trajectory import (CAP_TRAJECTORY, CAP_TRIGGER, CAP_TRIGGER_EXTENDED, CAP_PRESET_STORAGE, CAP_KEYPOINTS, PRESET_CAPS, UPLOAD_BEGIN, UPLOAD_CHUNK,
                         UPLOAD_COMMIT, PRESET_CONTROL, PRESET_STATUS,CAP_MODIFIER_SELECTORS,requires_modifier_selectors)

CAPS=struct.Struct('<BBHBBIIIHB')
STATUS=struct.Struct('<7B5IqqI')
CAP_DEVICE_FLASH=0x20000
PERSISTENCE_STATUS=struct.Struct('<4B4IB')
METHODS={'get_preset_capabilities':set(),'get_preset_status':set(),'get_mode_control_status':set(),
         'set_bypass_config':{'config'},'get_bypass_status':set(),
         'preset_upload_begin':{'slot','size','crc32','format_version'},'preset_upload_chunk':{'token','offset','data'},
         'preset_upload_commit':{'token'},'preset_upload_abort':{'token'},
         'preset_control':{'command','slot','expected_crc32','auxiliary_generation'},'set_mouse_triggers':{'data'},
         'save_simulated_presets':{'safe_stopped'},'restore_simulated_presets':set(),
         'get_device_persistence':set(),'save_device_presets':{'safe_stopped','expected_ram_revision','cookie'},
         'set_run_policy':{'slot','policy'},'get_run_policy':{'slot'},'get_run_status':set(),
         'trajectory_execution':{'action','slot','duration_ms','easing','expected_crc32','auxiliary_generation'},
         'simulate_mouse_buttons':{'mask','dx','dy','wheel'}}

class PresetApi:
    def __init__(self,broker):
        self.broker=broker;self.link=broker.link;self.lock=asyncio.Lock()
        self.owner=None;self.token=0;self.deadline=0;self.generation=0

    def metadata(self):
        return dict(source=self.link.transport.kind,hardware_verified=False)

    def slot_max(self):
        return 7 if self.link.info['capabilities']&CAP_KEYPOINTS else 3

    def image_max(self):
        return 4096 if self.link.info['capabilities']&CAP_KEYPOINTS else 8192

    async def status(self,owner):
        body=await self.link.request(PRESET_STATUS,owner=owner)
        if len(body)!=STATUS.size:raise FusionError('PROTOCOL_ERROR','Wrong preset status size')
        keys=('selected','playing','active_slot','upload_state','loaded_mask','trigger_count','last_error',
              'consumed','skipped','offered','bridge_accepted','bridge_dropped','output_dx','output_dy','last_apply_token')
        result=dict(zip(keys,STATUS.unpack(body)));result['playing']=bool(result['playing'])
        return {**result,**self.metadata(), 'output_kind':'SIMULATED_HID_COMPLETED' if self.link.transport.kind=='SIMULATION' else 'HID_COMPLETED_COUNTER'}

    async def abandon(self,owner):
        async with self.lock:
            if self.owner==owner:
                if self.link.connected:
                    try:await self.link.request(0x29,struct.pack('<I',self.token))
                    except FusionError:pass
                self.owner=None;self.token=0

    def _owns(self,owner,p):
        token=integer(p.get('token'),'token',1,0xffffffff)
        if self.owner!=owner or self.token!=token or self.generation!=self.link.generation:
            raise FusionError('INVALID_ARGUMENT','Upload does not belong to this client connection')
        if time.perf_counter()>self.deadline:raise FusionError('EXPIRED','RAM upload expired; abort and begin again')
        self.deadline=time.perf_counter()+10
        return token

    async def call(self,owner,method,p):
        self.link.require(CAP_TRAJECTORY,method)
        guard=p.get('auxiliary_generation')
        if method=='get_mode_control_status':
            self.link.require(0x2000,method)
            body=await self.link.request(0x30,owner=owner)
            if len(body)!=4 or body[0]!=1:raise FusionError('PROTOCOL_ERROR','Wrong mode control status')
            return dict(selected=body[1],enabled=bool(body[2]),configured=bool(body[3]),**self.metadata())
        if guard is not None:integer(guard,'auxiliary_generation',1,0xffffffff)
        if method in ('set_bypass_config','get_bypass_status'):
            self.link.require(CAP_BYPASS,method)
            try:payload=encode_config(p.get('config')) if method=='set_bypass_config' else b''
            except ValueError as error:raise FusionError('INVALID_ARGUMENT',str(error)) from error
            body=await self.link.request(SET_BYPASS if method=='set_bypass_config' else GET_BYPASS,payload,owner=owner)
            try:result=decode_status(body)
            except ValueError as error:raise FusionError('PROTOCOL_ERROR',str(error)) from error
            return dict(**result,persistence='RAM_ONLY',physical_button_policy='passthrough',**self.metadata())
        if method=='simulate_mouse_buttons':
            from .preset_simulator import PresetSimulatorTransport
            if not isinstance(self.link.transport,PresetSimulatorTransport):
                raise FusionError('NOT_SUPPORTED','Virtual button ingress requires the production-C simulator')
            mask=integer(p.get('mask'),'mask',0,31)
            dx=integer(p.get('dx',0),'dx',-32768,32767);dy=integer(p.get('dy',0),'dy',-32768,32767)
            wheel=integer(p.get('wheel',0),'wheel',-32768,32767)
            self.link.transport.inject_physical(mask,dx,dy,wheel=wheel)
            return dict(mask=mask,dx=dx,dy=dy,wheel=wheel,virtual_input=True,**self.metadata())
        if method in ('set_run_policy','get_run_policy','get_run_status','trajectory_execution'):
            self.link.require(CAP_RUN_POLICY,method)
            if method=='get_run_status':
                body=await self.link.request(RUN_STATUS,owner=owner)
                if len(body)!=RUN_STATUS_BODY.size:raise FusionError('PROTOCOL_ERROR','Wrong execution status size')
                v=RUN_STATUS_BODY.unpack(body)
                try:phase,reason,mode=PHASES[v[0]],REASONS[v[3]],ENUMS['run_mode'][v[5]]
                except IndexError as error:raise FusionError('PROTOCOL_ERROR','Invalid execution state') from error
                keys=('cycles','elapsed_ms','return_skipped','run_serial','consumed','skipped','preset_x','preset_y')
                return dict(zip(keys,v[7:]),phase=phase,slot=v[1],target_slot=v[2],reason=reason,
                            position_known=bool(v[4]),run_mode=mode,held=bool(v[6]),**self.metadata())
            if method=='trajectory_execution':
                actions=('stop','keep','return','switch');action=p.get('action')
                if action not in actions:raise FusionError('INVALID_ARGUMENT','Unknown execution action')
                slot=integer(p.get('slot',0),'slot',0,self.slot_max())
                ms=integer(p.get('duration_ms',0),'duration_ms',0,60000)
                easing=p.get('easing','smoothstep')
                if easing not in ENUMS['return_easing']:raise FusionError('INVALID_ARGUMENT','Invalid easing')
                crc=p.get('expected_crc32')
                if crc is not None:integer(crc,'expected_crc32',0,0xffffffff)
                body=struct.pack('<BBHBBI',actions.index(action),slot,ms,ENUMS['return_easing'].index(easing),crc is not None,crc or 0)
                await self.link.request(EXEC_CONTROL,body,owner=owner,auxiliary_generation=guard)
                return dict(action=action,slot=slot,replayed=False,**self.metadata())
            slot=integer(p.get('slot'),'slot',0,self.slot_max())
            if method=='set_run_policy':
                try:wire=encode_policy(p.get('policy'))
                except (ValueError,TypeError) as error:raise FusionError('INVALID_ARGUMENT',str(error)) from error
                await self.link.request(SET_RULE,bytes([slot])+wire,owner=owner)
                return dict(slot=slot,policy=decode_policy(wire),schema=1,persistence='RAM_ONLY',**self.metadata())
            body=await self.link.request(GET_RULE,bytes([slot]),owner=owner)
            try:
                if len(body)!=29 or body[0]!=slot:raise ValueError('Wrong run-rule response')
                policy=decode_policy(body[1:])
            except (ValueError,struct.error) as error:raise FusionError('PROTOCOL_ERROR',str(error)) from error
            return dict(slot=slot,policy=policy,schema=1,**self.metadata())
        if method in ('get_device_persistence','save_device_presets'):
            self.link.require(CAP_DEVICE_FLASH,method)
            payload=b''
            if method=='save_device_presets':
                if p.get('safe_stopped') is not True:raise FusionError('UNSAFE','Explicit safe_stopped=True is required')
                revision=integer(p.get('expected_ram_revision'),'expected_ram_revision',1,0xffffffff)
                cookie=integer(p.get('cookie'),'cookie',1,0xffffffff)
                payload=struct.pack('<BII',1,revision,cookie)
            async with self.lock:body=await self.link.request(0x32 if payload else 0x31,payload,owner=owner)
            if len(body)!=PERSISTENCE_STATUS.size:raise FusionError('PROTOCOL_ERROR','Wrong device persistence status size')
            version,phase,boot,last,generation,size,revision,cookie,bank=PERSISTENCE_STATUS.unpack(body)
            if version!=1 or phase>2 or bank not in (0,1,255):raise FusionError('PROTOCOL_ERROR','Invalid device persistence state')
            return dict(phase=('idle','pending','writing')[phase],boot_result=boot,last_result=last,generation=generation,
                        payload_bytes=size,ram_revision=revision,cookie=cookie,bank=bank,
                        request_accepted=bool(payload),confirmed=False,persistence='DEVICE_FLASH' if self.link.transport.kind=='LIVE_COM' else 'SIMULATED_DEVICE_FLASH',**self.metadata())
        if method in ('save_simulated_presets','restore_simulated_presets'):
            self.link.require(CAP_PRESET_STORAGE,method)
            if self.link.transport.kind!='SIMULATION':raise FusionError('NOT_SUPPORTED','Real flash writes are not implemented or authorized')
            if method=='save_simulated_presets' and p.get('safe_stopped') is not True:raise FusionError('UNSAFE','Explicit safe_stopped=True required for simulated NOR save')
            async with self.lock:
                body=await self.link.request(0x27 if method=='save_simulated_presets' else 0x28,b'\x01',owner=owner)
                if method=='restore_simulated_presets' and self.link.info['capabilities']&CAP_BYPASS:
                    await self.link.request(GET_BYPASS,owner=owner)
            if len(body)!=9:raise FusionError('PROTOCOL_ERROR','Wrong preset storage response')
            generation,size,bank=struct.unpack('<IIB',body)
            return dict(generation=generation,payload_bytes=size,bank=bank,persistence='SIMULATED_NOR_ONLY',
                        **({'auxiliary_generation':self.link.auxiliary_generation} if self.link.info['capabilities']&CAP_BYPASS else {}),
                        bank_capacity_bytes=786432-4096,**self.metadata())
        if method=='set_mouse_triggers':
            self.link.require(CAP_TRIGGER,method)
            data=p.get('data')
            if not isinstance(data,str) or len(data)>384:raise FusionError('INVALID_ARGUMENT','At most 16 trigger records allowed')
            try:wire=bytes.fromhex(data)
            except ValueError as e:raise FusionError('INVALID_ARGUMENT','Invalid trigger record hex') from e
            if len(wire)%12:raise FusionError('INVALID_ARGUMENT','Trigger record length must be 12 bytes')
            if any(wire[i+2]>=5 for i in range(0,len(wire),12)):self.link.require(0x2000,method)
            if any(wire[i]&96 or wire[i+2]>=7 for i in range(0,len(wire),12)):self.link.require(CAP_TRIGGER_EXTENDED,method)
            if requires_modifier_selectors(wire):self.link.require(CAP_MODIFIER_SELECTORS,method)
            async with self.lock:
                count=len(wire)//12
                await self.link.request(0x26,bytes([1,0,count,0]),owner=owner)
                for start in range(0,count,3):
                    n=min(3,count-start)
                    await self.link.request(0x26,bytes([1,1,start,n])+wire[start*12:(start+n)*12],owner=owner)
                await self.link.request(0x26,bytes([1,2,0,0]),owner=owner)
                if self.link.info['capabilities']&CAP_BYPASS:await self.link.request(GET_BYPASS,owner=owner)
            return dict(mouse_trigger_count=count,persistence='RAM_ONLY',keyboard_scope='APP_LOCAL',
                        **({'auxiliary_generation':self.link.auxiliary_generation} if self.link.info['capabilities']&CAP_BYPASS else {}),**self.metadata())
        if method=='get_preset_capabilities':
            body=await self.link.request(PRESET_CAPS,owner=owner)
            if len(body)!=CAPS.size:raise FusionError('PROTOCOL_ERROR','Wrong preset capabilities size')
            keys=('format_version','mode_slots','ram_slot_bytes','max_mouse_triggers','trigger_schema',
                  'flash_bytes','firmware_reserved_bytes','bank_bytes','bank_metadata_bytes','storage_available')
            result=dict(zip(keys,CAPS.unpack(body)))
            if result['format_version'] not in (1,2):raise FusionError('NOT_SUPPORTED','Executable data version unsupported')
            if result['format_version']==2 and not self.link.info['capabilities']&CAP_KEYPOINTS:raise FusionError('PROTOCOL_ERROR','Keypoint format missing capability')
            if not 1<=result['mode_slots']<=8 or not 41<=result['ram_slot_bytes']<=8192:raise FusionError('PROTOCOL_ERROR','Invalid preset capacity')
            result['supported_formats']=['MFT1','MFT2'] if result['format_version']==2 else ['MFT1']
            return {**result,**self.metadata(),'persistence':'RAM_ONLY',
                    'real_flash_write':'EXPLICIT_MAINTENANCE' if self.link.info['capabilities']&CAP_DEVICE_FLASH else 'NOT_SUPPORTED'}
        if method=='get_preset_status':return await self.status(owner)
        if method=='preset_control':
            command=integer(p.get('command'),'command',0,5);slot=integer(p.get('slot'),'slot',0,self.slot_max())
            body=bytes([command,slot]);expected=p.get('expected_crc32')
            if expected is not None:
                if command!=0:raise FusionError('INVALID_ARGUMENT','CRC guard only applies to start')
                body+=struct.pack('<I',integer(expected,'expected_crc32',0,0xffffffff))
            await self.link.request(PRESET_CONTROL,body,owner=owner,auxiliary_generation=guard)
            return dict(command=command,slot=slot,replayed=False,**self.metadata())
        async with self.lock:
            self.broker.check(owner)
            if method=='preset_upload_begin':
                if self.owner and self.generation==self.link.generation:
                    status=await self.status(owner)
                    if status['upload_state'] not in (1,2) and status['last_apply_token']==self.token:
                        self.owner=None
                    elif time.perf_counter()>self.deadline:
                        await self.link.request(0x29,struct.pack('<I',self.token),owner=owner);self.owner=None
                    else:raise FusionError('BUSY','Another RAM upload is in progress')
                version=integer(p.get('format_version',1),'format_version',1,2)
                if version==2:self.link.require(CAP_KEYPOINTS,method)
                slot=integer(p.get('slot'),'slot',0,self.slot_max());size=integer(p.get('size'),'size',41,self.image_max())
                crc=integer(p.get('crc32'),'crc32',0,0xffffffff);token=secrets.randbits(32) or 1
                self.owner=owner;self.token=token;self.generation=self.link.generation;self.deadline=time.perf_counter()+10
                try:
                    await self.link.request(UPLOAD_BEGIN,struct.pack('<IBHI',token,slot,size,crc),owner=owner)
                except FusionError as error:
                    if error.details.get('outcome_unknown') is False:self.owner=None;self.token=0
                    raise
                # A cancelled/unknown BEGIN retains tentative ownership so
                # disconnect/expiry can retire its possibly accepted staging.
                return dict(token=token,chunk_bytes=42,**self.metadata())
            token=self._owns(owner,p)
            if method=='preset_upload_chunk':
                offset=integer(p.get('offset'),'offset',0,self.image_max()-1);data=p.get('data')
                if not isinstance(data,str) or len(data)>84:raise FusionError('INVALID_ARGUMENT','Chunk must contain 1..42 hex bytes')
                try:body=bytes.fromhex(data)
                except ValueError as e:raise FusionError('INVALID_ARGUMENT','Invalid hex chunk') from e
                if not 1<=len(body)<=42:raise FusionError('INVALID_ARGUMENT','Chunk must contain 1..42 bytes')
                await self.link.request(UPLOAD_CHUNK,struct.pack('<IH',token,offset)+body,owner=owner)
                return dict(received_offset=offset+len(body),**self.metadata())
            if method=='preset_upload_commit':
                await self.link.request(UPLOAD_COMMIT,struct.pack('<I',token),owner=owner)
                return dict(token=token,state='VALIDATING',persistence='RAM_ONLY',**self.metadata())
            if method=='preset_upload_abort':
                await self.link.request(0x29,struct.pack('<I',token),owner=owner);self.owner=None;self.token=0
                return dict(aborted=True,**self.metadata())
        raise FusionError('INVALID_ARGUMENT','Unknown preset method')
