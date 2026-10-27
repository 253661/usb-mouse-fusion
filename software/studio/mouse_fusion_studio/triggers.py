"""APP configuration + deterministic reference trigger machine, no OS hooks/MCU I/O."""
from collections import deque
from dataclasses import dataclass, field
import math
import uuid

MOUSE_BUTTONS=('left','right','middle','side1','side2','wheel_up','wheel_down')
WHEEL_EVENTS=frozenset(('wheel_up','wheel_down'))
MODIFIERS=('CTRL','ALT','SHIFT')
KEYS=set(MODIFIERS)|set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')|{f'F{i}' for i in range(1,25)}|{'SPACE','ENTER','ESC','UP','DOWN','LEFT','RIGHT','TAB'}
RESERVED=(frozenset(['CTRL','S']),frozenset(['CTRL','SHIFT','S']),frozenset(['CTRL','O']),
          frozenset(['CTRL','Z']),frozenset(['CTRL','Y']),frozenset(['CTRL','SHIFT','Z']),
          frozenset(['CTRL','A']),frozenset(['CTRL','D']),frozenset(['F']),frozenset(['SPACE']))


def normalize_keys(keys):
    aliases={'CONTROL_L':'CTRL','CONTROL_R':'CTRL','CTRL':'CTRL','SHIFT_L':'SHIFT','SHIFT_R':'SHIFT',
             'ALT_L':'ALT','ALT_R':'ALT','RETURN':'ENTER','ESCAPE':'ESC'}
    values={aliases.get(str(k).upper(),str(k).upper()) for k in keys}
    if not values<=KEYS: raise ValueError('不支持的键盘按键')
    return sorted(values,key=lambda v:(MODIFIERS.index(v) if v in MODIFIERS else 3,v))


@dataclass
class Trigger:
    buttons: list = field(default_factory=lambda:['side1','side2'])
    device: str = 'mouse'
    gesture: str = 'chord'
    action: str = 'next_mode'
    target_mode: str | None = None
    behavior: str = 'once'
    window_ms: int = 200
    hold_ms: int = 400
    debounce_ms: int = 12
    button_policy: str = 'passthrough'
    enabled: bool = True
    trigger_id: str = field(default_factory=lambda:uuid.uuid4().hex)

    def validate(self):
        if self.device not in ('mouse','keyboard'): raise ValueError('触发输入须为 mouse 或 keyboard')
        allowed=set(MOUSE_BUTTONS) if self.device=='mouse' else KEYS
        if not isinstance(self.buttons,list) or not 1<=len(self.buttons)<= (6 if self.device=='mouse' else 5) or any(not isinstance(b,str) or b not in allowed for b in self.buttons) or len(set(self.buttons))!=len(self.buttons):
            raise ValueError('组合须包含不同的有效按键和至多一个滚轮方向')
        if self.device=='keyboard' and not set(self.buttons)-set(MODIFIERS): raise ValueError('键盘组合须包含至少一个普通键')
        if self.gesture not in ('click','hold','chord'): raise ValueError('未知触发手势')
        if self.action not in ('switch_mode','next_mode','previous_mode','start_trajectory','stop_trajectory','toggle_mode','start_current_trajectory','start_mode_trajectory'): raise ValueError('未知触发动作')
        if self.behavior not in ('once','hold','toggle'): raise ValueError('未知执行方式')
        if self.behavior=='hold' and (self.gesture=='click' or self.action not in ('start_trajectory','start_current_trajectory','start_mode_trajectory')):
            raise ValueError('按住执行须配合长按/组合同时按下及启动轨迹动作')
        if self.behavior=='toggle' and self.action!='start_trajectory': raise ValueError('再次触发停止仅适用于启动轨迹')
        if WHEEL_EVENTS&set(self.buttons) and (WHEEL_EVENTS<=set(self.buttons) or self.gesture=='hold' or self.behavior!='once'):
            raise ValueError('滚轮为瞬时事件，只能选一个方向并使用单击/组合单次执行')
        if self.button_policy not in ('passthrough','suppress_requested'): raise ValueError('未知按键策略')
        if type(self.enabled) is not bool: raise ValueError('启用状态须为布尔值')
        for key,low,high in (('window_ms',10,2000),('hold_ms',50,5000),('debounce_ms',1,50)):
            if type(getattr(self,key)) is not int or not low<=getattr(self,key)<=high: raise ValueError(f'{key} 须为 {low}..{high}ms 整数')
        if self.debounce_ms>self.window_ms or self.debounce_ms>=self.hold_ms: raise ValueError('去抖时间须小于长按阈值，且不大于组合窗口')
        if not isinstance(self.trigger_id,str) or not self.trigger_id or len(self.trigger_id)>64: raise ValueError('触发器 ID 无效')
        if self.target_mode is not None and (not isinstance(self.target_mode,str) or not self.target_mode): raise ValueError('目标模式 ID 无效')


@dataclass(frozen=True)
class Conflict:
    severity: str
    kind: str
    ids: tuple
    message: str


def find_conflicts(bindings,*,bypass=None):
    conflicts=[]; active=[b for b in bindings if b.enabled]
    from mouse_fusion.bypass import normalize_config
    button=normalize_config(bypass or {})['button']
    if button:
        for b in active:
            if b.device=='mouse' and button in b.buttons:
                conflicts.append(Conflict('warning','bypass',('global-bypass',b.trigger_id),'包含纯物理模式旁路键；BYPASS 优先，此组合会被阻止，建议重新绑定'))
    for b in active:
        # Tk's specific shortcut binding also matches additional modifiers.
        if b.device=='keyboard' and any(shortcut<=set(b.buttons) for shortcut in RESERVED):
            conflicts.append(Conflict('error','reserved',(b.trigger_id,),'与 Studio 内置编辑快捷键冲突'))
    for i,a in enumerate(active):
        for b in active[i+1:]:
            if a.device!=b.device: continue
            x,y=set(a.buttons),set(b.buttons)
            if x==y and (a.gesture==b.gesture or bool(x&WHEEL_EVENTS)):
                scoped=a.action==b.action=='start_mode_trajectory' and a.target_mode!=b.target_mode
                shared={a.action,b.action}=={'start_current_trajectory','start_mode_trajectory'}
                if not scoped and not shared:conflicts.append(Conflict('error','duplicate',(a.trigger_id,b.trigger_id),'相同组合与手势重复绑定'))
            elif x & y:
                compatible_pair=x==y and {a.gesture,b.gesture}=={'click','hold'} and a.hold_ms==b.hold_ms
                if not compatible_pair:
                    conflicts.append(Conflict('warning','overlap',(a.trigger_id,b.trigger_id),'按键有交集；额外按键阻止匹配，已触发组合会消耗子组合点击'))
    return conflicts


@dataclass(frozen=True)
class TriggerEvent:
    trigger_id: str
    action: str
    target_mode: str | None
    behavior: str
    phase: str = 'activate'
    button_policy: str = 'passthrough'


class TriggerMachine:
    """Exact chord matching, per-button debounce, release latch. Reference model only.

    feed/advance return logical events. raw_events records inputs verbatim; there is
    no physical event interception, output HID or suppression implementation.
    """
    def __init__(self,bindings,*,bypass=None):
        from mouse_fusion.bypass import normalize_config
        self.bypass_button=normalize_config(bypass or {})['button'];self.bypass_active=False;self.bypass_rearm=False
        self.bindings=[b for b in bindings if b.enabled]
        for b in self.bindings: b.validate()
        errors=[c for c in find_conflicts(self.bindings) if c.severity=='error']
        if errors: raise ValueError(errors[0].message)
        self.now=0; self.pressed={'mouse':set(),'keyboard':set()}
        self.raw={'mouse':set(),'keyboard':set()}; self.candidates={}; self.down_at={}; self.up_at={}
        self.states={b.trigger_id:dict(state='idle',start=0,active=False) for b in self.bindings}
        self.consumed={'mouse':set(),'keyboard':set()}
        self.raw_events=deque(maxlen=128)
        self.selected_mode=None;self.scope_enabled=False;self.scope_set=False

    def set_scope(self,mode_id,enabled):
        if type(enabled) is not bool:raise ValueError('模式开启状态须为布尔值')
        self.selected_mode=mode_id;self.scope_enabled=enabled;self.scope_set=True

    def _allowed(self,b):
        if b.action=='start_mode_trajectory':return self.scope_set and self.scope_enabled and b.target_mode==self.selected_mode
        if b.action!='start_current_trajectory' or not self.scope_set:return True
        return self.scope_enabled and not any(t.action=='start_mode_trajectory' and t.target_mode==self.selected_mode for t in self.bindings)

    def feed_wheel(self,delta,t_ms):
        if type(delta) is not int or not -32768<=delta<=32767:raise ValueError('滚轮须为有符号整数增量')
        events=self.advance(t_ms)
        if not delta:return events
        wheel='wheel_up' if delta>0 else 'wheel_down';self.raw_events.append(('mouse',wheel,True,t_ms))
        if self.bypass_active or self.bypass_rearm:return events
        mode_keys=set().union(*(set(b.buttons)-WHEEL_EVENTS for b in self.bindings if b.device=='mouse' and b.action=='toggle_mode'))
        for b in sorted(self.bindings,key=lambda b:(b.action!='toggle_mode',b.gesture=='click')):
            if b.device!='mouse' or wheel not in b.buttons or not self._allowed(b):continue
            keys=set(b.buttons)-WHEEL_EVENTS
            if b.action=='toggle_mode':
                if self.pressed['mouse']&mode_keys!=keys or self.raw['mouse']&mode_keys!=keys:continue
            elif keys!=self.pressed['mouse'] or keys!=self.raw['mouse']:continue
            modifier=b.action=='toggle_mode' and keys in ({'side1'},{'side2'})
            if not modifier and any(t_ms-self.down_at['mouse',key]>b.window_ms for key in keys):continue
            self.consumed['mouse']|=keys
            events.append(self._event(b));break
        return events

    def _time(self,t):
        if isinstance(t,bool) or not isinstance(t,(int,float)) or not math.isfinite(t) or t<self.now:
            raise ValueError('模拟时间须为递增的有限毫秒值')

    def feed(self,button,down,t_ms,*,device='mouse'):
        if device not in self.pressed or button not in (MOUSE_BUTTONS[:5] if device=='mouse' else KEYS) or type(down) is not bool:
            raise ValueError('无效模拟按键事件')
        self._time(t_ms)
        if device=='mouse' and button==self.bypass_button and down!=(button in self.raw['mouse']):
            self.now=t_ms;self.raw_events.append((device,button,down,t_ms))
            if down:self.raw['mouse'].add(button)
            else:self.raw['mouse'].discard(button)
            self.bypass_active=down;self.bypass_rearm=True
            self._clear_recognition()
            return [TriggerEvent('global-bypass','bypass',None,'hold','activate' if down else 'release')]
        events=self.advance(t_ms)
        self.raw_events.append((device,button,down,t_ms))
        if down==(button in self.raw[device]): return events
        if down: self.raw[device].add(button)
        else: self.raw[device].discard(button)
        debounce=max([b.debounce_ms for b in self.bindings if b.device==device and button in b.buttons] or [12])
        self.candidates[device,button]=(down,t_ms+debounce,t_ms)
        return events

    def _event(self,b,phase='activate'):
        return TriggerEvent(b.trigger_id,b.action,b.target_mode,b.behavior,phase,b.button_policy)

    def advance(self,t_ms):
        self._time(t_ms); self.now=t_ms
        if self.bypass_active or self.bypass_rearm:
            self._clear_recognition()
            if not self.bypass_active and not self.raw['mouse']:self.bypass_rearm=False
            return []
        for (device,key),(down,at,edge_at) in list(self.candidates.items()):
            if at>t_ms: continue
            del self.candidates[device,key]
            if down:
                if device=='mouse' and key in ('side1','side2') and key not in self.pressed[device]:self.consumed[device].discard(key)
                self.pressed[device].add(key); self.down_at[device,key]=edge_at
            else: self.pressed[device].discard(key); self.up_at[device,key]=edge_at
        events=[]
        # Hold/chord activation precedes clicks, so a consumed hold cannot fire a click.
        bindings=sorted(self.bindings,key=lambda b:(b.action!='toggle_mode',b.gesture=='click'))
        mode_keys={device:set().union(*(set(b.buttons) for b in bindings if b.action=='toggle_mode' and b.device==device)) for device in self.pressed}
        for b in bindings:
            if WHEEL_EVENTS&set(b.buttons):continue
            state=self.states[b.trigger_id]; keys=set(b.buttons); pressed=self.pressed[b.device]
            extras=bool((pressed&mode_keys[b.device])-keys) if b.action=='toggle_mode' else bool(pressed-keys)
            exact=keys<=pressed and not extras if b.action=='toggle_mode' else pressed==keys
            any_down=bool(pressed & keys)
            if b.device=='mouse' and b.action=='toggle_mode' and b.gesture=='click' and keys in ({'side1','right'},{'side2','right'}):
                modifier=next(k for k in keys if k!='right')
                if state['state']=='tracking':
                    if extras:state['state']='blocked'
                    elif 'right' not in pressed:
                        release=self.up_at.get(('mouse','right'),-1)
                        if 'right' not in self.raw['mouse'] and state['start']<=release<state['start']+b.hold_ms:events.append(self._event(b))
                        state['state']='idle'
                elif 'right' not in pressed:state['state']='idle'
                elif state['state']=='idle' and exact:
                    if self.raw['mouse']&mode_keys['mouse']==keys and self.down_at['mouse',modifier]+b.debounce_ms<=self.down_at['mouse','right']:
                        state.update(state='tracking',start=self.down_at['mouse','right']);self.consumed['mouse'].add(modifier)
                    else:state['state']='blocked'
                continue
            if not self._allowed(b):
                if state['active']:events.append(self._event(b,'release'));state['active']=False
                state['state']='blocked' if any_down else 'idle';continue
            if state['state']=='fired':
                if state['active'] and not exact:
                    events.append(self._event(b,'release')); state['active']=False
                if not any_down: state['state']='idle'
                continue
            if state['state']=='blocked':
                if not any_down: state['state']='idle'
                continue
            if extras and any_down:
                state['state']='blocked'; continue
            if exact:
                times=[self.down_at[b.device,key] for key in keys]
                if max(times)-min(times)>b.window_ms:
                    state['state']='blocked'; continue
                if state['state']=='idle': state['state']='tracking'; state['start']=max(times)
                eligible=(b.gesture=='chord' or b.gesture=='hold' and t_ms-state['start']>=b.hold_ms)
                if eligible and (self.raw[b.device]&mode_keys[b.device]==keys if b.action=='toggle_mode' else self.raw[b.device]==keys):
                    emitted=not bool(keys & self.consumed[b.device])
                    if emitted:
                        events.append(self._event(b)); self.consumed[b.device]|=keys
                    state['state']='fired'; state['active']=emitted and b.behavior=='hold'
            elif not any_down:
                if state['state']=='tracking' and b.gesture=='click':
                    # Use the accepted release's original edge time, not a late tick.
                    release_at=max(self.up_at.get((b.device,key),t_ms) for key in keys)
                    if release_at-state['start']<b.hold_ms and not keys & self.consumed[b.device]:
                        events.append(self._event(b)); self.consumed[b.device]|=keys
                state['state']='idle'; state['active']=False
        for device in self.pressed:
            if not self.pressed[device]: self.consumed[device].clear()
        return events

    def _clear_recognition(self):
        self.pressed={'mouse':set(),'keyboard':set()};self.candidates.clear();self.down_at.clear();self.up_at.clear()
        self.consumed={'mouse':set(),'keyboard':set()}
        self.states={b.trigger_id:dict(state='idle',start=0,active=False) for b in self.bindings}

    def reset(self):
        events=[self._event(b,'release') for b in self.bindings if self.states[b.trigger_id]['active']]
        self.pressed={'mouse':set(),'keyboard':set()}; self.raw={'mouse':set(),'keyboard':set()}
        self.candidates.clear(); self.down_at.clear(); self.up_at.clear(); self.consumed={'mouse':set(),'keyboard':set()}
        self.states={b.trigger_id:dict(state='idle',start=0,active=False) for b in self.bindings}
        self.bypass_active=False;self.bypass_rearm=False
        return events
