"""V2 navigation and draft adapter around the existing production editor."""
from copy import deepcopy
import tkinter as tk
from tkinter import ttk
from .workspace_ui import WorkspaceLayout
from .ui_components import ScrollFrame,Disclosure
from .triggers import Trigger,MOUSE_BUTTONS

NAMES={'left':'左键','right':'右键','middle':'中键','side1':'侧键1 · HID Button4','side2':'侧键2 · HID Button5',
       'wheel_up':'滚轮向前','wheel_down':'滚轮向后'}

def parse_buttons(text):
    reverse={v:k for k,v in NAMES.items()}
    def key(part):
        if 'HID Button4' in part:return 'side1'
        if 'HID Button5' in part:return 'side2'
        return reverse.get(part.strip(),part.strip())
    result=[key(part) for part in text.split('+') if part.strip()]
    if not result or len(set(result))!=len(result) or any(k not in NAMES for k in result):
        raise ValueError('请选择鼠标键；组合用 + 连接，且不能重复')
    return result

class StudioV2Layout(WorkspaceLayout):
    def build_workspaces(self):
        self._drafts={};self._point_drafts={};self._v2_busy=True;self._draft_job=None;self.page='home'
        super().build_workspaces()
        nav=ttk.Frame(self,padding=(16,4));nav.pack(fill='x',before=self.workspaces)
        self.button(nav,'我的模式',lambda:self.navigate('home')).pack(side='left')
        self.button(nav,'模式设置中心',lambda:self.navigate('center')).pack(side='left',padx=8)
        self.breadcrumb=tk.StringVar(value='我的模式')
        ttk.Label(nav,textvariable=self.breadcrumb,style='Section.TLabel').pack(side='left',padx=16)
        self.button(nav,'设备与保存',lambda:self.navigate('device')).pack(side='right')
        self.home_workspace=ttk.Frame(self.workspaces);self.center_workspace=ttk.Frame(self.workspaces)
        self.workspaces.add(self.home_workspace,text='我的模式');self.workspaces.add(self.center_workspace,text='模式设置中心')
        top=ttk.Frame(self.home_workspace,padding=20);top.pack(fill='x')
        ttk.Label(top,text='我的模式',style='Title.TLabel').pack(side='left')
        self.button(top,'＋ 新建模式',self.add_mode,style='Accent.TButton').pack(side='right')
        self.button(top,'一键应用八模式快捷键模板',self.apply_eight_mode_template).pack(side='right',padx=12)
        ttk.Label(self.home_workspace,text='选择一个模式，随时编辑轨迹、按键和运行方式。修改自动保留在当前工程草稿。',
                  style='Muted.TLabel',padding=(20,0)).pack(anchor='w')
        self.cards_scroll=ScrollFrame(self.home_workspace);self.cards_scroll.pack(fill='both',expand=True,padx=10,pady=16)
        self.mode_cards=self.cards_scroll.body
        self.center_name=tk.StringVar()
        ttk.Label(self.center_workspace,textvariable=self.center_name,style='Title.TLabel',padding=24).pack(anchor='w')
        tiles=ttk.Frame(self.center_workspace,padding=24);tiles.pack(fill='x')
        for i,(key,title,description) in enumerate([('editor','编辑轨迹','完整坐标画布、控制点、底图与贝塞尔手柄'),
                ('bindings','按键设置','进入或退出模式 · 全局或独立执行键'),('run','运行方式','循环、停止、返回时间与缓动'),
                ('preview','预览测试','本地播放与平滑扰动，另可使用离线设备模拟')]):
            box=ttk.Labelframe(tiles,text=title,padding=24);box.grid(row=i//2,column=i%2,sticky='nsew',padx=8,pady=8)
            ttk.Label(box,text=description,style='Muted.TLabel',wraplength=400).pack(anchor='w',pady=(0,16))
            self.button(box,'进入'+title,lambda k=key:self.navigate(k),style='Accent.TButton').pack(anchor='w')
        tiles.columnconfigure(0,weight=1);tiles.columnconfigure(1,weight=1)
        self._build_simple_bindings()
        self.style=ttk.Style(self);self.style.layout('V2.TNotebook.Tab',[]);self.workspaces.configure(style='V2.TNotebook')
        self.mode_tools.configure(style='V2.TNotebook')
        for variables in (self.point_vars,self.image_vars,self.preview_vars):
            for var in variables.values():var.trace_add('write',self.draft_changed)
        self._v2_busy=False
        self.navigate('home')

    def _build_simple_bindings(self):
        self.simple_host=ttk.Frame(self.mode_tools);self.mode_tools.add(self.simple_host,text='按键设置')
        scroll=ScrollFrame(self.simple_host);scroll.pack(fill='both',expand=True)
        p=scroll.body
        ttk.Label(p,text='当前模式的按键',style='Section.TLabel').pack(anchor='w',pady=(0,12))
        self.button(p,'一键应用八模式快捷键模板',self.apply_eight_mode_template).pack(anchor='w',pady=(0,12))
        self.simple_vars={k:tk.StringVar(value=v) for k,v in dict(selector='',gesture='单击',
             global_execute=NAMES['left'],independent='继承全局执行键',execute=NAMES['left'],execute_gesture='按住执行',mapping='侧键1 = Button4').items()}
        self.simple_widgets={}
        for key,title,values in [('selector','进入 / 再按退出',list(NAMES.values())),('gesture','模式键触发',['单击','长按','组合同时按下']),
                 ('global_execute','全局执行键',list(NAMES.values())),('independent','本模式执行键',['继承全局执行键','本模式使用独立执行键']),
                 ('execute','独立执行键',list(NAMES.values())),('execute_gesture','执行方式',['按住执行','单击执行','长按执行','组合同时按下']),
                 ('mapping','侧键显示映射',['侧键1 = Button4','侧键1 = Button5'])]:
            row=ttk.Frame(p);row.pack(fill='x',pady=7)
            ttk.Label(row,text=title,width=20).pack(side='left')
            combo=ttk.Combobox(row,textvariable=self.simple_vars[key],values=values,width=36,
                               state='normal' if key in ('selector','global_execute','execute') else 'readonly')
            combo.pack(side='left',padx=12);self.simple_widgets[key]=row
        ttk.Label(p,text='组合可以输入“侧键1 · HID Button4 + 中键”。滚轮为瞬时事件，只能单击执行。\n'
                  '模式选择不会开始轨迹；再次选择关闭。物理按键继续透传。侧键映射只改变显示，不改 HID 解析。',
                  style='Muted.TLabel',wraplength=800).pack(anchor='w',pady=14)
        self.simple_feedback=tk.StringVar(value='修改自动保留；运行和返回策略在“运行方式”中设置。')
        ttk.Label(p,textvariable=self.simple_feedback,style='Accent.TLabel',wraplength=850).pack(anchor='w',pady=12)
        advanced=Disclosure(p,'高级：原有触发规则编辑器');advanced.pack(fill='x',pady=14)
        self.button(advanced.body,'打开高级触发器',lambda:self.navigate('advanced')).pack(anchor='w')
        self.button(advanced.body,'全局纯物理 Bypass',lambda:self.navigate('bypass')).pack(anchor='w',pady=8)
        for var in self.simple_vars.values():var.trace_add('write',self.simple_changed)

    def apply_eight_mode_template(self):
        from .mode_controls import configure_eight_mode_template
        if not self.flush_drafts(strict=True):return False
        self._v2_busy=True
        try:
            self.editor.apply(lambda:configure_eight_mode_template(self.editor.project))
            self.after_edit(False);self._sync_simple();self.refresh_cards()
            self.status.set('八模式模板已保留在草稿，可撤销；全部继承左键，轨迹与运行/返回策略保留。')
            return True
        except ValueError as error:
            self.report_error(error);return False
        finally:self._v2_busy=False

    def draft_changed(self,*_):
        if self._v2_busy or self._refreshing:return
        self.dirty=True;self.update_title()
        if self._draft_job:self.after_cancel(self._draft_job)
        self._draft_job=self.after_idle(self.flush_drafts)

    def preview_values(self):
        p=self.editor.mode.perturbation
        if 'amplitude' in p:
            return dict(enabled='关闭',amplitude_x=round(p['amplitude']) or 3,amplitude_y=round(p['amplitude']) or 3,
                        frequency_hz=p['frequency_hz'],smoothness='更平滑',max_deviation=round(p['amplitude']) or 8,
                        fixed_seed_enabled='固定种子' if p['amplitude'] else '每次不同',seed=p['seed'])
        return dict(enabled='开启' if p['enabled'] else '关闭',amplitude_x=p['amplitude_x'],amplitude_y=p['amplitude_y'],
                    frequency_hz=f"{1000/p['period_ms']:.9g}",smoothness=['线性','平滑','更平滑'][p['smoothness']],max_deviation=p['max_deviation'],
                    fixed_seed_enabled='固定种子' if p['fixed_seed_enabled'] else '每次不同',seed=p['seed'])

    def mode_tool_changed(self,event=None):
        if self.page in ('run','advanced','bypass'):super().mode_tool_changed(event)

    def workspace_changed(self,event=None):
        if self.page in ('run','advanced','bypass'):super().workspace_changed(event)

    def ensure_mode_tool(self,key):
        panel=super().ensure_mode_tool(key)
        if not getattr(panel,'_v2_traced',False):
            variables=list(self.panel_variables(panel).values())
            if key=='triggers':variables+=list(panel.button_vars.values())+[panel.keys_var,panel.enabled]
            for var in variables:var.trace_add('write',self.draft_changed)
            panel._v2_traced=True
        return panel

    def panel_variables(self,panel):
        return panel.vars if hasattr(panel,'vars') else {'button':panel.button_var}

    def flush_drafts(self,strict=False):
        if self._draft_job:
            self.after_cancel(self._draft_job)
        self._draft_job=None
        if self._v2_busy:return True
        self._v2_busy=True
        try:
            for name,variables,action in [('point',self.point_vars,self.apply_point),('image',self.image_vars,self.apply_image),
                                          ('preview',self.preview_vars,self.apply_preview)]:
                baseline=getattr(self,'_loaded_'+name+'_form',None)
                if baseline and any(v.get()!=baseline.get(k) for k,v in variables.items()):action()
            for attr,action_name in [('run_panel','save_rule'),('trigger_manager','save_binding'),('bypass_panel','save_config')]:
                panel=getattr(self,attr,None)
                if panel is not None and panel.winfo_exists() and panel.has_pending_form():getattr(panel,action_name)()
        finally:self._v2_busy=False
        pending=self.has_pending_forms()
        if not pending:self._point_drafts.pop(getattr(self,'_form_selection',None),None)
        if strict and (pending or self._drafts or self._point_drafts or getattr(self,'_simple_pending',False)):
            self.report_error(ValueError('工程中仍有未完成或无效输入；返回对应模式修正后再保存或应用设备。'))
            return False
        return not pending

    def _stash_draft(self):
        self.flush_drafts()
        data={'selection':self.editor.selection.copy()}
        if getattr(self,'_simple_pending',False):data['simple']={k:v.get() for k,v in self.simple_vars.items()}
        for name,variables in [('point',self.point_vars),('image',self.image_vars),('preview',self.preview_vars)]:
            baseline=getattr(self,'_loaded_'+name+'_form',None)
            if baseline and any(v.get()!=baseline.get(k) for k,v in variables.items()):data[name]={k:v.get() for k,v in variables.items()}
        for attr in ('trigger_manager','run_panel','bypass_panel'):
            panel=getattr(self,attr,None)
            if panel is not None and panel.winfo_exists() and panel.has_pending_form():
                data[attr]={k:v.get() for k,v in self.panel_variables(panel).items()}
                if attr=='trigger_manager':data['trigger_extra']=(panel.editing_id,{k:v.get() for k,v in panel.button_vars.items()},panel.keys_var.get(),panel.enabled.get())
        if len(data)>1:self._drafts[self.editor.mode.mode_id]=data
        else:self._drafts.pop(self.editor.mode.mode_id,None)

    def enter_mode(self,index):
        if not 0<=index<len(self.editor.project.modes):return
        self.stop_playback()
        self._stash_draft();self._v2_busy=True
        try:
            self.editor.cancel();self.editor.project.active_mode=index;self.editor.selection.clear()
            self._loaded_point_form=self._loaded_image_form=self._loaded_preview_form=None
            for attr in ('run_panel','trigger_manager','bypass_panel'):
                panel=getattr(self,attr,None)
                if panel is not None:panel._loaded_form=None
            self.refresh_all();self.compiled_stale=True;self.schedule_compile()
            data=self._drafts.pop(self.editor.mode.mode_id,None)
            if data:
                self.editor.selection=data['selection'];self.refresh_selection()
                for name in ('point','image','preview'):
                    for k,value in data.get(name,{}).items():getattr(self,name+'_vars')[k].set(value)
                for attr in ('trigger_manager','run_panel','bypass_panel'):
                    panel=getattr(self,attr,None)
                    if panel is not None:
                        for k,value in data.get(attr,{}).items():self.panel_variables(panel)[k].set(value)
                if 'trigger_extra' in data:
                    panel=self.trigger_manager;panel.editing_id,buttons,keys,enabled=data['trigger_extra']
                    for k,value in buttons.items():panel.button_vars[k].set(value)
                    panel.keys_var.set(keys);panel.enabled.set(enabled)
            self._sync_simple()
            if data and 'simple' in data:
                for k,value in data['simple'].items():self.simple_vars[k].set(value)
                self._simple_pending=True
        finally:self._v2_busy=False
        self.navigate('center');self.update_title()

    def refresh_selection(self):
        self._ensure_point_tokens()
        key=(self.editor.mode.mode_id,tuple(self.editor.mode.points[i]._studio_draft_id for i in sorted(self.editor.selection) if i<len(self.editor.mode.points)))
        previous=getattr(self,'_form_selection',None)
        baseline=getattr(self,'_loaded_point_form',None)
        if previous!=key:
            if baseline and any(v.get()!=baseline.get(k) for k,v in self.point_vars.items()):
                self._point_drafts[previous]=dict({k:v.get() for k,v in self.point_vars.items()},
                    _order_dirty=self.point_vars['order'].get()!=baseline.get('order'))
            self._loaded_point_form=None
        super().refresh_selection();self._form_selection=key
        if previous!=key and key in self._point_drafts:
            busy=self._v2_busy;self._v2_busy=True
            try:
                values=self._point_drafts[key]
                for k,value in values.items():
                    if k not in self.point_vars:continue
                    if k=='order' and not values.get('_order_dirty'):value=str(min(self.editor.selection)+1)
                    self.point_vars[k].set(value)
            finally:self._v2_busy=busy
        self._prune_point_drafts()

    def _ensure_point_tokens(self):
        import uuid
        for mode in self.editor.project.modes:
            used=set()
            for point in mode.points:
                token=getattr(point,'_studio_draft_id',None)
                if not token or token in used:point._studio_draft_id=uuid.uuid4().hex
                used.add(point._studio_draft_id)

    def _prune_point_drafts(self):
        self._ensure_point_tokens()
        alive={m.mode_id:{p._studio_draft_id for p in m.points} for m in self.editor.project.modes}
        self._point_drafts={k:v for k,v in self._point_drafts.items() if k and k[0] in alive and set(k[1])<=alive[k[0]]}

    def navigate(self,page):
        if not self._v2_busy:self.flush_drafts()
        self.page=page
        if hasattr(self,'run_panel'):self.run_panel.virtual_up()
        if hasattr(self,'bypass_panel'):self.bypass_panel.release_virtual()
        panel={'home':self.home_workspace,'center':self.center_workspace,'editor':self.edit_workspace,'preview':self.edit_workspace,
               'device':self.device_workspace}.get(page,self.modes_workspace)
        self.workspaces.select(panel)
        if page=='home':self.refresh_cards()
        if page=='bindings':
            if not getattr(self,'_simple_pending',False):self._sync_simple()
            self.mode_tools.select(self.simple_host)
        if page in ('run','advanced','bypass'):
            key='triggers' if page=='advanced' else page
            self.ensure_mode_tool(key);self.mode_tools.select(self.mode_hosts[key])
        if page=='preview':self.notebook.select(self.inspector_scrolls['preview'])
        self.center_name.set(self.editor.mode.name+' · 模式设置中心')
        label={'home':'我的模式','center':'模式设置中心','editor':'编辑轨迹','preview':'预览测试','bindings':'按键设置','run':'运行方式','device':'设备与保存','advanced':'高级触发器','bypass':'纯物理 Bypass'}[page]
        self.breadcrumb.set(label if page in ('home','device') else self.editor.mode.name+' / '+label)

    def refresh_cards(self):
        for child in self.mode_cards.winfo_children():child.destroy()
        for i,mode in enumerate(self.editor.project.modes):
            box=ttk.Labelframe(self.mode_cards,text=f'{i+1:02}  {mode.name}',padding=18)
            box.grid(row=i//3,column=i%3,sticky='nsew',padx=8,pady=8)
            own=next((t for t in mode.triggers if t.enabled and t.action=='toggle_mode'),None)
            execute=next((t for m in self.editor.project.modes for t in m.triggers if t.enabled and
                         (t.action in ('start_trajectory','start_mode_trajectory') and t.target_mode==mode.mode_id)),None)
            if execute is None:execute=next((t for m in self.editor.project.modes for t in m.triggers if t.enabled and t.action=='start_current_trajectory'),None)
            names=self.button_labels()
            combo=lambda t:' + '.join(names.get(k,k) for k in t.buttons) if t else '未设置'
            ttk.Label(box,text=f'切换：{combo(own)}\n执行：{combo(execute)}\n时长：{sum(p.duration_ms for p in mode.points[:-1])/1000:g} 秒',
                      style='Muted.TLabel',wraplength=340,justify='left').pack(anchor='w',pady=(0,12))
            self.button(box,'打开模式',lambda n=i:self.enter_mode(n),style='Accent.TButton').pack(fill='x')
            row=ttk.Frame(box);row.pack(fill='x',pady=(10,0))
            for title,method in [('复制',self.copy_mode),('重命名',self.rename_mode),('删除',self.delete_mode)]:
                self.button(row,title,lambda n=i,f=method:self.card_action(n,f)).pack(side='left',padx=3)
        for i in range(3):self.mode_cards.columnconfigure(i,weight=1)

    def card_action(self,index,action):
        self.enter_mode(index);action();self.navigate('home')

    def simple_changed(self,*_):
        if self._v2_busy or self._refreshing:return
        if self.simple_vars['mapping'].get()!=getattr(self,'_mapping_last',self.simple_vars['mapping'].get()):
            pending=getattr(self,'_simple_pending',False)
            raw={k:v.get() for k,v in self.simple_vars.items()} if pending else None
            self.editor.apply(lambda:self.editor.project.editor_state.update(side_label_swap=self.simple_vars['mapping'].get().endswith('Button5')))
            self._v2_busy=True
            try:
                self.after_edit(False);self._sync_simple()
                if raw:
                    for k,value in raw.items():self.simple_vars[k].set(value)
                    self._simple_pending=True
            finally:self._v2_busy=False
            return
        self.simple_widgets['execute'].pack_forget()
        if self.simple_vars['independent'].get()=='本模式使用独立执行键':self.simple_widgets['execute'].pack(fill='x',pady=7)
        try:
            selector=parse_buttons(self.simple_vars['selector'].get())
            execute=parse_buttons(self.simple_vars['execute' if self.simple_vars['independent'].get()=='本模式使用独立执行键' else 'global_execute'].get())
            if set(selector)==set(execute):raise ValueError('模式键与执行键须不同')
            mode=self.editor.mode
            state=deepcopy(self.editor.project.editor_state.get('v2_controls',{}));config=state.setdefault(mode.mode_id,{})
            old_ids=set(config.get('ids',[]));shared_id=state.get('_shared_id')
            if not config:
                own=next((t for t in mode.triggers if t.enabled and t.action=='toggle_mode'),None)
                if own:old_ids.add(own.trigger_id)
            candidates=deepcopy(self.editor.project)
            target=candidates.modes[candidates.active_mode]
            for m in candidates.modes:m.triggers=[t for t in m.triggers if t.trigger_id not in old_ids]
            gesture={'单击':'click','长按':'hold','组合同时按下':'chord'}[self.simple_vars['gesture'].get()]
            behavior='hold' if self.simple_vars['execute_gesture'].get()=='按住执行' else 'once'
            eg='chord' if behavior=='hold' or self.simple_vars['execute_gesture'].get()=='组合同时按下' else 'hold' if self.simple_vars['execute_gesture'].get()=='长按执行' else 'click'
            if any(k.startswith('wheel') for k in selector) and gesture!='click':raise ValueError('滚轮为瞬时事件，请选择单击')
            if any(k.startswith('wheel') for k in execute) and (behavior=='hold' or eg!='click'):raise ValueError('滚轮不能持续按住，请选择单击执行')
            select=Trigger(buttons=selector,gesture=gesture,action='toggle_mode',target_mode=mode.mode_id)
            independent=self.simple_vars['independent'].get()=='本模式使用独立执行键'
            run=Trigger(buttons=execute,gesture=eg,action='start_mode_trajectory',target_mode=mode.mode_id,behavior=behavior)
            target.triggers.append(select)
            target.triggers.append(run)
            config.update(ids=[select.trigger_id,run.trigger_id],selector=selector,execute=execute,
                          independent=independent,gesture=gesture,execute_gesture=eg,behavior=behavior)
            state['_global_execute']=parse_buttons(self.simple_vars['global_execute'].get())
            for m in candidates.modes:
                inherited=state.get(m.mode_id)
                if inherited and not inherited.get('independent'):
                    inherited['execute']=state['_global_execute'].copy()
                    for t in m.triggers:
                        if t.trigger_id in inherited.get('ids',[]) and t.action=='start_mode_trajectory':t.buttons=state['_global_execute'].copy()
                # Legacy shared execution continues to serve modes not yet managed by V2.
                for t in m.triggers:
                    if t.action=='start_current_trajectory':t.buttons=state['_global_execute'].copy()
            candidates.editor_state['v2_controls']=state;candidates.editor_state['side_label_swap']=self.simple_vars['mapping'].get().endswith('Button5')
            if behavior=='hold':target.run_policy['run_mode']='hold'
            elif target.run_policy['run_mode']=='hold':target.run_policy['run_mode']='once'
            candidates.validate()
            def change():
                for original,changed in zip(self.editor.project.modes,candidates.modes):original.triggers=changed.triggers;original.run_policy=changed.run_policy
                self.editor.project.editor_state=candidates.editor_state
            self._v2_busy=True
            try:self.editor.apply(change);self.after_edit(False)
            finally:self._v2_busy=False
            self.simple_feedback.set('已保留在工程草稿；保存文件和应用设备 RAM 分别操作。')
            self._simple_pending=False
        except (ValueError,KeyError) as error:
            self._simple_pending=True;self.simple_feedback.set(str(error));self.dirty=True;self.update_title()

    def _sync_simple(self):
        busy=self._v2_busy;self._v2_busy=True
        try:
            state=self.editor.project.editor_state.get('v2_controls',{});config=state.get(self.editor.mode.mode_id,{})
            own=next((t for t in self.editor.mode.triggers if t.action=='toggle_mode'),None)
            shared=next((t for m in self.editor.project.modes for t in m.triggers if t.action=='start_current_trajectory'),None)
            names=self.button_labels()
            combo=lambda keys:' + '.join(names.get(k,k) for k in keys)
            self.simple_vars['selector'].set(combo(config.get('selector',own.buttons if own else [])))
            self.simple_vars['global_execute'].set(combo(state.get('_global_execute',shared.buttons if shared else ['left'])))
            self.simple_vars['execute'].set(combo(config.get('execute',['left'])))
            self.simple_vars['independent'].set('本模式使用独立执行键' if config.get('independent') else '继承全局执行键')
            self.simple_vars['gesture'].set({'click':'单击','hold':'长按','chord':'组合同时按下'}[config.get('gesture',own.gesture if own else 'click')])
            self.simple_vars['execute_gesture'].set('按住执行' if config.get('behavior','hold')=='hold' else
                '长按执行' if config.get('execute_gesture')=='hold' else '组合同时按下' if config.get('execute_gesture')=='chord' else '单击执行')
            self.simple_vars['mapping'].set('侧键1 = Button5' if self.editor.project.editor_state.get('side_label_swap') else '侧键1 = Button4')
            self._mapping_last=self.simple_vars['mapping'].get();self._simple_pending=False
            for key in ('selector','execute','global_execute'):
                row=self.simple_widgets[key]
                for child in row.winfo_children():
                    if isinstance(child,ttk.Combobox):child.configure(values=list(names.values()))
            self.simple_widgets['execute'].pack_forget()
            if config.get('independent'):self.simple_widgets['execute'].pack(fill='x',pady=7)
        finally:self._v2_busy=busy

    def confirm_inspector_change(self):self.flush_drafts();return True
    def confirm_mode_change(self):self.flush_drafts();return True
    def require_applied_forms(self):return self.flush_drafts(strict=True)
    def settings_mode_selected(self,event=None):self.enter_mode(self.settings_mode_box.current());return True
    def mode_selected(self,event=None):
        if not self._refreshing and self.mode_list.curselection():self.enter_mode(self.mode_list.curselection()[0])
    def show_editor(self):self.navigate('editor')
    def show_device(self):self.navigate('device')
    def show_mode_tool(self,key):
        if self.root.state()=='withdrawn':
            self.root.geometry(f'{min(1366,self.root.winfo_screenwidth()-40)}x{min(768,self.root.winfo_screenheight()-80)}');self.root.deiconify()
        self.navigate({'triggers':'advanced','run':'run','bypass':'bypass'}[key]);return self.ensure_mode_tool(key)

    def button_labels(self):
        labels=dict(NAMES)
        if self.editor.project.editor_state.get('side_label_swap'):
            labels['side1']='侧键2 · HID Button4';labels['side2']='侧键1 · HID Button5'
        return labels

    def add_mode(self):
        self._stash_draft();super().add_mode();self.enter_mode(self.editor.project.active_mode)

    def copy_mode(self):
        self._stash_draft();super().copy_mode();self.enter_mode(self.editor.project.active_mode)

    def _append_mode(self,mode):
        source_id=mode.mode_id
        source_config=deepcopy(self.editor.project.editor_state.get('v2_controls',{}).get(source_id))
        source_ids=[t.trigger_id for t in mode.triggers]
        super()._append_mode(mode)
        if source_config and source_id!=mode.mode_id:
            remap={old:t.trigger_id for old,t in zip(source_ids,mode.triggers)}
            source_config['ids']=[remap[t] for t in source_config.get('ids',[]) if t in remap]
            self.editor.project.editor_state['v2_controls'][mode.mode_id]=source_config

    def delete_mode(self):
        if len(self.editor.project.modes)==1:return self.report_error(ValueError('工程至少保留一个模式'))
        mode_id=self.editor.mode.mode_id
        def change():
            self.editor.project.modes.pop(self.editor.project.active_mode)
            for mode in self.editor.project.modes:mode.triggers=[t for t in mode.triggers if t.target_mode!=mode_id]
            self.editor.project.editor_state.get('v2_controls',{}).pop(mode_id,None)
            self.editor.project.active_mode=0;self.editor.selection.clear()
        self.editor.apply(change);self._drafts.pop(mode_id,None);self._simple_pending=False
        self.after_edit();self.enter_mode(0)
        self._point_drafts={k:v for k,v in self._point_drafts.items() if k and k[0]!=mode_id}

    def delete_points(self):
        super().delete_points()
        self._prune_point_drafts()

    def open_from(self,path):
        self._v2_busy=True
        try:
            super().open_from(path);self._drafts.clear();self._point_drafts.clear();self._sync_simple();self.navigate('home')
        finally:self._v2_busy=False

    def dispose(self):
        if self._draft_job:self.after_cancel(self._draft_job);self._draft_job=None
        super().dispose()

    def new_project(self):
        previous=self.editor
        super().new_project()
        self._v2_busy=True
        try:
            if self.editor is not previous:
                self._drafts.clear();self._point_drafts.clear();self._sync_simple();self.navigate('home')
        finally:self._v2_busy=False

    def dispatch_trigger(self,event):
        from dataclasses import replace
        if event.action=='toggle_mode' and event.phase=='activate':
            index=next((i for i,m in enumerate(self.editor.project.modes) if m.mode_id==event.target_mode),None)
            if index is None:return
            enabled=not self._mode_control_enabled or self.editor.project.active_mode!=index
            page=self.page;self.stop_playback();self.enter_mode(index);self._mode_control_enabled=enabled;self.navigate(page)
            self.status.set('本地模式：'+self.editor.mode.name+(' · 已开启，等待执行键' if enabled else ' · 已关闭'))
            return
        if event.action=='start_mode_trajectory':
            if not self._mode_control_enabled or event.target_mode!=self.editor.mode.mode_id:return
            event=replace(event,action='start_trajectory')
        elif event.action=='start_current_trajectory':
            if any(t.enabled and t.action=='start_mode_trajectory' and t.target_mode==self.editor.mode.mode_id for m in self.editor.project.modes for t in m.triggers):return
        super().dispatch_trigger(event)

    def confirm_unsaved(self):
        from tkinter import messagebox
        if self._flash_saving:
            messagebox.showwarning('请等待保存结束','设备正在保存Flash并重启，请等结果确认后再关闭。',parent=self.root);return False
        self.flush_drafts()
        if not self.dirty and not self.has_pending_forms() and not self._drafts and not self._point_drafts:return True
        answer=messagebox.askyesnocancel('未保存的工程','保存当前工程后继续？\n未完成输入仍保留在内存中，请先修正后保存。',parent=self.root)
        return False if answer is None else self.choose_save() if answer else True
