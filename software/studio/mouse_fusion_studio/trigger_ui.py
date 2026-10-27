"""Binding editor and virtual-button simulation; no device input or OS hooks."""
import tkinter as tk
from tkinter import ttk, messagebox
from .triggers import Trigger, TriggerMachine, MOUSE_BUTTONS, WHEEL_EVENTS, find_conflicts, normalize_keys
from .ui_components import DockablePanel,ScrollFrame,Disclosure

BUTTON_NAMES=dict(left='左键',right='右键',middle='中键',side1='侧键1',side2='侧键2',wheel_up='滚轮前滚',wheel_down='滚轮后滚')
GESTURES={'组合同时按下':'chord','单击（释放时）':'click','长按':'hold'}
ACTIONS={'切换到指定模式':'switch_mode','下一模式':'next_mode','上一模式':'previous_mode',
         '启动指定轨迹':'start_trajectory','停止指定轨迹':'stop_trajectory',
         '开启/关闭指定模式':'toggle_mode','启动当前模式轨迹':'start_current_trajectory','执行指定开启模式':'start_mode_trajectory'}
BEHAVIORS={'单次触发':'once','按住执行':'hold','再次触发停止':'toggle'}
POLICIES={'正常透传（默认）':'passthrough','请求屏蔽（未来 MCU）':'suppress_requested'}


class TriggerManager(DockablePanel):
    def __init__(self,app,parent=None):
        super().__init__(app,parent,title='Mouse Fusion Studio — 快捷键与触发器',geometry='1100x760',minsize=(900,640))
        self.protocol('WM_DELETE_WINDOW',self.destroy)
        self.editing_id=None; self.message=tk.StringVar(value='选择模式，再设置切换键或执行键。保存绑定后需另行应用设备。')
        self.keys_var=tk.StringVar(); self.enabled=tk.BooleanVar(value=True)
        self.button_vars={key:tk.BooleanVar(value=key in ('side1','side2')) for key in MOUSE_BUTTONS}
        self.sim_vars={key:tk.BooleanVar(value=False) for key in MOUSE_BUTTONS[:5]}
        self.sim_ms=0; self.machine=None
        self._synced=None;self._loaded_form=None;self._loaded_mode=None
        self._build(); self.refresh_list(); self.new_binding(); self.reset_simulator()

    @property
    def modes(self): return self.app.editor.project.modes
    def labels(self): return [f'{i+1}: {m.name}' for i,m in enumerate(self.modes)]
    def mode_for(self,label):
        try: return self.modes[int(label.split(':',1)[0])-1]
        except (ValueError,IndexError): raise ValueError('请选择有效模式')
    def bindings(self): return [b for m in self.modes for b in m.triggers]

    def _build(self):
        top=ttk.Frame(self,padding=14); top.pack(fill='x')
        ttk.Label(top,text='模式切换与执行键',style='Section.TLabel').pack(side='left')
        ttk.Checkbutton(top,text='启用 APP 本机键盘快捷键',variable=self.app.keyboard_enabled,
                        command=self.app.keyboard_setting_changed).pack(side='right')
        ttk.Label(self,text='鼠标绑定保存到工程；应用设备后生效。键盘绑定仅运行 APP 的电脑且 APP 有焦点时有效。',
                  style='Muted.TLabel').pack(anchor='w',padx=14,pady=(0,10))
        quick=Disclosure(self,'快速设置：模式开关 + 共用执行键',expanded=True);quick.pack(fill='x',padx=14,pady=(0,8))
        row=ttk.Frame(quick.body);row.pack(fill='x')
        self.quick_mode_key=tk.StringVar(value='侧键1');self.quick_execute_key=tk.StringVar(value='左键')
        self.quick_return_ms=tk.StringVar(value=str(max(250,min(60000,sum(p.duration_ms for p in self.app.editor.mode.points[:-1])))))
        for label,var in [('模式开关键',self.quick_mode_key),('共用执行键',self.quick_execute_key)]:
            ttk.Label(row,text=label).pack(side='left',padx=(0,6))
            ttk.Combobox(row,textvariable=var,values=[BUTTON_NAMES[k] for k in MOUSE_BUTTONS[:5]],state='readonly',width=7).pack(side='left',padx=(0,12))
        ttk.Label(row,text='返回 ms').pack(side='left',padx=(0,6))
        ttk.Entry(row,textvariable=self.quick_return_ms,width=7).pack(side='left',padx=(0,12))
        self.app.button(row,'应用控制方式',self.apply_quick_controls,style='Accent.TButton').pack(side='left')
        ttk.Label(quick.body,text='按模式键开启，再按关闭；执行键按住运行、松开返回。会替换所选键原绑定。新固件才可应用设备。',
                  style='Muted.TLabel',wraplength=1050).pack(anchor='w',pady=(6,0))
        main=ttk.Frame(self,padding=(14,0)); main.pack(fill='both',expand=True)
        main.columnconfigure(0,weight=1); main.columnconfigure(1,weight=1); main.rowconfigure(0,weight=1)
        left_scroll=ScrollFrame(main);left_scroll.grid(row=0,column=0,sticky='nsew',padx=(0,12));left=left_scroll.body
        self.tree=ttk.Treeview(left,columns=('mode','combo','gesture','action'),show='headings',height=5)
        for key,label,width in [('mode','所属模式',80),('combo','组合键',130),('gesture','手势',70),('action','动作',80)]:
            self.tree.heading(key,text=label); self.tree.column(key,width=width,stretch=True)
        self.tree.pack(fill='x'); self.tree.bind('<<TreeviewSelect>>',self.selected)
        row=ttk.Frame(left); row.pack(fill='x',pady=8)
        for text,cmd in [('新增',self.new_binding_from_ui),('删除',self.delete_binding),('恢复默认',self.restore_defaults)]:
            ttk.Button(row,text=text,command=cmd).pack(side='left',padx=3)
        self.conflict_text=ttk.Label(left,text='',wraplength=415,foreground='#f8b563'); self.conflict_text.pack(anchor='w',pady=8)
        sim=ttk.Labelframe(left,text='虚拟鼠标模拟（不会读取真实鼠标）',padding=10); sim.pack(fill='both',expand=True,pady=8)
        row=ttk.Frame(sim); row.pack(fill='x')
        for index,key in enumerate(MOUSE_BUTTONS[:5]):
            ttk.Checkbutton(row,text=BUTTON_NAMES[key],variable=self.sim_vars[key],
                command=lambda key=key:self.sim_feed(key,self.sim_vars[key].get())).grid(row=index//3,column=index%3,sticky='w',pady=3)
        for index,key in enumerate(MOUSE_BUTTONS[5:]):
            ttk.Button(row,text=BUTTON_NAMES[key],command=lambda key=key:self.sim_wheel(1 if key=='wheel_up' else -1)).grid(row=2,column=index,sticky='w',pady=3)
        row=ttk.Frame(sim); row.pack(fill='x',pady=8)
        for index,(text,cmd) in enumerate([('按下组合',self.sim_press_form),('释放全部',self.sim_release_all),
                         ('+50ms',lambda:self.sim_advance(50)),('+400ms',lambda:self.sim_advance(400))]):
            ttk.Button(row,text=text,command=cmd).grid(row=index//2,column=index%2,sticky='ew',padx=1,pady=2)
        row.columnconfigure((0,1),weight=1)
        row=ttk.Frame(sim); row.pack(fill='x')
        ttk.Button(row,text='重置模拟器',command=self.reset_simulator).pack(side='left')
        self.sim_state=ttk.Label(row,text='0ms · 全部释放',style='Muted.TLabel'); self.sim_state.pack(side='left',padx=10)
        details=Disclosure(sim,'高级：模拟事件记录');details.pack(fill='x',pady=8)
        self.log=tk.Text(details.body,height=7,width=40,bg='#101b28',fg='#cbdcea',font=('Consolas',10),borderwidth=0,wrap='word')
        self.log.pack(fill='both',expand=True,pady=(10,0)); self.log.configure(state='disabled')
        right_scroll=ScrollFrame(main);right_scroll.grid(row=0,column=1,sticky='nsew');right=right_scroll.body
        form=ttk.Frame(right); form.pack(fill='x')
        self.vars=self.app.fields(form,[('owner','所属模式','',self.labels()),('device','输入类型','mouse',['mouse','keyboard']),
            ('gesture','触发手势','组合同时按下',list(GESTURES)),('action','触发动作','下一模式',list(ACTIONS)),
            ('target','指定目标','',self.labels()),('behavior','执行方式','单次触发',list(BEHAVIORS))])
        self._owner_combo=form.grid_slaves(row=0,column=1)[0]
        self._target_combo=form.grid_slaves(row=4,column=1)[0]
        self._gesture_combo=form.grid_slaves(row=2,column=1)[0]
        self._behavior_combo=form.grid_slaves(row=5,column=1)[0]
        self.behavior_hint=tk.StringVar()
        ttk.Label(right,textvariable=self.behavior_hint,wraplength=440,style='Muted.TLabel').pack(fill='x',pady=(5,0))
        mouse=ttk.Frame(right); mouse.pack(fill='x',pady=10)
        for index,key in enumerate(MOUSE_BUTTONS):
            ttk.Checkbutton(mouse,text=BUTTON_NAMES[key],variable=self.button_vars[key],command=self.update_fields).grid(row=index//4,column=index%4,sticky='w')
        self.key_area=ttk.Frame(right)
        ttk.Label(self.key_area,text='键盘组合（如 CTRL+SHIFT+F6）',style='Muted.TLabel').pack(anchor='w')
        entry=ttk.Entry(self.key_area,textvariable=self.keys_var); entry.pack(fill='x',pady=5); entry.bind('<KeyPress>',self.record_key)
        self.key_area.pack(fill='x')
        self.input_anchor=ttk.Frame(right);self.input_anchor.pack(fill='x')
        actions=ttk.Frame(right_scroll,padding=(12,8));actions.grid(row=1,column=0,columnspan=2,sticky='ew')
        buttons=ttk.Frame(actions);buttons.pack(fill='x')
        ttk.Checkbutton(buttons,text='启用当前绑定',variable=self.enabled).pack(side='left')
        self.save_button=ttk.Button(buttons,text='保存绑定',command=self.save_binding,style='Accent.TButton');self.save_button.pack(side='right')
        self.save_feedback=tk.StringVar()
        self.feedback_label=ttk.Label(actions,textvariable=self.save_feedback,wraplength=430)
        self.feedback_label.pack(fill='x',pady=(5,0))
        actions.bind('<Configure>',lambda e:self.feedback_label.configure(wraplength=max(160,e.width-24)))
        timing=Disclosure(right,'高级：识别时间与按键透传');timing.pack(fill='x',pady=10)
        form=ttk.Frame(timing.body);form.pack(fill='x')
        self.vars.update(self.app.fields(form,[('window_ms','组合窗口 (ms)',200,None),('hold_ms','长按阈值 (ms)',400,None),
            ('debounce_ms','去抖 (ms)',12,None),('button_policy','物理按键策略','正常透传（默认）',list(POLICIES))]))
        ttk.Label(timing.body,text='默认不屏蔽物理按键。屏蔽选项仅记录请求，当前设备不支持。',style='Muted.TLabel',wraplength=390).pack(anchor='w',pady=8)
        self.vars['device'].trace_add('write',lambda *_:self.update_fields())
        self.vars['action'].trace_add('write',lambda *_:self.update_fields())
        self.vars['gesture'].trace_add('write',lambda *_:self.update_fields())
        for var in self.button_vars.values():var.trace_add('write',lambda *_:self.update_fields())
        self.mouse_form=mouse;self.key_label=entry;self.target_form=self._target_combo
        ttk.Label(self,textvariable=self.message,wraplength=1000,style='Accent.TLabel').pack(side='bottom',anchor='w',padx=14,pady=10)
        main.pack_forget(); main.pack(fill='both',expand=True)

    def capture_keys(self,keys): self.keys_var.set('+'.join(normalize_keys(keys))); self.vars['device'].set('keyboard')

    def apply_quick_controls(self):
        if not self.app.require_applied_forms():return
        from .mode_controls import configure_hold_mode
        try:
            reverse={label:key for key,label in BUTTON_NAMES.items()}
            self.app.editor.apply(lambda:configure_hold_mode(self.app.editor.project,self.app.editor.project.active_mode,
                reverse[self.quick_mode_key.get()],reverse[self.quick_execute_key.get()],int(self.quick_return_ms.get())))
            self.app._mode_control_enabled=False;self.app.after_edit(False);self.new_binding();self.refresh_list();self.reset_simulator()
            self.save_feedback.set('模式开关和共享执行键已加入工程；Ctrl+S 保存，再应用设备 RAM。')
            self.feedback_label.configure(foreground='#8ee9d8');self.message.set(self.save_feedback.get())
        except (ValueError,KeyError) as error:self.app.report_error(error)
    def record_key(self,event):
        keys=[]
        if event.state & 4: keys.append('CTRL')
        if event.state & 1: keys.append('SHIFT')
        if event.state & 0x20000: keys.append('ALT')
        keys.append(event.keysym)
        try: self.capture_keys(keys)
        except ValueError: pass
        return 'break'

    def new_binding(self):
        self.editing_id=None; owner=self.labels()[self.app.editor.project.active_mode]
        for key,value in dict(owner=owner,target=owner,device='mouse',gesture='组合同时按下',action='下一模式',behavior='单次触发',
                window_ms=200,hold_ms=400,debounce_ms=12,button_policy='正常透传（默认）').items(): self.vars[key].set(str(value))
        self.enabled.set(True); self.keys_var.set('')
        self.save_feedback.set('')
        for key,var in self.button_vars.items(): var.set(key in ('side1','side2'))
        self._loaded_form=self.form_signature();self._loaded_mode=self.app.editor.project.active_mode
        self.update_fields()

    def confirm_form_discard(self):
        return not self.has_pending_form() or messagebox.askokcancel('尚未保存的绑定',
            '此操作会放弃绑定表单中尚未保存的输入。继续？',parent=self.winfo_toplevel())

    def new_binding_from_ui(self):
        if self.confirm_form_discard():
            self.tree.selection_remove(*self.tree.selection());self.new_binding()

    def form_signature(self):
        return repr(([v.get() for v in self.vars.values()],self.keys_var.get(),self.enabled.get(),[v.get() for v in self.button_vars.values()]))

    def has_pending_form(self):return self._loaded_form is not None and self.form_signature()!=self._loaded_form

    def update_fields(self):
        if not hasattr(self,'mouse_form'):return
        if self.vars['device'].get()=='mouse':self.mouse_form.pack(fill='x',pady=10,before=self.input_anchor);self.key_area.pack_forget()
        else:self.mouse_form.pack_forget();self.key_area.pack(fill='x',before=self.input_anchor)
        self._target_combo.configure(state='readonly' if ACTIONS[self.vars['action'].get()] in ('switch_mode','start_trajectory','stop_trajectory','toggle_mode','start_mode_trajectory') else 'disabled')
        wheel=self.vars['device'].get()=='mouse' and any(self.button_vars[k].get() for k in WHEEL_EVENTS)
        gestures=[name for name,value in GESTURES.items() if not wheel or value!='hold']
        self._gesture_combo.configure(values=gestures)
        if self.vars['gesture'].get() not in gestures:self.vars['gesture'].set('单击（释放时）')
        start=ACTIONS[self.vars['action'].get()] in ('start_trajectory','start_current_trajectory','start_mode_trajectory')
        values=['单次触发']
        if start and not wheel:
            if GESTURES[self.vars['gesture'].get()]!='click':values.append('按住执行')
            if ACTIONS[self.vars['action'].get()]=='start_trajectory':values.append('再次触发停止')
        self._behavior_combo.configure(values=values,state='readonly' if start else 'disabled')
        if self.vars['behavior'].get() not in values:self.vars['behavior'].set('单次触发')
        self.behavior_hint.set('滚轮是瞬时事件，只能单次执行；按住其他键后在组合窗口内滚动可组成组合。' if wheel else
            '按下即启动、松开停止：选“组合同时按下”与“按住执行”。' if start else
            '切换模式只选择轨迹，执行方式固定为“单次触发”。' if ACTIONS[self.vars['action'].get()]!='stop_trajectory' else
            '停止动作只触发一次；是否返回点1在“运行与停止”中设置。')
    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for mode in self.modes:
            for b in mode.triggers:
                if self.window is None and mode is not self.app.editor.mode and b.action!='start_current_trajectory':continue
                combo='+'.join(BUTTON_NAMES.get(key,key) for key in b.buttons)
                gesture=next(k for k,v in GESTURES.items() if v==b.gesture)
                action=next(k for k,v in ACTIONS.items() if v==b.action)
                self.tree.insert('', 'end',iid=b.trigger_id,values=(mode.name,combo,('✓ ' if b.enabled else '○ ')+gesture,action))
        conflicts=find_conflicts(self.bindings(),bypass=self.app.editor.project.bypass)
        self.conflict_text.configure(text='\n'.join(c.message for c in conflicts[:4]) or '当前配置无冲突')
        self._synced=self.project_signature()
    def project_signature(self):
        return repr(([(m.mode_id,m.name,m.triggers) for m in self.modes],self.app.editor.project.bypass,
                     self.app.editor.project.active_mode if self.window is None else None))
    def sync_project(self):
        mode=self.app.editor.mode
        if getattr(self,'_quick_mode_id',None)!=mode.mode_id:
            self._quick_mode_id=mode.mode_id
            own=next((t.buttons[0] for t in mode.triggers if t.enabled and t.action=='toggle_mode' and len(t.buttons)==1),None)
            used={k for m in self.modes if m is not mode for t in m.triggers if t.enabled and t.action=='toggle_mode' for k in t.buttons}
            choice=own or next((k for k in ('side1','side2','middle','right') if k not in used and k!=self.app.editor.project.bypass['button']),'side1')
            self.quick_mode_key.set(BUTTON_NAMES[choice])
            self.quick_return_ms.set(str(mode.run_policy['return_ms'] if own else max(250,min(60000,sum(p.duration_ms for p in mode.points[:-1])))))
        signature=self.project_signature()
        if signature==self._synced:
            if self._loaded_mode!=self.app.editor.project.active_mode and not self.has_pending_form():self.new_binding()
            return
        if self.has_pending_form():
            self.conflict_text.configure(text='绑定表单尚未保存；先保存绑定或点击“新增”重填。')
            return
        self.refresh_list()
        labels=self.labels(); self._owner_combo.configure(values=labels); self._target_combo.configure(values=labels)
        for key in ('owner','target'):
            if self.vars[key].get() not in labels: self.vars[key].set(labels[self.app.editor.project.active_mode])
        if self.editing_id and self.editing_id in {b.trigger_id for b in self.bindings()}:
            self.tree.selection_set(self.editing_id); self.selected()
        else: self.new_binding()
        self.reset_simulator()
    def selected(self,event=None):
        if not self.tree.selection(): return
        if event is not None and self.tree.selection()[0]==self.editing_id and self.has_pending_form():return
        if event is not None and self.tree.selection()[0]!=self.editing_id and not self.confirm_form_discard():
            if self.editing_id:self.tree.selection_set(self.editing_id)
            else:self.tree.selection_remove(*self.tree.selection())
            return
        self.editing_id=self.tree.selection()[0]
        for index,m in enumerate(self.modes):
            for b in m.triggers:
                if b.trigger_id!=self.editing_id: continue
                self.vars['owner'].set(self.labels()[index]); self.vars['device'].set(b.device)
                for key,mapping in [('gesture',GESTURES),('action',ACTIONS),('behavior',BEHAVIORS),('button_policy',POLICIES)]:
                    self.vars[key].set(next(k for k,v in mapping.items() if v==getattr(b,key)))
                for key in ('window_ms','hold_ms','debounce_ms'): self.vars[key].set(str(getattr(b,key)))
                target=next((i for i,m in enumerate(self.modes) if m.mode_id==b.target_mode),index)
                self.vars['target'].set(self.labels()[target]); self.enabled.set(b.enabled)
                for key,var in self.button_vars.items(): var.set(b.device=='mouse' and key in b.buttons)
                self.keys_var.set('+'.join(b.buttons) if b.device=='keyboard' else '')
        self._loaded_form=self.form_signature();self._loaded_mode=self.app.editor.project.active_mode
        self.update_fields()
    def form_binding(self):
        device=self.vars['device'].get()
        keys=[key for key,var in self.button_vars.items() if var.get()] if device=='mouse' else normalize_keys(self.keys_var.get().split('+'))
        action=ACTIONS[self.vars['action'].get()]
        binding=Trigger(buttons=keys,device=device,gesture=GESTURES[self.vars['gesture'].get()],action=action,
            target_mode=self.mode_for(self.vars['target'].get()).mode_id if action in ('switch_mode','start_trajectory','stop_trajectory','toggle_mode','start_mode_trajectory') else None,
            behavior=BEHAVIORS[self.vars['behavior'].get()],button_policy=POLICIES[self.vars['button_policy'].get()],
            window_ms=int(self.vars['window_ms'].get()),hold_ms=int(self.vars['hold_ms'].get()),
            debounce_ms=int(self.vars['debounce_ms'].get()),enabled=self.enabled.get())
        if self.editing_id: binding.trigger_id=self.editing_id
        binding.validate(); return binding
    def save_binding(self):
        try:
            binding=self.form_binding(); owner=self.mode_for(self.vars['owner'].get())
            candidates=[b for b in self.bindings() if b.trigger_id!=binding.trigger_id]+[binding]
            errors=[c for c in find_conflicts(candidates) if c.severity=='error']
            if errors: raise ValueError(errors[0].message)
            def change():
                for mode in self.modes: mode.triggers=[b for b in mode.triggers if b.trigger_id!=binding.trigger_id]
                owner.triggers.append(binding)
            self.app.editor.apply(change)
            self._loaded_form=self.form_signature();self.editing_id=binding.trigger_id
            self.app.after_edit(False)
            self.editing_id=binding.trigger_id; self.refresh_list(); self.tree.selection_set(binding.trigger_id);self.selected();self.reset_simulator()
            self.save_feedback.set('绑定已保存到工程，可继续点击“新增”。')
            self.feedback_label.configure(foreground='#8ee9d8')
            self.message.set('绑定已加入工程。Ctrl+S 保存文件；设备使用需另行“应用工程到 RAM”。')
            return True
        except (ValueError,KeyError) as error:
            text=f'保存失败：{error}'
            self.message.set(text);self.save_feedback.set(text);self.feedback_label.configure(foreground='#ff9a9a')
            self.app.report_error(ValueError(text))
            return False
    def delete_binding(self):
        if not self.confirm_form_discard():return
        if not self.editing_id: return
        def change():
            for mode in self.modes: mode.triggers=[b for b in mode.triggers if b.trigger_id!=self.editing_id]
        self.app.editor.apply(change); self.app.after_edit(False); self.new_binding(); self.refresh_list(); self.reset_simulator()
    def restore_defaults(self):
        if not self.confirm_form_discard():return
        mode=self.mode_for(self.vars['owner'].get())
        self.app.editor.apply(lambda:setattr(mode,'triggers',[])); self.app.after_edit(False)
        self.new_binding(); self.refresh_list(); self.reset_simulator(); self.message.set('当前模式恢复默认：无自定义绑定，正常透传')
    def reset_simulator(self):
        if self.machine:
            for event in self.machine.reset(): self.app.dispatch_trigger(event)
        self.sim_ms=0; self.machine=TriggerMachine(self.bindings(),bypass=self.app.editor.project.bypass)
        self.sync_sim_scope()
        for var in self.sim_vars.values(): var.set(False)
        self.update_sim_state()
    def sim_events(self,events):
        for event in events:
            self.app.dispatch_trigger(event)
            self.sync_sim_scope()
            self.log.configure(state='normal'); self.log.insert('end',f'{self.sim_ms:g}ms  {event.action} / {event.phase}\n')
            if int(self.log.index('end-1c').split('.')[0])>100: self.log.delete('1.0','2.0')
            self.log.see('end'); self.log.configure(state='disabled')
        self.update_sim_state()
    def sim_feed(self,key,down):
        self.sync_sim_scope()
        self.sim_vars[key].set(down); self.sim_events(self.machine.feed(key,down,self.sim_ms))
    def sync_sim_scope(self):self.machine.set_scope(self.app.editor.mode.mode_id,bool(getattr(self.app,'_mode_control_enabled',False)))
    def sim_wheel(self,delta):
        self.sync_sim_scope();self.sim_events(self.machine.feed_wheel(delta,self.sim_ms))
    def sim_advance(self,delta):
        self.sync_sim_scope()
        self.sim_ms+=delta; self.sim_events(self.machine.advance(self.sim_ms))
    def sim_press_form(self):
        for key,var in self.button_vars.items():
            if var.get() and key not in WHEEL_EVENTS:self.sim_feed(key,True)
        self.sim_advance(20)
        for key in WHEEL_EVENTS:
            if self.button_vars[key].get():self.sim_wheel(1 if key=='wheel_up' else -1)
    def sim_release_all(self):
        for key in MOUSE_BUTTONS[:5]: self.sim_feed(key,False)
        self.sim_advance(20)
    def update_sim_state(self):
        down='+'.join(BUTTON_NAMES[key] for key in MOUSE_BUTTONS if key in self.machine.raw['mouse'])
        self.sim_state.configure(text=f'{self.sim_ms:g}ms · {down or "全部释放"}'+(' · BYPASS' if self.machine.bypass_active else ''))
    def destroy(self):
        if self.machine:
            for event in self.machine.reset(): self.app.dispatch_trigger(event)
        super().destroy()
