"""Global physical-only control in the existing Studio/SDK/C simulation path."""
import time
import tkinter as tk
from tkinter import ttk
from mouse_fusion.bypass import BUTTONS,MASKS,normalize_config
from .triggers import find_conflicts
from .ui_components import DockablePanel,ScrollFrame,Disclosure

LABELS=('未绑定 · 关闭','左键','右键','中键','侧键1','侧键2')

class BypassPanel(DockablePanel):
    def __init__(self,app,parent=None):
        super().__init__(app,parent,title='纯物理模式 / BYPASS',geometry='850x690',minsize=(760,620))
        self.last_status=None;self.pending=False;self.next_poll=0;self.holding=False;self.sim_mask=0
        self.button_var=tk.StringVar();self.state=tk.StringVar(value='未连接 · 状态未知')
        self.detail=tk.StringVar(value='配置未应用到 MCU');self.result=tk.StringVar(value='通过现有 SDK、本机服务查询；模拟按键仅在模拟后端可用。')
        top=ttk.Frame(self,padding=16);top.pack(fill='x')
        ttk.Label(top,text='纯物理旁路',style='Section.TLabel').pack(side='left')
        app.button(top,'连接设备' if app.live_device else '连接模拟器',lambda:app.request_api('connect')).pack(side='right')
        scroll=ScrollFrame(self);scroll.pack(fill='both',expand=True);body=scroll.body
        settings=ttk.Labelframe(body,text='全局旁路键 · 不占用轨迹槽',padding=14);settings.pack(fill='x')
        row=ttk.Frame(settings);row.pack(fill='x')
        ttk.Label(row,text='自定义旁路键').pack(side='left',padx=(0,15))
        ttk.Combobox(row,textvariable=self.button_var,values=LABELS,state='readonly',width=22).pack(side='left')
        ttk.Label(row,text='固定按住生效 / 松开退出',style='Accent.TLabel').pack(side='left',padx=15)
        ttk.Label(settings,text='按住时仅使用物理鼠标；松开后需要重新触发轨迹。物理按键正常透传。',wraplength=770,style='Muted.TLabel').pack(anchor='w',pady=12)
        row=ttk.Frame(settings);row.pack(fill='x')
        app.button(row,'保存到工程（可撤销）',self.save_config).pack(side='left',padx=3)
        app.button(row,'恢复默认 / 删除绑定',self.reset_config).pack(side='left',padx=3)
        app.button(row,'应用工程到 RAM',app.apply_project_ram,style='Accent.TButton').pack(side='left',padx=3)
        self.conflict=ttk.Label(settings,foreground='#f8b563',wraplength=760);self.conflict.pack(anchor='w',pady=(12,0))
        self.button_var.trace_add('write',lambda *_:self.refresh_conflicts())
        monitor=ttk.Labelframe(body,text=f'{app.backend_label} BYPASS 状态',padding=14);monitor.pack(fill='x',pady=12)
        ttk.Label(monitor,textvariable=self.state,style='Title.TLabel').pack(anchor='w')
        ttk.Label(monitor,textvariable=self.detail,wraplength=760,style='Muted.TLabel').pack(anchor='w',pady=8)
        row=ttk.Frame(monitor);row.pack(fill='x')
        app.button(row,'查询状态',lambda:app.request_api('get_bypass_status')).pack(side='left',padx=3)
        for label,down in [('模拟按下',True),('模拟松开',False)]:
            button=app.button(row,label,lambda down=down:self.simulate(down));button.pack(side='left',padx=3)
            if app.live_device:button.state(['disabled'])
        app.button(row,'打开轨迹执行',app.show_presets).pack(side='left',padx=3)
        ttk.Label(monitor,text='模拟按下后可在“MCU 离线执行”测试停止；该输入不会读取真实鼠标。\n关闭面板会释放由此面板注入的虚拟按键。',wraplength=760,style='Muted.TLabel').pack(anchor='w',pady=(12,0))
        advanced=Disclosure(body,'高级：状态与兼容性详情');advanced.pack(fill='x',pady=10)
        ttk.Label(advanced.body,textvariable=self.result,wraplength=770).pack(anchor='w',pady=6)
        ttk.Label(advanced.body,text='按下清除待用 AI、轨迹、返回、过渡和未提交辅助；已提交报告无法撤回。\n0.4.1-run 无 BYPASS 能力。当前真实 Flash 持久化未开放。',style='Muted.TLabel',wraplength=770).pack(anchor='w',pady=8)
        self.protocol('WM_DELETE_WINDOW',self.destroy);self.sync_project()

    def config(self):
        try:return normalize_config(dict(button=BUTTONS[LABELS.index(self.button_var.get())],behavior='hold'))
        except ValueError:raise ValueError('请选择有效的旁路键')

    def sync_project(self):
        self.button_var.set(LABELS[BUTTONS.index(self.app.editor.project.bypass['button'])]);self.refresh_conflicts()
        self._loaded_button=self.button_var.get()

    def refresh_conflicts(self):
        try:
            bindings=[b for m in self.app.editor.project.modes for b in m.triggers]
            warnings=find_conflicts(bindings,bypass=self.config())
            warnings=[c.message for c in warnings if c.kind=='bypass']
            pending=self.config()!=self.app.editor.project.bypass
            self.conflict.configure(text=('尚未保存：请先保存到工程。\n' if pending else '')+
                                    ('\n'.join(warnings) if warnings else '未发现与普通鼠标绑定的旁路键冲突。'))
        except ValueError:pass

    def save_config(self):
        try:
            config=self.config()
            self._loaded_button=self.button_var.get()
            self.app.edit(lambda:self.app.editor.apply(lambda:setattr(self.app.editor.project,'bypass',config)),geometry=False)
            self.result.set('旁路键已保存到工程；支持 BYPASS 的固件应用 RAM 后生效。')
        except ValueError as error:self.app.report_error(error)

    def reset_config(self):self.button_var.set(LABELS[0]);self.save_config()

    def require_saved_config(self):
        try:
            if self.config()!=self.app.editor.project.bypass:raise ValueError('旁路键已修改，请先保存到工程')
            return True
        except ValueError as error:self.app.report_error(error);return False

    def has_pending_form(self):
        return getattr(self,'_loaded_button',self.button_var.get())!=self.button_var.get()

    def release_virtual(self):
        if self.holding and not self.app._disposed:self.app.request_api('simulate_mouse_buttons',0)
        self.holding=False

    def simulate(self,down):
        if not self.require_saved_config():return
        button=self.app.editor.project.bypass['button']
        if down and button is None:self.app.report_error(ValueError('请先绑定旁路键并应用 RAM'));return
        if down:
            from .device_data import execution_signature
            if getattr(self.app,'_preset_applied_signature',None)!=execution_signature(self.app.editor.project):
                self.app.report_error(ValueError('请先将当前工程应用到 RAM'));return
            self.sim_mask=MASKS[BUTTONS.index(button)]
        self.holding=down;self.app.request_api('simulate_mouse_buttons',self.sim_mask if down else 0)
        self.app.request_api('get_bypass_status')

    def tick(self):
        caps=self.app.device_capabilities
        if self.app.connected and caps is not None and not caps&0x1000:
            self.state.set('当前固件不支持 BYPASS');self.detail.set('可编辑并保存到工程；需要更新 0.4.2-bypass 后才能应用到设备。');return
        if self.app.connected and not self.pending and time.perf_counter()>=self.next_poll:
            self.pending=True;self.next_poll=time.perf_counter()+.15;self.app.request_api('get_bypass_status')

    def receive(self,method,result,error):
        if method=='get_bypass_status':self.pending=False
        if error:
            if method in ('connect','disconnect','get_bypass_status'):
                self.state.set('状态未知 / 不支持');self.detail.set(str(error));self.next_poll=time.perf_counter()+2
            if method=='simulate_mouse_buttons':self.holding=False
            return
        if method in ('get_bypass_status','set_bypass_config'):
            self.last_status=result
            state='BYPASS 生效 · 仅物理鼠标' if result['active'] else ('安全阻止辅助 · 物理输入尚未有效' if result['inhibited'] else 'BYPASS 未按下 · 旧运动不会恢复')
            if result['button'] is None:state='未绑定 · BYPASS 关闭'
            self.state.set(state)
            self.detail.set(f"MCU 绑定：{LABELS[BUTTONS.index(result['button'])]} · 输入有效：{result['input_valid']} · 等待全组释放：{result['rearm']}\n"
                            f"辅助代际 {result['generation']} · 进入 {result['entries']} 次 · 无法撤回的在途辅助报告：{result['submitted_auxiliary_inflight']}")
        elif method=='disconnect':self.state.set('J4 已断开 · 当前 MCU 状态未知');self.pending=False
        elif method=='apply_project_ram':self.result.set(f'工程已应用到{self.app.backend_label} RAM；不自动启动轨迹。')

    def destroy(self):
        self.release_virtual();super().destroy()
