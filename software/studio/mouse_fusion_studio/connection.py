"""Bounded APP worker. All device operations use the unchanged Python SDK."""
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
import mouse_fusion
from mouse_fusion import FusionClient, FusionError


class StudioConnection:
    def __init__(self, *, runtime_dir=None, endpoint=None, c_simulator=None, preset_c_simulator=None, status_delay_ms=0,
                 allow_live_device=False, live_com=None, board_id=None,simulate_device_flash=False):
        if live_com is not None:
            if not allow_live_device or not re.fullmatch(r'COM[1-9][0-9]*',live_com,re.I):
                raise ValueError('实机授权及精确 COM 编号必需；禁止自动扫描')
            if not isinstance(board_id,str) or not re.fullmatch(r'[0-9a-fA-F]{16}',board_id):
                raise ValueError('必须指定准确的 16 位 Flash board ID')
            if endpoint or c_simulator or preset_c_simulator or status_delay_ms:
                raise ValueError('实机 COM 不可与外部端点或模拟后端混用')
        self.allow_live_device=allow_live_device
        self.live_com=live_com;self.board_id=board_id
        self.runtime_dir = Path(runtime_dir or Path.cwd()/'output'/'studio-runtime'/uuid.uuid4().hex)
        self.endpoint = Path(endpoint) if endpoint else self.runtime_dir/'service.json'
        self.external = endpoint is not None
        self.c_simulator = c_simulator
        self.preset_c_simulator = preset_c_simulator
        self.simulate_device_flash=simulate_device_flash;self.flash_busy=False
        self.status_delay_ms=status_delay_ms
        self.process = None
        self.client = FusionClient(endpoint=self.endpoint,timeout=1)
        self.requests, self.results = queue.Queue(maxsize=16), queue.Queue()
        self.motion_requests=queue.Queue(maxsize=1)
        self.lifecycle_lock=threading.Lock()
        self._motion_lock=threading.Lock()
        self._process_lock=threading.Lock()
        self._generation=0; self._motion_enabled=False; self._move_ttl_ms=40
        self._auxiliary_generation=None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run,name='Studio SDK worker',daemon=True)
        self._thread.start()
        self._realtime=threading.Thread(target=self._run_motion,name='Studio realtime SDK worker',daemon=True)
        self._realtime.start()

    def submit(self, method, *args, **kwargs):
        if self._stop.is_set(): raise ValueError('通信线程已关闭')
        if method not in ('connect','disconnect','get_device_info','get_status','move','set_mode','get_config','set_config',
                          'get_preset_capabilities','get_preset_status','get_mode_control_status','load_trajectory','start_trajectory',
                          'stop_trajectory','unload_trajectory','select_trajectory_mode','set_mouse_triggers','save_simulated_presets',
                          'restore_simulated_presets','apply_project_ram','get_run_status','get_run_policy','set_run_policy',
                          'stop_trajectory_keep','return_trajectory','switch_trajectory','simulate_mouse_buttons','set_bypass_config','get_bypass_status'):
            if method not in ('get_device_persistence','save_project_flash'):
                raise ValueError('未知开放API方法')
        if self.flash_busy and method not in ('get_device_info','get_status','get_device_persistence','get_preset_status','get_run_status','get_bypass_status'):
            raise ValueError('设备正在保存Flash并重启，请等待结果')
        if method=='move':
            with self._motion_lock:
                if not self._motion_enabled:
                    self.results.put((method,None,FusionError('NOT_CONNECTED','请先显式连接本地服务'))); return
                self._discard_motion('SUPERSEDED','已被最新测试位移替换')
                self.motion_requests.put_nowait((args,time.perf_counter_ns()+self._move_ttl_ms*1_000_000,self._generation,self._auxiliary_generation))
            return
        if method in ('connect','disconnect'):
            with self._motion_lock:
                self._generation+=1; self._motion_enabled=False
                self._discard_motion('CANCELLED','会话变更，未发送的测试位移已取消')
        generation=self._generation
        if method in ('start_trajectory','stop_trajectory','return_trajectory','switch_trajectory','select_trajectory_mode'):
            kwargs.setdefault('auxiliary_generation',self._auxiliary_generation)
        try: self.requests.put_nowait((method,args,kwargs,generation))
        except queue.Full: raise ValueError('APP 请求队列已满，请等待当前操作完成')

    def _discard_motion(self,code,message):
        try:
            self.motion_requests.get_nowait()
            self.results.put(('move',None,FusionError(code,message,{'replayed':False})))
        except queue.Empty: pass

    def poll(self):
        try: return self.results.get_nowait()
        except queue.Empty: return None

    def _ensure_simulation(self):
        if self._stop.is_set(): raise ValueError('APP 已关闭')
        if not self.external and (self.process is None or self.process.poll() is not None):
            self.runtime_dir.mkdir(parents=True,exist_ok=True)
            if self.endpoint.exists(): self.endpoint.unlink()
            entry=['--service'] if getattr(sys,'frozen',False) else ['-m','mouse_fusion.service']
            cmd = [sys.executable,*entry,'--port','0','--endpoint',str(self.endpoint)]
            if self.live_com:
                cmd += ['--live-com',self.live_com,'--board-id',self.board_id,'--authorize-device-access']
            elif self.preset_c_simulator:
                cmd += ['--preset-c-simulator',str(self.preset_c_simulator),'--simulated-store-file',str(self.runtime_dir/'presets.nor')]
                if self.simulate_device_flash:cmd += ['--simulate-device-flash']
            else: cmd += ['--c-simulator',str(self.c_simulator)] if self.c_simulator else ['--simulate','fusion']
            if self.status_delay_ms: cmd += ['--simulate-status-delay-ms',str(self.status_delay_ms)]
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            env = os.environ.copy()
            sdk_root = str(Path(mouse_fusion.__file__).resolve().parent.parent)
            env['PYTHONPATH'] = sdk_root + os.pathsep + env.get('PYTHONPATH','')
            # No shell or enumeration. Live access uses one explicitly selected service owner.
            child = subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,creationflags=flags,env=env)
            with self._process_lock:
                closing=self._stop.is_set()
                if not closing: self.process=child
            if closing:
                # A late launch return keeps local ownership and reaps its child.
                self._stop_owned_process(child)
                self.endpoint.unlink(missing_ok=True)
                raise ValueError('APP 已关闭')
        deadline = time.perf_counter()+4
        while not self.endpoint.exists():
            if self._stop.is_set(): raise ValueError('APP 已关闭')
            if self.process and self.process.poll() is not None: raise ValueError('本地服务未能启动')
            if time.perf_counter() >= deadline: raise ValueError('未找到本地服务端点')
            time.sleep(.02)
        descriptor = json.loads(self.endpoint.read_text(encoding='utf-8'))
        backend=descriptor.get('backend')
        if backend=='LIVE_COM' and not self.allow_live_device:
            raise ValueError('连接实机需要实机授权入口 / -AllowLiveDevice')
        if backend not in ('SIMULATION','LIVE_COM'):
            raise ValueError('未知模拟后端或实机后端')
        if self.live_com and backend!='LIVE_COM':raise ValueError('实机入口拒绝模拟端点')

    def _run(self):
        while not self._stop.is_set():
            try: method,args,kwargs,generation = self.requests.get(timeout=.1)
            except queue.Empty: continue
            if self._stop.is_set(): break
            try:
                if generation!=self._generation and method in ('move','set_mode','set_config','load_trajectory','start_trajectory',
                    'stop_trajectory','unload_trajectory','select_trajectory_mode','set_mouse_triggers','apply_project_ram','set_run_policy',
                    'stop_trajectory_keep','return_trajectory','switch_trajectory','set_bypass_config','save_simulated_presets','save_project_flash'):
                    raise FusionError('CANCELLED','会话或Flash保存阶段已改变，旧操作已取消',{'replayed':False})
                if method in ('connect','disconnect'):
                    with self.lifecycle_lock:
                        if method=='connect': self._ensure_simulation()
                        result=getattr(self.client,method)(*args)
                        if method=='connect':
                            from mouse_fusion.bypass import CAP_BYPASS
                            self._auxiliary_generation=self.client.get_bypass_status()['generation'] if result['capabilities']&CAP_BYPASS else None
                            if result['capabilities']&0x20000:result={**result,'device_persistence':self.client.get_device_persistence()}
                            with self._motion_lock:
                                self._motion_enabled=generation==self._generation and not self._stop.is_set()
                elif method=='save_project_flash':result=self._save_project_flash(**kwargs)
                elif method=='apply_project_ram':
                    images,bindings,mapping=args;applied=[];crcs={};unloaded=[]
                    try:
                        policies=kwargs.get('run_policies')
                        bypass=kwargs.get('bypass')
                        # Capability preflight before stopping or changing any RAM slot.
                        caps=self.client.get_device_info()['capabilities']
                        from mouse_fusion.trajectory import inspect_image,encode_triggers,requires_modifier_selectors,CAP_MODIFIER_SELECTORS
                        device_caps=self.client.get_preset_capabilities()
                        supported=device_caps.get('supported_formats',['MFT1'])
                        if any(slot>=device_caps['mode_slots'] for slot in mapping.values()):
                            raise FusionError('NOT_SUPPORTED','工程模式数超过设备容量；尚未修改设备 RAM')
                        for slot,image in images:
                            info=inspect_image(image)
                            if slot>=device_caps['mode_slots'] or info['format'] not in supported or len(image)>device_caps['ram_slot_bytes']:
                                raise FusionError('NOT_SUPPORTED','工程轨迹格式或大小超过设备能力；MFT2/8槽需要新版固件，尚未修改设备 RAM')
                        needed=0x100|0x200|(0x800 if policies is not None else 0)
                        if any(b.get('action') in ('toggle_mode','start_current_trajectory','start_mode_trajectory') for b in bindings):needed|=0x2000
                        if any(b.get('action')=='start_mode_trajectory' or set(b.get('buttons',()))&{'wheel_up','wheel_down'} for b in bindings):needed|=0x8000
                        if requires_modifier_selectors(encode_triggers(bindings,mapping)):needed|=CAP_MODIFIER_SELECTORS
                        if caps&needed!=needed:raise FusionError('NOT_SUPPORTED','固件不支持工程所需的控制方式；模式开关/执行当前模式需要新版固件，尚未修改设备 RAM')
                        if bypass is not None and bypass.get('button') is not None and not caps&0x1000:
                            raise FusionError('NOT_SUPPORTED','当前固件不支持 BYPASS；请关闭工程旁路绑定或更新固件')
                        if policies is not None:self.client.stop_trajectory_keep()
                        if bypass is not None:
                            try:self.client.set_bypass_config(bypass)
                            except FusionError as error:
                                if error.code!='NOT_SUPPORTED' or bypass.get('button') is not None:raise
                        for slot,image in images:
                            if self._stop.is_set() or generation!=self._generation:raise ValueError('RAM 应用已取消')
                            loaded=self.client.load_trajectory(image,slot=slot);applied.append(slot);crcs[slot]=loaded['crc32']
                        for slot in range(device_caps['mode_slots']):
                            if slot not in applied:
                                if self._stop.is_set() or generation!=self._generation:raise ValueError('RAM 应用已取消')
                                self.client.unload_trajectory(slot);unloaded.append(slot)
                        if policies is not None:
                            for slot in mapping.values():self.client.set_run_policy(slot,policies[slot])
                        result=self.client.set_mouse_triggers(bindings,mode_slots=mapping)
                        if bypass is not None:
                            from mouse_fusion.bypass import CAP_BYPASS
                            if self.client.get_device_info()['capabilities']&CAP_BYPASS:
                                result['bypass']=self.client.get_bypass_status();self._auxiliary_generation=result['bypass']['generation']
                        result.update(applied_slots=applied,unloaded_slots=unloaded,applied_crcs=crcs,project_signature=kwargs.get('project_signature'))
                        if caps&0x20000:result['ram_revision']=self.client.get_device_persistence()['ram_revision']
                    except Exception as error:
                        raise ValueError(f'RAM 应用未全部完成；已应用槽 {applied}，已卸载槽 {unloaded}。{error}') from error
                else: result=getattr(self.client,method)(*args,**kwargs)
                if method in ('get_config','set_config') and 'default_ttl_ms' in result:
                    self._move_ttl_ms=result['default_ttl_ms']
                if method in ('set_bypass_config','get_bypass_status'):self._auxiliary_generation=result['generation']
                self.results.put((method,result,None))
            except Exception as error:
                if getattr(error,'details',{}).get('auxiliary_generation'):self._auxiliary_generation=error.details['auxiliary_generation']
                self.results.put((method,None,error))
        with self.lifecycle_lock: self.client.disconnect()

    def _save_project_flash(self,*,expected_ram_revision):
        # Send one save, then identity/status reads only. Accepted is not committed.
        import secrets
        self.flash_busy=True;connected=True
        with self.lifecycle_lock:
            with self._motion_lock:
                self._generation+=1;self._motion_enabled=False;self._discard_motion('CANCELLED','保存Flash，旧位移已取消')
            try:
                before=self.client.get_device_persistence()
                if before['ram_revision']!=expected_ram_revision:raise ValueError('设备RAM已被修改；请重新应用当前工程后再保存Flash')
                self.client.stop_trajectory_keep()
                cookie=secrets.randbits(32) or 1
                while cookie==before['cookie']:cookie=secrets.randbits(32) or 1
                try:
                    self.client.save_device_presets(expected_ram_revision=expected_ram_revision,cookie=cookie,safe_stopped=True)
                except FusionError as error:
                    if error.details.get('outcome_unknown') is not True:raise
                    # A lost reply can follow a successful commit. Read the same
                    # cookie after reconnect; never issue a second save request.
                self.client.disconnect();connected=False
                deadline=time.perf_counter()+30
                while not self._stop.is_set() and time.perf_counter()<deadline:
                    try:
                        info=self.client.connect();connected=True
                        status=self.client.get_device_persistence()
                    except (FusionError,OSError):
                        self.client.disconnect();connected=False;time.sleep(.15);continue
                    if status['phase']=='idle':
                        if status['boot_result']!=0 or status['cookie']!=cookie or status['generation']!=((before['generation']+1)&0xffffffff):
                            raise ValueError('设备已恢复连接，但未确认此次Flash提交；草稿保留，没有自动重写')
                        self._auxiliary_generation=self.client.get_bypass_status()['generation']
                        return {**status,'confirmed':True,'info':info}
                    time.sleep(.1)
                raise ValueError('Flash保存结果未确认；请检查连接。没有自动重写，草稿仍保留')
            finally:
                self.flash_busy=False
                with self._motion_lock:self._motion_enabled=connected and not self._stop.is_set()

    def _run_motion(self):
        while not self._stop.is_set():
            try: args,deadline,generation,auxiliary_generation=self.motion_requests.get(timeout=.05)
            except queue.Empty: continue
            try:
                with self.lifecycle_lock:
                    if self._stop.is_set() or generation!=self._generation or not self._motion_enabled:
                        raise FusionError('CANCELLED','未发送的测试位移已取消',{'replayed':False})
                    remaining=(deadline-time.perf_counter_ns())//1_000_000
                    if remaining<1: raise FusionError('EXPIRED','APP 排队位移已过期，直接丢弃',{'replayed':False})
                    result=self.client.move(*args,ttl_ms=min(100,remaining),auxiliary_generation=auxiliary_generation)
                self.results.put(('move',result,None))
            except Exception as error:
                if getattr(error,'details',{}).get('auxiliary_generation'):self._auxiliary_generation=error.details['auxiliary_generation']
                self.results.put(('move',None,error))

    @staticmethod
    def _stop_owned_process(process):
        if process and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2)

    def close(self):
        self._stop.set()
        with self._motion_lock:
            self._generation+=1; self._motion_enabled=False; self._discard_motion('CANCELLED','APP 已关闭')
        # Publication and close share a lock; a later launch cleans up itself.
        with self._process_lock: process=self.process
        self._stop_owned_process(process)
        self._thread.join(timeout=3)
        self._realtime.join(timeout=2)
        if not self.external: self.endpoint.unlink(missing_ok=True)
