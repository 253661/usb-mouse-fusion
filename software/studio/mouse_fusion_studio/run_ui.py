"""Run settings and completed-position preview; communication uses the shared SDK worker."""
import json
import time
import tkinter as tk
from tkinter import ttk
from mouse_fusion.run_policy import normalize_policy,ENUMS
from .compiler import compile_mode,round_delta
from .device_data import execution_signature
from .ui_components import DockablePanel,ScrollFrame,Disclosure

LABELS=dict(run_mode=('单次运行','无限循环','指定次数','指定时长','按住执行'),
            stop_action=('立即停止 · 保留位置','平滑返回点1'),loop_join=('返回点1后重复','从终点继续'),
            return_easing=('线性','缓入','缓出','平滑缓入缓出'),transition_action=('立即切换 · 新相对起点','平滑过渡到新点1'),
            transition_easing=('线性','缓入','缓出','平滑缓入缓出'))
PHASE_TEXT=dict(idle='已停止',path='轨迹运行',waiting='等待 USB 完成',**{'return':'返回点1'},transition='切换过渡')
REASON_TEXT=dict(none='运行中',finished='已完成',stopped='保留当前位置',returned='已返回点1',
                 return_incomplete='返回未完成：有过期/丢弃，已停止',target_reset='J1 会话失效',range_error='返回速度超出 HID 范围',
                 preset_changed='运行涉及的轨迹已变更，已停止',bypass='纯物理模式 · 辅助已取消',input_reset='物理输入失效 · 辅助已取消')

class RunPanel(DockablePanel):
    def __init__(self,app,parent=None):
        super().__init__(app,parent,title=f'轨迹运行 · {app.backend_label}',geometry='1120x780',minsize=(900,640))
        self.vars={};self.slot=app.editor.project.active_mode;self.monitor_pending=False;self.next_poll=0
        self.trail=[];self.last_serial=None;self.last_status=None;self.holding=False;self.last_status_time=0
        self.monitor_enabled=True
        self.phase=tk.StringVar(value=f'未连接 · {app.backend_label}');self.detail=tk.StringVar(value='连接 → 保存运行规则 → 应用工程到 RAM → 开始')
        self.mode_state=tk.StringVar(value='模式开关状态：连接后查询')
        self.result=tk.StringVar(value='等待显式操作。真实 Flash 擦写未开放。')
        app.preset_result=self.result
        top=ttk.Frame(self,padding=12);top.pack(fill='x')
        ttk.Label(top,text='运行与停止',style='Section.TLabel').pack(side='left')
        for title,command in [('连接设备' if app.live_device else '连接模拟器',lambda:app.request_api('connect')),('能力',lambda:app.request_api('get_preset_capabilities')),
                              ('断开 J4',lambda:app.request_api('disconnect'))]:
            app.button(top,title,command).pack(side='right',padx=4)
        body=ttk.Frame(self,padding=(12,0));body.pack(fill='both',expand=True)
        left_scroll=ScrollFrame(body,width=390);left_scroll.pack(side='left',fill='y',padx=(0,16));left_scroll.pack_propagate(False);left=left_scroll.body
        ttk.Label(left,text='设置当前模式').pack(anchor='w')
        self.mode_var=tk.StringVar();self.mode_box=ttk.Combobox(left,textvariable=self.mode_var,state='readonly')
        self.mode_box.pack(fill='x',pady=(4,8));self.mode_box.bind('<<ComboboxSelected>>',lambda e:self.select_mode())
        form=ttk.Frame(left);form.pack(fill='x');form.columnconfigure(1,weight=1)
        fields=[('run_mode','运行方式'),('repeat_count','指定次数'),('duration_ms','指定时长 / ms'),('stop_action','停止策略'),
                ('loop_join','循环衔接'),('return_ms','返回时间 / ms'),('return_easing','返回缓动'),
                ('transition_action','切换策略'),('transition_ms','切换时间 / ms'),('transition_easing','切换缓动')]
        transition=Disclosure(left,'高级：模式切换过渡');transition.pack(fill='x',pady=8)
        app.button(left,'返回时间参考轨迹时长',self.match_return_time).pack(fill='x',pady=6)
        ttk.Label(left,text='返回的是辅助 HID 相对位移；Windows 指针加速开启时，快返可能越过屏幕起点。',
                  style='Muted.TLabel',wraplength=345).pack(fill='x',pady=4)
        transition_form=ttk.Frame(transition.body);transition_form.pack(fill='x');transition_form.columnconfigure(1,weight=1)
        self.rule_widgets={}
        for row,(key,title) in enumerate(fields):
            parent_form=transition_form if key.startswith('transition') else form
            label=ttk.Label(parent_form,text=title);label.grid(row=row,column=0,sticky='w',pady=4,padx=(0,8))
            self.vars[key]=tk.StringVar()
            widget=ttk.Combobox(parent_form,textvariable=self.vars[key],values=LABELS[key],state='readonly',width=17) if key in LABELS else ttk.Entry(parent_form,textvariable=self.vars[key],width=17)
            widget.grid(row=row,column=1,sticky='ew',pady=4)
            self.rule_widgets[key]=(label,widget)
        rule_actions=ttk.Frame(left_scroll,padding=8);rule_actions.grid(row=1,column=0,columnspan=2,sticky='ew')
        app.button(rule_actions,'保存运行规则',self.save_rule,style='Accent.TButton').pack(fill='x')
        right_scroll=ScrollFrame(body);right_scroll.pack(side='left',fill='both',expand=True);right=right_scroll.body
        ttk.Label(right,textvariable=self.phase,style='Title.TLabel').pack(anchor='w')
        ttk.Label(right,textvariable=self.mode_state,style='Accent.TLabel').pack(anchor='w',pady=4)
        ttk.Label(right,textvariable=self.detail,style='Muted.TLabel',wraplength=650).pack(anchor='w',pady=6)
        self.preview=tk.Canvas(right,bg='#111c2a',highlightthickness=0,height=220);self.preview.pack(fill='x')
        self.preview.bind('<Configure>',lambda e:self.draw())
        ttk.Label(right,text='蓝：计划曲线 · 虚线：返回衔接 · 绿：C 完成位置（50ms 状态刷新）',style='Muted.TLabel').pack(anchor='w',pady=5)
        execution=ttk.Frame(right_scroll,padding=(6,8));execution.grid(row=1,column=0,columnspan=2,sticky='ew')
        controls=ttk.Frame(execution);controls.pack(fill='x',pady=(0,6))
        self.start_button=app.button(controls,'开始',self.start,style='Accent.TButton');self.start_button.pack(side='left',padx=2)
        app.button(controls,'按策略停止',lambda:app.request_api('stop_trajectory')).pack(side='left',padx=2)
        app.button(controls,'立即停止',lambda:app.request_api('stop_trajectory_keep')).pack(side='left',padx=2)
        app.button(controls,'返回点1',self.return_now).pack(side='left',padx=2)
        app.button(controls,'切换执行',self.switch).pack(side='left',padx=2)
        virtual=ttk.Frame(execution);virtual.pack(fill='x')
        self.mask=tk.StringVar(value='24')
        self.virtual_buttons={key:tk.BooleanVar(value=key in ('side1','side2')) for key in ('left','right','middle','side1','side2')}
        names=dict(left='左',right='右',middle='中',side1='侧1',side2='侧2')
        for key,var in self.virtual_buttons.items():
            b=ttk.Checkbutton(virtual,text=names[key],variable=var,command=self.update_virtual_mask);b.pack(side='left')
            if app.live_device:b.state(['disabled'])
        self.virtual_hold_button=hold=app.button(virtual,'按住模拟',lambda:None);hold.pack(side='left',padx=8)
        if app.live_device:hold.state(['disabled'])
        hold.bind('<ButtonPress-1>',lambda e:self.virtual_down());hold.bind('<ButtonRelease-1>',lambda e:self.virtual_up())
        self._focus_top=self.winfo_toplevel()
        self._focus_binding=self._focus_top.bind('<FocusOut>',lambda e:self.after_idle(self.release_if_unfocused),add='+')
        self.timebar=ttk.Progressbar(right,maximum=100);self.timebar.pack(fill='x',pady=4)
        debug=Disclosure(right,'高级：执行诊断');debug.pack(fill='x',pady=6)
        self.log=tk.Text(debug.body,height=7,bg='#111c2a',fg='#b7cadc',font=('Consolas',10),wrap='word',state='disabled')
        self.log.pack(fill='x',pady=6)
        bottom=ttk.Frame(self,padding=(12,6));bottom.pack(fill='x')
        app.button(bottom,'应用工程到 RAM',app.apply_project_ram).pack(side='left')
        ttk.Label(bottom,text='本地工程与设备配置分别保存；模拟存储与导出在“设备与保存”。',style='Muted.TLabel',wraplength=650).pack(side='right')
        self.protocol('WM_DELETE_WINDOW',self.destroy);self.sync_project()
        for key in ('run_mode','stop_action','loop_join'):self.vars[key].trace_add('write',lambda *_:self.update_rule_fields())
        self.update_rule_fields()

    def update_rule_fields(self):
        run=self.vars['run_mode'].get()
        for key,visible in [('repeat_count',run=='指定次数'),('duration_ms',run=='指定时长'),('loop_join',run!='单次运行'),('return_ms',self.vars['stop_action'].get()=='平滑返回点1' or run!='单次运行'),('return_easing',self.vars['stop_action'].get()=='平滑返回点1' or run!='单次运行')]:
            for widget in self.rule_widgets[key]:widget.grid() if visible else widget.grid_remove()

    def update_virtual_mask(self):
        self.mask.set(str(sum(1<<i for i,var in enumerate(self.virtual_buttons.values()) if var.get())))

    def match_return_time(self):
        duration=sum(p.duration_ms for p in self.app.editor.project.modes[self.slot].points[:-1])
        self.vars['return_ms'].set(str(max(250,min(60000,duration))))
        self.app.status.set('已参考轨迹时长设置返回时间；请保存运行规则。屏幕是否归位仍受目标程序/指针加速影响。')

    def has_pending_form(self):
        return getattr(self,'_loaded_form',None) is not None and self._loaded_form!=tuple(v.get() for v in self.vars.values())

    def sync_project(self):
        modes=self.app.editor.project.modes;self.slot=min(self.slot,len(modes)-1)
        self.mode_box['values']=[f'{i+1} · {m.name}' for i,m in enumerate(modes)];self.mode_box.current(self.slot)
        rule=normalize_policy(modes[self.slot].run_policy)
        for key,var in self.vars.items():var.set(LABELS[key][ENUMS[key].index(rule[key])] if key in LABELS else str(rule[key]))
        self._loaded_form=tuple(v.get() for v in self.vars.values())
        try:self.compiled=compile_mode(modes[self.slot])
        except ValueError:self.compiled=None
        self.draw()
        if hasattr(self,'rule_widgets'):self.update_rule_fields()

    def select_mode(self):
        index=self.mode_box.current()
        if self.window is None and hasattr(self.app,'enter_mode'):
            self.app.enter_mode(index);self.app.navigate('run');self.slot=index;self.trail=[]
            return
        if self.has_pending_form():
            self.require_saved_rule();self.mode_box.current(self.slot);return
        if self.window is None:
            self.app.settings_mode_box.current(index)
            if not self.app.settings_mode_selected():
                self.mode_box.current(self.slot);return
        self.slot=index;self.trail=[]
        self.sync_project()

    def read_rule(self):
        values=dict(self.app.editor.project.modes[self.slot].run_policy)
        for key,var in self.vars.items():values[key]=ENUMS[key][LABELS[key].index(var.get())] if key in LABELS else int(var.get())
        return normalize_policy(values)

    def save_rule(self):
        try:
            rule=self.read_rule();mode=self.app.editor.project.modes[self.slot]
            def change():
                mode.run_policy=rule
                config=self.app.editor.project.editor_state.get('v2_controls',{}).get(mode.mode_id)
                if config:
                    for t in mode.triggers:
                        if t.action=='start_mode_trajectory' and t.trigger_id in config.get('ids',[]):
                            t.behavior='hold' if rule['run_mode']=='hold' else 'once'
                            if t.behavior=='hold' and t.gesture=='click':t.gesture='chord'
                            config.update(behavior=t.behavior,execute_gesture=t.gesture)
            self.app.editor.apply(change)
            self._loaded_form=tuple(v.get() for v in self.vars.values())
            self.app.after_edit(False)
        except (ValueError,TypeError) as error:self.app.report_error(error)

    def require_saved_rule(self):
        if hasattr(self.app,'flush_drafts') and not self.app._v2_busy:self.app.flush_drafts()
        try:
            if self.read_rule()!=normalize_policy(self.app.editor.project.modes[self.slot].run_policy):
                raise ValueError('运行规则已修改，请先保存运行规则到工程')
        except (ValueError,TypeError) as error:self.app.report_error(error);return False
        return True

    def guard(self):
        state=getattr(self,'mode_info',None)
        if state and state.get('configured') and (not state['enabled'] or state['selected']!=self.slot):
            self.app.report_error(ValueError('请先按模式开关键开启所选模式，再按执行键运行'));return False
        if not self.require_saved_rule():return False
        if hasattr(self.app,'bypass_panel') and self.app.bypass_panel.winfo_exists() and not self.app.bypass_panel.require_saved_config():return False
        if self.slot not in self.app._preset_applied_crcs or self.app._preset_applied_signature!=execution_signature(self.app.editor.project):
            self.app.report_error(ValueError('请先保存运行规则并重新应用当前工程到 RAM'));return False
        return True

    def start(self):
        if self.guard():self.trail=[];self.app.request_api('start_trajectory',self.slot,expected_crc32=int(self.app._preset_applied_crcs[self.slot],16))

    def switch(self):
        if self.guard():self.app.request_api('switch_trajectory',self.slot,expected_crc32=int(self.app._preset_applied_crcs[self.slot],16))

    def return_now(self):
        try:
            rule=self.read_rule();self.app.request_api('return_trajectory',duration_ms=rule['return_ms'],easing=rule['return_easing'])
        except ValueError as error:self.app.report_error(error)

    def virtual_down(self):
        if not self.guard():return
        try:
            mask=int(self.mask.get())
            if not 1<=mask<=31:raise ValueError('虚拟 mask 须为 1..31')
            self.holding=True;self.app.request_api('simulate_mouse_buttons',mask)
        except ValueError as error:self.app.report_error(error)

    def virtual_up(self):
        if self.holding:self.holding=False;self.app.request_api('simulate_mouse_buttons',0)

    def release_if_unfocused(self):
        if not self.winfo_exists():return
        try:focus=self.focus_get()
        except (KeyError,tk.TclError):focus=None  # Tcl-created Combobox popdown has no Python widget.
        if focus is None or focus.winfo_toplevel() is not self.winfo_toplevel():self.virtual_up()

    def tick(self):
        now=time.perf_counter()
        if self.app.connected and self.monitor_enabled and not self.monitor_pending and now>=self.next_poll:
            try:
                self.app.connection.submit('get_run_status');self.monitor_pending=True;self.next_poll=now+.05
                if self.app.device_capabilities and self.app.device_capabilities&0x2000:self.app.connection.submit('get_mode_control_status')
            except ValueError:self.next_poll=now+.25

    def receive(self,method,result,error=None):
        if method=='get_mode_control_status':
            self.mode_info=None if error else result
            if error:self.mode_state.set('模式开关状态：未知')
            elif result.get('configured'):self.mode_state.set(f"模式 {result['selected']+1} · "+('已开启，等待执行键' if result['enabled'] else '已关闭，轨迹不叠加'))
            else:self.mode_state.set('当前使用传统绑定')
            return
        if method=='get_run_status':self.monitor_pending=False
        if method=='connect':self.monitor_enabled=True
        if error:
            if method=='get_run_status' and getattr(error,'code','')=='NOT_SUPPORTED':self.monitor_enabled=False
            self.phase.set(f'通信错误：{error}');return
        if method=='get_run_status':
            now=time.perf_counter();speed=0
            if self.last_status and self.last_status_time and result['run_serial']==self.last_status['run_serial']:
                dx=result['preset_x']-self.last_status['preset_x'];dy=result['preset_y']-self.last_status['preset_y']
                speed=(dx*dx+dy*dy)**.5/max(.001,now-self.last_status_time)
            self.last_status_time=now
            self.last_status=result
            if self.last_serial!=result['run_serial']:self.trail=[];self.last_serial=result['run_serial']
            if result['position_known']:
                point=(result['preset_x'],-result['preset_y'])
                if not self.trail or self.trail[-1]!=point:self.trail.append(point);self.trail=self.trail[-512:]
            self.phase.set(PHASE_TEXT[result['phase']]+' · '+REASON_TEXT[result['reason']])
            self.detail.set(f"模式 {result['slot']+1}  ·  已完成 {result['cycles']} 轮  ·  {result['elapsed_ms']} ms\n"
                f"预设完成坐标 ({result['preset_x']}, {-result['preset_y']})  ·  预览速度 {speed:.0f} counts/s\n"
                f"路径跳过 {result['skipped']}  ·  返回跳过 {result['return_skipped']}")
            total=self.compiled.duration_ms if self.compiled else 1;self.timebar['value']=min(100,100*result['consumed']/total);self.draw()
        if method not in ('get_run_status','get_status','get_config','move'):
            self.result.set(json.dumps(result,ensure_ascii=False,indent=2));self.log.configure(state='normal');self.log.delete('1.0','end')
            self.log.insert('end',self.result.get());self.log.configure(state='disabled')

    def draw(self):
        if not hasattr(self,'preview'):return
        c=self.preview;c.delete('all');w=max(400,c.winfo_width());h=max(260,c.winfo_height())
        points=[(p.x,p.y) for m in self.app.editor.project.modes for p in m.points]+self.trail+[(0,0)]
        xs=[p[0] for p in points];ys=[p[1] for p in points]
        lowx,highx=min(xs)-10,max(xs)+10;lowy,highy=min(ys)-10,max(ys)+10
        scale=min((w-60)/max(20,highx-lowx),(h-50)/max(20,highy-lowy))
        def screen(p):return 30+(p[0]-lowx)*scale,h-25-(p[1]-lowy)*scale
        ox,oy=screen((0,0));c.create_line(0,oy,w,oy,fill='#344b62');c.create_line(ox,0,ox,h,fill='#344b62')
        c.create_text(w-15,oy-12,text='X',fill='#91a7bb');c.create_text(ox+12,14,text='Y',fill='#91a7bb')
        if getattr(self,'compiled',None):
            samples=self.compiled.samples;stride=max(1,len(samples)//1200)
            path=[screen((s.x,s.y)) for s in samples[::stride]]+[screen((samples[-1].x,samples[-1].y))]
            if len(path)>=2:c.create_line(*[v for p in path for v in p],fill='#56a4fa',width=2)
            rule=normalize_policy(self.app.editor.project.modes[self.slot].run_policy)
            if rule['stop_action']=='return' or (rule['run_mode']!='once' and rule['loop_join']=='return'):
                a,b=screen((samples[-1].x,samples[-1].y)),screen((samples[0].x,samples[0].y))
                c.create_line(*a,*b,fill='#e0a763',dash=(5,4),arrow='last')
        for i,p in enumerate(self.app.editor.project.modes[self.slot].points):
            x,y=screen((p.x,p.y));c.create_oval(x-8,y-8,x+8,y+8,fill='#203a55',outline='#56a4fa')
            c.create_text(x,y,text=str(i+1),fill='white',font=('Consolas',8))
        if len(self.trail)>=2:c.create_line(*[v for p in self.trail for v in screen(p)],fill='#6bdbac',width=3)
        if self.trail:
            x,y=screen(self.trail[-1]);c.create_oval(x-5,y-5,x+5,y+5,fill='#6bdbac',outline='white')

    def destroy(self):
        self.virtual_up()
        if self._focus_top.winfo_exists():self._focus_top.unbind('<FocusOut>',self._focus_binding)
        super().destroy()
