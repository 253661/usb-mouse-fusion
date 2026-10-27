"""Runnable Studio UI. Editing/preview is local; SDK worker owns communication."""
from copy import deepcopy
from pathlib import Path
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk
from .canvas import TrajectoryCanvas, BG, BLUE, CYAN, ORANGE
from .compiler import compile_mode, export_data
from .connection import StudioConnection
from .model import Editor, Mode, Point, Project
from .project_io import atomic_json, import_image, load_project, save_project
from .triggers import TriggerMachine, normalize_keys
from .studio_v2 import StudioV2Layout
from .workspace_ui import WorkspaceLayout
from .ui_components import Disclosure, MappedCombobox, Tooltip
import uuid

PANEL='#1b2839'
FG='#e4edf6'
MUTED='#91a7bb'


class LegacyStudioApp(WorkspaceLayout,ttk.Frame):
    def __init__(self, root, *, project=None, endpoint=None, c_simulator=None, preset_c_simulator=None,
                 allow_live_device=False,live_com=None,board_id=None,simulate_device_flash=False):
        self.root=root
        self._theme(root)
        super().__init__(root)
        self.pack(fill='both',expand=True)
        self.editor=Editor(project)
        self.path=None; self.dirty=False; self.compiled=None; self.compiled_stale=True
        self.compile_count=0; self._compile_id=None; self._poll_id=None; self._play_id=None
        self.playing=False; self._disposed=False; self.connection=None
        self.active_trajectory_id=None; self.trigger_manager=None
        self._mode_control_enabled=False
        self.keyboard_enabled=tk.BooleanVar(value=False); self._keyboard_machine=None; self._keyboard_signature=None
        self.endpoint=endpoint; self.c_simulator=c_simulator
        self.allow_live_device=allow_live_device;self.live_com=live_com;self.board_id=board_id
        self.live_device=bool(live_com or allow_live_device and endpoint)
        self.backend_label='实机 J4' if self.live_device else '模拟后端'
        self.device_capabilities=None
        self.preset_c_simulator=preset_c_simulator
        self.simulate_device_flash=simulate_device_flash;self._flash_saving=False;self._device_persistence_status={}
        self._preset_applied_revision=None
        self._preset_applied_signature=None;self._preset_applied_crcs={}
        self.connected=False; self._status_pending=False; self._next_status=0
        self._refreshing=False; self._bindings=[]
        self.edit_mode=tk.StringVar(value='轨迹编辑')
        self.flags={k:tk.BooleanVar(value=v) for k,v in
                    dict(grid=True,snap=False,points=True,curves=True,samples=False,perturbed=True).items()}
        self.grid_var=tk.StringVar(value='10')
        self.coordinates=tk.StringVar(value='X 0.00   Y 0.00')
        self.status=tk.StringVar(value='就绪 · 双击空白添加点；拖动空白框选；右键拖动画布')
        self.compile_text=tk.StringVar(value='等待编译')
        self.playhead=tk.DoubleVar(value=0)
        self.time_text=tk.StringVar(value='0 / 0 ms')
        self.device_text=tk.StringVar(value=f'{self.backend_label} · 未连接')
        self._build()
        self.restore_editor_state()
        self.refresh_all(); self.schedule_compile()
        self._poll_id=self.after(50,self.poll_connection)
        for seq,callback in (
            ('<Control-s>',self.choose_save),('<Control-Shift-S>',lambda:self.choose_save(True)),
            ('<Control-o>',self.choose_open),('<Control-z>',self.undo),('<Control-y>',self.redo),
            ('<Control-Shift-Z>',self.redo),('<Control-a>',self.select_all),
            ('<Control-d>',self.duplicate_points),('<Delete>',self.delete_points),
            ('<Key-f>',self.fit_view),('<space>',self.toggle_playback)):
            fid=root.bind(seq,lambda e,cb=callback:self.shortcut(e,cb),add='+')
            self._bindings.append((seq,fid))
        for seq,callback in (('<KeyPress>',self.keyboard_press),('<KeyRelease>',self.keyboard_release),('<FocusOut>',self.keyboard_focus_out)):
            fid=root.bind(seq,callback,add='+'); self._bindings.append((seq,fid))

    @staticmethod
    def _theme(root):
        for name in ('TkDefaultFont','TkTextFont','TkMenuFont'):
            tkfont.nametofont(name,root=root).configure(family='Microsoft YaHei UI',size=11)
        style=ttk.Style(root); style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',11),background=PANEL,foreground=FG)
        style.configure('TFrame',background=PANEL)
        style.configure('TLabel',background=PANEL,foreground=FG)
        style.configure('Muted.TLabel',foreground=MUTED)
        style.configure('Section.TLabel',font=('Microsoft YaHei UI',12,'bold'),foreground=FG)
        style.configure('Disclosure.TButton',padding=(8,6),anchor='w',background=PANEL)
        style.configure('Title.TLabel',font=('Microsoft YaHei UI',15,'bold'),foreground=FG)
        style.configure('Accent.TLabel',foreground=CYAN)
        style.configure('TButton',padding=(8,5),background='#2b4057',foreground=FG,borderwidth=0)
        style.map('TButton',background=[('active','#3d5b7c')])
        style.configure('Accent.TButton',background='#296e72',foreground='#e6fffa')
        style.configure('TEntry',fieldbackground='#101b28',foreground=FG,insertcolor=FG,bordercolor='#395068',padding=5)
        style.configure('TCombobox',fieldbackground='#101b28',foreground=FG,arrowcolor=FG,padding=4)
        style.map('TCombobox',fieldbackground=[('readonly','#101b28')],foreground=[('readonly',FG)])
        style.configure('TCheckbutton',background=PANEL,foreground=FG)
        style.map('TCheckbutton',background=[('active',PANEL)])
        style.configure('TNotebook',background=PANEL,borderwidth=0)
        style.configure('TNotebook.Tab',background='#25364b',foreground=MUTED,padding=(9,8))
        style.map('TNotebook.Tab',background=[('selected','#36536f')],foreground=[('selected',FG)])
        style.configure('TLabelframe',background=PANEL,bordercolor='#34465b')
        style.configure('TLabelframe.Label',background=PANEL,foreground=MUTED)
        style.configure('Treeview',background='#101b28',fieldbackground='#101b28',foreground=FG,rowheight=30,bordercolor='#34465b')
        style.configure('Treeview.Heading',background='#2b4057',foreground=FG)
        style.map('Treeview',background=[('selected','#375a76')],foreground=[('selected','white')])
        root.option_add('*TCombobox*Listbox.background','#152336')
        root.option_add('*TCombobox*Listbox.foreground',FG)

    def button(self,parent,text,command,**kw):
        tip=kw.pop('tip',None)
        b=ttk.Button(parent,text=text,command=command,**kw)
        if tip: b.tooltip=Tooltip(b,tip)
        return b

    def _build(self):
        self.build_workspaces()

    def fields(self,parent,specs):
        variables={}
        for r,(key,label,value,options) in enumerate(specs):
            ttk.Label(parent,text=label,style='Muted.TLabel').grid(row=r,column=0,sticky='w',pady=5)
            var=tk.StringVar(value=str(value)); variables[key]=var
            widget=MappedCombobox(parent,textvariable=var,values=options,state='readonly',width=17) if options else ttk.Entry(parent,textvariable=var,width=20)
            widget.grid(row=r,column=1,sticky='ew',padx=(8,0),pady=4)
        parent.columnconfigure(1,weight=1)
        return variables

    def _build_point_panel(self):
        p=self.point_panel
        self.selection_label=ttk.Label(p,text='选择控制点以编辑',style='Accent.TLabel'); self.selection_label.pack(anchor='w',pady=(0,8))
        self.point_form=form=ttk.Frame(p); form.pack(fill='x')
        self.point_vars=self.fields(form,[('x','X',0,None),('y','Y',0,None),('order','执行顺序',1,None),
            ('duration_ms','下一段时间 (ms)',300,None),('curve','下一段曲线','smooth',['linear','bezier','smooth']),
            ('easing','速度变化','linear',['linear','ease_in','ease_out','ease_in_out'])])
        self.point_apply_button=self.button(self.inspector_scrolls['point'],'应用精确参数',self.apply_point,style='Accent.TButton',tip='将输入值应用到所选点；支持 Ctrl+Z 撤销')
        self.point_apply_button.grid(row=1,column=0,columnspan=2,sticky='ew',padx=12,pady=8)
        self.handle_section=Disclosure(p,'高级：贝塞尔手柄');self.handle_section.pack(fill='x',pady=6)
        handles=ttk.Frame(self.handle_section.body);handles.pack(fill='x')
        self.point_vars.update(self.fields(handles,[('handle_in_x','入手柄 X','',None),('handle_in_y','入手柄 Y','',None),
            ('handle_out_x','出手柄 X','',None),('handle_out_y','出手柄 Y','',None)])
        )
        self.button(self.handle_section.body,'应用手柄参数',self.apply_point).pack(fill='x',pady=8)
        self.button(self.handle_section.body,'重置当前点手柄',self.reset_handles,tip='手柄是世界坐标；留空使用自动手柄').pack(fill='x')
        self.point_hint=ttk.Label(p,text='末点不参与下一段时间计算。',style='Muted.TLabel',wraplength=260)
        self.point_hint.pack(anchor='w',pady=12)

    def _build_transform_panel(self):
        p=self.transform_panel
        ttk.Label(p,text='相对基准点缩放，保持执行顺序',style='Accent.TLabel').pack(anchor='w',pady=(0,12))
        form=ttk.Frame(p); form.pack(fill='x')
        self.transform_vars=self.fields(form,[('dx','整体平移 X',0,None),('dy','整体平移 Y',0,None),
            ('sx','X 缩放系数',1,None),('sy','Y 缩放系数',1,None),('pivot','缩放基准','选区中心',['选区中心','指定点']),
            ('pivot_point','基准点序号',1,None)])
        self.uniform=tk.BooleanVar(value=False)
        ttk.Checkbutton(p,text='等比例（Y 使用 X 系数）',variable=self.uniform).pack(anchor='w',pady=10)
        self.button(self.inspector_scrolls['transform'],'应用点组变换',self.apply_transform,style='Accent.TButton').grid(row=1,column=0,columnspan=2,sticky='ew',padx=12,pady=8)
        for label,sx,sy in [('沿 X 镜像（X 取反）',-1,1),('沿 Y 镜像（Y 取反）',1,-1)]:
            self.button(p,label,lambda sx=sx,sy=sy:self.mirror(sx,sy)).pack(fill='x',pady=4)
        ttk.Label(p,text='0.5 缩小 · 2 放大\n所选点与手柄一起变换。',style='Muted.TLabel',wraplength=260).pack(anchor='w',pady=12)

    def _build_image_panel(self):
        p=self.image_panel
        self.image_name=ttk.Label(p,text='尚未导入参考图片',style='Accent.TLabel',wraplength=300); self.image_name.pack(anchor='w',pady=(0,8))
        self.image_visible=tk.BooleanVar(value=True); self.image_locked=tk.BooleanVar(value=False)
        flags=ttk.Frame(p); flags.pack(fill='x')
        ttk.Checkbutton(flags,text='显示',variable=self.image_visible,command=self.apply_image_flags).pack(side='left')
        ttk.Checkbutton(flags,text='锁定底图',variable=self.image_locked,command=self.apply_image_flags).pack(side='left',padx=12)
        form=ttk.Frame(p); form.pack(fill='x',pady=8)
        self.image_vars=self.fields(form,[('x','原点 X',0,None),('y','原点 Y',0,None),('scale','图片自身缩放',1,None),
            ('rotation','旋转 (°)',0,None),('opacity','不透明度 0..1',.55,None)])
        self.button(self.inspector_scrolls['image'],'应用底图参数',self.apply_image,style='Accent.TButton').grid(row=1,column=0,columnspan=2,sticky='ew',padx=12,pady=8)
        self.button(p,'图片原点对齐坐标原点',lambda:self.align_reference(origin=True)).pack(fill='x',pady=4)
        self.button(p,'恢复原始尺寸',self.reset_image_size).pack(fill='x',pady=4)
        alignment=Disclosure(p,'高级：指定参考位置对齐');alignment.pack(fill='x',pady=8)
        form=ttk.Frame(alignment.body);form.pack(fill='x')
        self.image_vars.update(self.fields(form,[('reference_x','参考像素 X',0,None),('reference_y','参考像素 Y',0,None)]))
        self.button(alignment.body,'将参考像素对齐世界原点',self.align_reference).pack(fill='x',pady=8)
        row=ttk.Frame(p); row.pack(fill='x',pady=4)
        self.button(row,'替换图片',self.choose_image).pack(side='left')
        self.button(row,'移除底图',self.remove_image).pack(side='right')
        ttk.Label(p,text='拖动移动 · Alt+滚轮缩放底图\n锁定后仍可编辑上层控制点。',style='Muted.TLabel',wraplength=260).pack(anchor='w',pady=12)

    def _build_device_panel(self):
        p=self.device_panel
        ttk.Label(p,text='连接与设备信息',style='Section.TLabel').pack(anchor='w')
        ttk.Label(p,textvariable=self.device_text,style='Accent.TLabel').pack(anchor='w',pady=8)
        row=ttk.Frame(p); row.pack(fill='x',pady=10)
        for text,method in [('连接','connect'),('断开','disconnect'),('刷新','get_status')]:
            self.button(row,text,lambda method=method:self.request_api(method)).pack(side='left',padx=2)
        self.info_label=ttk.Label(p,text='尚未连接。可先编辑工程，连接后再应用。',style='Muted.TLabel',wraplength=440)
        self.info_label.pack(anchor='w',pady=4)
        self.device_advanced=Disclosure(p,'高级：辅助输入测试与通信参数');self.device_advanced.pack(fill='x',pady=18)
        p=self.device_advanced.body
        form=ttk.Frame(p); form.pack(fill='x',pady=10)
        self.device_vars=self.fields(form,[('dx','测试 dx',2,None),('dy','测试 dy',-1,None),
            ('mode','设备运行模式','1',['0','1']),('default_ttl_ms','RAM TTL / ms',40,None),('max_delta','RAM 最大位移',32767,None)])
        self.button(p,'发送一次 X/Y 测试',self.send_move,style='Accent.TButton').pack(fill='x',pady=4)
        self.button(p,'应用设备模式',lambda:self.request_api('set_mode',int(self.device_vars['mode'].get()))).pack(fill='x',pady=4)
        row=ttk.Frame(p); row.pack(fill='x',pady=5)
        self.button(row,'读取 RAM 配置',lambda:self.request_api('get_config')).pack(side='left')
        self.button(row,'应用 RAM 配置',self.apply_device_config).pack(side='right')
        ttk.Separator(p).pack(fill='x',pady=14)
        ttk.Label(p,text='断线后请重新连接；旧位移不会重放。\n通信确认不等于目标电脑收到鼠标报告。',style='Muted.TLabel',wraplength=420).pack(anchor='w')

    def _build_preview_panel(self):
        p=self.preview_panel
        ttk.Label(p,text='平滑扰动 · 预览与 MCU 共用设置',style='Accent.TLabel',wraplength=260).pack(anchor='w',pady=(0,12))
        form=ttk.Frame(p); form.pack(fill='x')
        self.preview_vars=self.fields(form,[('enabled','扰动开关','关闭',['关闭','开启']),
            ('amplitude_x','X 强度 / counts',3,None),('amplitude_y','Y 强度 / counts',3,None),
            ('frequency_hz','变化频率 / Hz',4,None),('smoothness','平滑程度','更平滑',['线性','平滑','更平滑']),
            ('max_deviation','最大偏差 / counts',8,None),('fixed_seed_enabled','运行种子','每次不同',['每次不同','固定种子']),
            ('seed','固定种子',0,None)])
        self.button(self.inspector_scrolls['preview'],'应用预览设置',self.apply_preview,style='Accent.TButton').grid(row=1,column=0,columnspan=2,sticky='ew',padx=12,pady=8)
        self.button(p,'换一组扰动预览',self.compile_now).pack(fill='x',pady=4)
        ttk.Label(p,text='连续低频偏差，不积累随机漂移。首尾偏差为 0。\n修改后需另行应用 RAM 才更新设备。',style='Muted.TLabel',wraplength=260).pack(anchor='w',pady=10)
        row=ttk.Frame(p);row.pack(fill='x',pady=8)
        ttk.Label(row,text='网格间距').pack(side='left')
        ttk.Entry(row,textvariable=self.grid_var,width=8).pack(side='right')
        self.button(p,'应用网格显示',self.view_option_changed).pack(fill='x',pady=4)

    def apply_preview(self):
        def action():
            from mouse_fusion.perturbation import normalize_perturbation
            frequency=float(self.preview_vars['frequency_hz'].get())
            if not .01526<=frequency<=25:raise ValueError('扰动频率须为 0.01526..25 Hz')
            settings=normalize_perturbation(dict(enabled=self.preview_vars['enabled'].get()=='开启',
                          amplitude_x=int(self.preview_vars['amplitude_x'].get()),amplitude_y=int(self.preview_vars['amplitude_y'].get()),
                          period_ms=round(1000/frequency),smoothness=['线性','平滑','更平滑'].index(self.preview_vars['smoothness'].get()),
                          max_deviation=int(self.preview_vars['max_deviation'].get()),fixed_seed_enabled=self.preview_vars['fixed_seed_enabled'].get()=='固定种子',
                          seed=int(self.preview_vars['seed'].get())))
            self.editor.apply(lambda:setattr(self.editor.mode,'perturbation',settings))
        self.commit_form(action,form='preview')

    def commit_form(self,action,geometry=True,form='point'):
        self._committing_form=form
        try:self.edit(action,geometry)
        finally:self._committing_form=False

    def inspector_pending(self):
        for name,variables in [('point',self.point_vars),('image',self.image_vars),('preview',self.preview_vars)]:
            baseline=getattr(self,'_loaded_'+name+'_form',None)
            if baseline and any(var.get()!=baseline.get(key) for key,var in variables.items()):return True
        return False

    def has_pending_forms(self):
        if self.inspector_pending():return True
        for attr in ('trigger_manager','run_panel','bypass_panel'):
            panel=getattr(self,attr,None)
            if panel is not None and panel.winfo_exists() and panel.has_pending_form():return True
        return False

    def require_applied_forms(self):
        if self.has_pending_forms():
            self.report_error(ValueError('参数尚未应用：请先点击“应用精确参数 / 应用底图参数 / 保存绑定 / 保存运行规则 / 保存旁路设置”，再保存或应用设备。'))
            return False
        return True

    def confirm_inspector_change(self):
        if not self.inspector_pending():return True
        if not messagebox.askokcancel('尚未应用的参数','切换选择会放弃尚未应用的参数。继续？',parent=self.root):return False
        self._loaded_point_form=self._loaded_image_form=self._loaded_preview_form=None
        self.refresh_all()
        return True

    def confirm_mode_change(self):
        for attr in ('trigger_manager','run_panel','bypass_panel'):
            panel=getattr(self,attr,None)
            if panel is not None and panel.winfo_exists() and panel.has_pending_form():
                self.require_applied_forms();return False
        return self.confirm_inspector_change()

    def selection_edit(self,action,geometry=True,*,mode=False):
        if not (self.confirm_mode_change() if mode else self.confirm_inspector_change()):return
        self.edit(action,geometry)

    def shortcut(self,event,callback):
        if isinstance(event.widget,(tk.Entry,ttk.Entry,ttk.Combobox,tk.Text)) and callback in (self.undo,self.redo,self.select_all,self.duplicate_points):return
        if event.keysym in ('Delete','space','f') and isinstance(event.widget,(tk.Entry,ttk.Entry,ttk.Combobox)):
            return
        if event.keysym=='a' and isinstance(event.widget,(tk.Entry,ttk.Entry)): return
        callback(); return 'break'

    def grid_spacing(self):
        try:
            value=float(self.grid_var.get())
            if not .01<=value<=100000: raise ValueError()
            return value
        except ValueError: return 10

    def view_option_changed(self):
        if hasattr(self,'canvas'):
            self.mark_view_changed(); self.sync_inspector();self.canvas.redraw()

    def mark_view_changed(self): self.dirty=True; self.update_title()

    def update_title(self):
        self.root.title(f'Mouse Fusion Studio — {self.path.name if self.path else "未命名工程"}{" *" if self.dirty else ""} — {self.backend_label} / 本地编辑')
        if hasattr(self,'project_caption'):self.project_caption.set(f'{self.path.name if self.path else "未命名工程"}'+(' · 有未保存修改' if self.dirty or self.has_pending_forms() else ''))

    def report_error(self,error):
        self.status.set(f'{getattr(error,"code","错误")}：{error}')
        self.status_label.configure(foreground='#ff9a9a')

    def edit(self,action,geometry=True):
        try:
            self.stop_playback(); action(); self.after_edit(geometry)
        except (ValueError,IndexError,TypeError) as error: self.report_error(error)

    def after_edit(self,geometry=True):
        self.dirty=True; self.update_title()
        self.status_label.configure(foreground=MUTED)
        self.status.set('已更新 · Ctrl+Z 撤销 · Ctrl+S 保存工程')
        if geometry:
            self.compiled_stale=True; self.schedule_compile()
        self.refresh_all()
        self.reset_keyboard_if_changed()

    def on_live_change(self,geometry=True):
        self.dirty=True
        if geometry: self.compiled_stale=True
        self.update_title()

    def refresh_all(self):
        self._refreshing=True
        self.mode_list.delete(0,'end')
        for mode in self.editor.project.modes: self.mode_list.insert('end',mode.name)
        self.mode_list.selection_set(self.editor.project.active_mode)
        self.settings_mode_box.configure(values=[m.name for m in self.editor.project.modes]);self.settings_mode_box.current(self.editor.project.active_mode)
        self.refresh_points(); self.refresh_image(); self.refresh_selection()
        previous=getattr(self,'_loaded_preview_form',None)
        pending=previous and any(var.get()!=previous.get(k) for k,var in self.preview_vars.items())
        if not pending or getattr(self,'_committing_form',False)=='preview':
            for k,v in self.preview_values().items(): self.preview_vars[k].set(str(v))
            self._loaded_preview_form={k:v.get() for k,v in self.preview_vars.items()}
        self._refreshing=False
        self.undo_button.configure(state='normal' if self.editor.can_undo else 'disabled')
        self.redo_button.configure(state='normal' if self.editor.can_redo else 'disabled')
        self.canvas.redraw(); self.update_title()
        if self.trigger_manager and self.trigger_manager.winfo_exists(): self.trigger_manager.sync_project()
        if hasattr(self,'run_panel') and self.run_panel.winfo_exists() and not self.run_panel.has_pending_form():
            if self.run_panel.window is None:self.run_panel.slot=self.editor.project.active_mode
            self.run_panel.sync_project()
        if hasattr(self,'bypass_panel') and self.bypass_panel.winfo_exists() and not self.bypass_panel.has_pending_form():self.bypass_panel.sync_project()

    def refresh_points(self):
        self.point_list.delete(0,'end')
        for i,p in enumerate(self.editor.mode.points): self.point_list.insert('end',f'{i+1:02}   {p.x:7.2f} {p.y:7.2f}')
        for i in self.editor.selection:
            if i<len(self.editor.mode.points): self.point_list.selection_set(i)

    def refresh_selection(self):
        selection=sorted(self.editor.selection)
        self.selection_label.configure(text=f'已选 {len(selection)} 个点' if selection else '选择控制点以编辑')
        self.point_list.selection_clear(0,'end')
        for i in selection: self.point_list.selection_set(i)
        pending=getattr(self,'_loaded_point_form',None) and any(v.get()!=self._loaded_point_form.get(k) for k,v in self.point_vars.items())
        if selection and (not pending or getattr(self,'_committing_form',False)=='point'):
            i=selection[0]; p=self.editor.mode.points[i]
            for key,value in dict(x=p.x,y=p.y,order=i+1,duration_ms=p.duration_ms,curve=p.curve,easing=p.easing).items():
                self.point_vars[key].set(f'{value:g}' if isinstance(value,(float,int)) else value)
            for field in ('handle_in','handle_out'):
                handle=getattr(p,field)
                self.point_vars[field+'_x'].set('' if handle is None else f'{handle[0]:g}')
                self.point_vars[field+'_y'].set('' if handle is None else f'{handle[1]:g}')
            self._loaded_handles={k:v.get() for k,v in self.point_vars.items() if k.startswith('handle')}
            self._loaded_point_form={k:v.get() for k,v in self.point_vars.items()}
            self.point_hint.configure(text='最后一个点没有下一段。' if i==len(self.editor.mode.points)-1 else '时间与曲线作用于此点到下一点。')
        elif not selection:self._loaded_point_form=None
        self.sync_inspector()
        if hasattr(self,'canvas'): self.canvas.redraw()

    def refresh_image(self):
        image=self.editor.mode.image
        self.image_name.configure(text=f'{image.name}\n{image.width} × {image.height} 像素 · 嵌入工程' if image else '尚未导入参考图片')
        pending=getattr(self,'_loaded_image_form',None) and any(v.get()!=self._loaded_image_form.get(k) for k,v in self.image_vars.items())
        if image and (not pending or getattr(self,'_committing_form',False)=='image'):
            for key,var in self.image_vars.items(): var.set(f'{getattr(image,key):g}')
            self.image_visible.set(image.visible); self.image_locked.set(image.locked)
            self._loaded_image_form={k:v.get() for k,v in self.image_vars.items()}
        elif not image:self._loaded_image_form=None

    def points_selected(self,event=None):
        if self._refreshing: return
        if not self.confirm_inspector_change():
            self.refresh_selection();return
        self.editor.selection=set(self.point_list.curselection()); self.refresh_selection()
        self.edit_mode.set('轨迹编辑')

    def mode_selected(self,event=None):
        if self._refreshing or not self.mode_list.curselection(): return
        i=self.mode_list.curselection()[0]
        if i==self.editor.project.active_mode: return
        if not self.confirm_mode_change():
            self.mode_list.selection_clear(0,'end');self.mode_list.selection_set(self.editor.project.active_mode);return
        self.editor.cancel(); self.editor.project.active_mode=i; self.editor.selection.clear()
        self.stop_playback(); self.compiled_stale=True; self.after_edit()

    def select_all(self):
        if not self.confirm_inspector_change():return
        self.editor.selection=set(range(len(self.editor.mode.points))); self.refresh_selection()
    def add_point(self): self.selection_edit(lambda:self.editor.add_point(self.editor.project.view.center_x,self.editor.project.view.center_y))
    def insert_point(self):
        if not self.editor.selection: return self.report_error(ValueError('请先选择插入位置'))
        self.selection_edit(lambda:self.editor.insert_after(min(self.editor.selection)))
    def duplicate_points(self): self.selection_edit(self.editor.duplicate_selected)
    def delete_points(self): self.selection_edit(self.editor.delete_selected)
    def reorder(self,delta):
        if len(self.editor.selection)!=1: return self.report_error(ValueError('请只选择一个点进行排列'))
        i=next(iter(self.editor.selection)); self.selection_edit(lambda:self.editor.edit_point(i,order=i+1+delta))

    def apply_point(self):
        if len(self.editor.selection)!=1: return self.report_error(ValueError('精确点参数须选择一个点；多点请使用点组变换'))
        def action():
            params={k:self.point_vars[k].get() for k in ('curve','easing')}
            params['duration_ms']=int(self.point_vars['duration_ms'].get())
            for field in ('handle_in','handle_out'):
                keys=(field+'_x',field+'_y'); values=[self.point_vars[k].get().strip() for k in keys]
                if any(self.point_vars[k].get()!=self._loaded_handles.get(k) for k in keys):
                    if not any(values): params[field]=None
                    elif all(values): params[field]=tuple(float(v) for v in values)
                    else: raise ValueError('手柄 X/Y 必须同时输入或同时留空')
            self.editor.edit_point(next(iter(self.editor.selection)),x=float(self.point_vars['x'].get()),
                y=float(self.point_vars['y'].get()),order=int(self.point_vars['order'].get()),**params)
        self.commit_form(action)

    def reset_handles(self):
        if len(self.editor.selection)==1:
            self.edit(lambda:self.editor.edit_point(next(iter(self.editor.selection)),handle_in=None,handle_out=None))

    def pivot(self):
        if self.transform_vars['pivot'].get()=='指定点':
            i=int(self.transform_vars['pivot_point'].get())-1
            if not 0<=i<len(self.editor.mode.points): raise ValueError('基准点序号超出范围')
            p=self.editor.mode.points[i]; return p.x,p.y
        return self.editor.selection_center()

    def apply_transform(self):
        def action():
            params={k:float(self.transform_vars[k].get()) for k in ('sx','sy','dx','dy')}
            if self.uniform.get(): params['sy']=params['sx']
            self.editor.transform_selection(pivot=self.pivot(),**params)
        self.edit(action)

    def mirror(self,sx,sy): self.edit(lambda:self.editor.transform_selection(sx=sx,sy=sy,pivot=self.pivot()))

    def undo(self):
        if not self.confirm_mode_change():return
        self.stop_playback(); self.editor.undo(); self.after_edit()
    def redo(self):
        if not self.confirm_mode_change():return
        self.stop_playback(); self.editor.redo(); self.after_edit()

    def add_mode(self):
        self.selection_edit(lambda:self.editor.apply(lambda:self._append_mode(Mode(f'轨迹 {len(self.editor.project.modes)+1:02}',[Point(0,0),Point(100,0)]))),mode=True)
    def _append_mode(self,mode):
        if any(m.mode_id==mode.mode_id for m in self.editor.project.modes):
            old_id=mode.mode_id; mode.mode_id=uuid.uuid4().hex
            for binding in mode.triggers:
                binding.trigger_id=uuid.uuid4().hex; binding.enabled=False
                if binding.target_mode==old_id: binding.target_mode=mode.mode_id
        self.editor.project.modes.append(mode); self.editor.project.active_mode=len(self.editor.project.modes)-1; self.editor.selection.clear()
    def copy_mode(self):
        mode=deepcopy(self.editor.mode); mode.name+=' 副本'
        self.selection_edit(lambda:self.editor.apply(lambda:self._append_mode(mode)),mode=True)
    def rename_mode(self):
        name=simpledialog.askstring('模式名称','输入模式名称',initialvalue=self.editor.mode.name,parent=self.root)
        if name is not None: self.edit(lambda:self.editor.apply(lambda:setattr(self.editor.mode,'name',name)))
    def delete_mode(self):
        if len(self.editor.project.modes)==1: return self.report_error(ValueError('工程至少保留一个模式'))
        def change():
            self.editor.project.modes.pop(self.editor.project.active_mode); self.editor.project.active_mode=0; self.editor.selection.clear()
        self.selection_edit(lambda:self.editor.apply(change),mode=True)

    def choose_image(self):
        path=filedialog.askopenfilename(parent=self.root,title='导入参考底图',filetypes=[('参考图片','*.png *.jpg *.jpeg')])
        if path:
            try: self.load_reference(path)
            except (OSError,ValueError) as error: self.report_error(error)

    def load_reference(self,path):
        if not self.confirm_inspector_change():return
        image=import_image(path)
        self.editor.apply(lambda:setattr(self.editor.mode,'image',image)); self.after_edit(False)
        self.edit_mode.set('图片编辑');self.sync_inspector()

    def image_editable(self):
        if not self.editor.mode.image: raise ValueError('请先导入底图')
        if self.editor.mode.image.locked: raise ValueError('底图已锁定，请先解锁')

    def apply_image(self):
        def action():
            self.image_editable(); self.editor.set_image_pose(**{k:float(v.get()) for k,v in self.image_vars.items()})
        self.commit_form(action,False,form='image')
    def apply_image_flags(self):
        if self.editor.mode.image: self.edit(lambda:self.editor.set_image_pose(visible=self.image_visible.get(),locked=self.image_locked.get()),False)
    def align_reference(self,origin=False):
        def action():
            self.image_editable()
            px,py=(0,0) if origin else (float(self.image_vars['reference_x'].get()),float(self.image_vars['reference_y'].get()))
            self.editor.align_image(px,py)
        self.edit(action,False)
    def reset_image_size(self):
        def action(): self.image_editable(); self.editor.set_image_pose(scale=1)
        self.edit(action,False)
    def remove_image(self): self.edit(lambda:self.editor.apply(lambda:setattr(self.editor.mode,'image',None)),False)

    def origin_view(self):
        self.editor.project.view.center_x=self.editor.project.view.center_y=0
        self.mark_view_changed(); self.canvas.redraw()
    def fit_view(self):
        points=[(p.x,p.y) for p in self.editor.mode.points]
        image=self.editor.mode.image
        if image and image.visible:
            points += [image.pixel_to_world(x,y) for x,y in ((0,0),(image.width,0),(image.width,image.height),(0,image.height))]
        if not points: return
        xmin,xmax=min(p[0] for p in points),max(p[0] for p in points)
        ymin,ymax=min(p[1] for p in points),max(p[1] for p in points)
        view=self.editor.project.view; view.center_x=(xmin+xmax)/2; view.center_y=(ymin+ymax)/2
        view.zoom=max(.03,min(30,(self.canvas.winfo_width()-100)/max(20,xmax-xmin),
                                   (self.canvas.winfo_height()-100)/max(20,ymax-ymin)))
        self.mark_view_changed(); self.canvas.redraw()

    def cancel_compile(self):
        if self._compile_id: self.after_cancel(self._compile_id); self._compile_id=None
    def schedule_compile(self):
        self.cancel_compile(); self._compile_id=self.after(180,self.compile_now)
    def compile_now(self):
        self.cancel_compile()
        try:
            self.compiled=compile_mode(self.editor.mode); self.compiled_stale=False; self.compile_count+=1
            data=export_data(self.compiled,self.editor.mode)
            self.compile_text.set(f'{len(self.compiled.samples)-1:,} × 1ms  ·  {self.compiled.duration_ms}ms  ·  估算 {data["estimated_bytes"]:,}B')
            self.compile_details.set(self.compile_text.get()+'（本地采样估算，非设备 Flash 占用）')
            self.compile_text.set(f'{self.compiled.duration_ms/1000:g} 秒 · {len(self.editor.mode.points)} 个点')
            self.timeline.configure(to=max(1,self.compiled.duration_ms))
            self.playhead.set(min(self.playhead.get(),self.compiled.duration_ms))
            self.seek(); self.draw_speed(); self.canvas.redraw()
        except ValueError as error:
            self.compiled=None; self.compiled_stale=True
            self.compile_text.set('空白轨迹 · 可直接编辑' if not self.editor.mode.points else '编译未通过')
            if self.editor.mode.points:self.report_error(error)
            else:self.compile_details.set('尚无轨迹数据；在画布添加至少两个控制点后可预览和执行。')
            self.draw_speed()

    def draw_speed(self):
        c=self.speed_canvas; c.delete('all'); w,h=max(1,c.winfo_width()),max(1,c.winfo_height())
        c.create_text(10,12,text='速度 / 世界单位·s⁻¹',anchor='w',fill=MUTED,font=('Microsoft YaHei UI',9))
        if not self.compiled: return
        samples=self.compiled.samples; maximum=max(1,max(s.speed for s in samples)); total=self.compiled.duration_ms
        stride=max(1,len(samples)//max(1,w//2)); start=155
        path=[(start+s.t_ms/total*max(1,w-start-12),h-8-s.speed/maximum*(h-18)) for s in samples[::stride]]
        if len(path)>1: c.create_line(*[v for p in path for v in p],fill=CYAN,width=1.5)
        c.create_text(10,38,text=f'峰值 {maximum:.1f}',anchor='w',fill=CYAN,font=('Consolas',9))
        x=start+self.playhead.get()/total*max(1,w-start-12)
        c.create_line(x,8,x,h-6,fill=ORANGE)

    def seek(self,value=None):
        total=self.compiled.duration_ms if self.compiled else 0
        self.time_text.set(f'{self.playhead.get():.0f} / {total} ms')
        if hasattr(self,'canvas'): self.canvas.request_redraw()
        if hasattr(self,'speed_canvas'): self.draw_speed()
    def toggle_playback(self):
        if self.playing: return self.stop_playback()
        if self.compiled_stale: self.compile_now()
        if not self.compiled: return
        if self.playhead.get()>=self.compiled.duration_ms: self.playhead.set(0)
        self.playing=True; self.play_button.configure(text='Ⅱ 暂停')
        self.active_trajectory_id=self.editor.mode.mode_id
        self._play_start=time.perf_counter()-self.playhead.get()/1000; self.tick_playback()
    def tick_playback(self):
        if not self.playing: return
        self.playhead.set(min(self.compiled.duration_ms,(time.perf_counter()-self._play_start)*1000)); self.seek()
        if self.playhead.get()>=self.compiled.duration_ms: self.stop_playback()
        else: self._play_id=self.after(16,self.tick_playback)
    def stop_playback(self):
        self.playing=False
        self.active_trajectory_id=None
        if self._play_id: self.after_cancel(self._play_id); self._play_id=None
        if hasattr(self,'play_button'): self.play_button.configure(text='▶ 本地播放')
    def reset_playback(self): self.stop_playback(); self.playhead.set(0); self.seek()

    def capture_editor_state(self):
        self.editor.project.editor_state.update(dict(edit_mode=self.edit_mode.get(),grid_spacing=self.grid_spacing(),
             display={k:v.get() for k,v in self.flags.items()},selection=sorted(self.editor.selection),
             uniform_scale=self.uniform.get(),transform={k:v.get() for k,v in self.transform_vars.items()},
             playhead_ms=self.playhead.get()))
        self.editor.project.editor_state['keyboard_enabled']=self.keyboard_enabled.get()
    def restore_editor_state(self):
        state=self.editor.project.editor_state
        self.edit_mode.set(state.get('edit_mode','轨迹编辑') if state.get('edit_mode') in ('轨迹编辑','图片编辑') else '轨迹编辑')
        self.grid_var.set(str(state.get('grid_spacing',10)))
        for k,v in state.get('display',{}).items():
            if k in self.flags and type(v) is bool: self.flags[k].set(v)
        self.editor.selection={i for i in state.get('selection',[]) if type(i) is int and 0<=i<len(self.editor.mode.points)}
        self.uniform.set(state.get('uniform_scale',False))
        self.keyboard_enabled.set(state.get('keyboard_enabled',False))
        for k,v in state.get('transform',{}).items():
            if k in self.transform_vars: self.transform_vars[k].set(str(v))
        try: self.playhead.set(max(0,float(state.get('playhead_ms',0))))
        except (ValueError,TypeError): self.playhead.set(0)

    def save_to(self,path):
        if not self.require_applied_forms():return False
        self.capture_editor_state(); save_project(self.editor.project,path); self.path=Path(path); self.dirty=False
        self.update_title(); self.status.set(f'工程已保存到：{self.path.resolve()}')
        return True
    def open_from(self,path):
        project=load_project(path)
        if self.trigger_manager and self.trigger_manager.winfo_exists(): self.trigger_manager.destroy()
        for attr in ('run_panel','bypass_panel'):
            panel=getattr(self,attr,None)
            if panel is not None and panel.winfo_exists():panel.destroy()
        self.stop_playback(); self.cancel_compile(); self.editor=Editor(project); self.path=Path(path)
        self._loaded_point_form=self._loaded_image_form=self._loaded_preview_form=None
        self.restore_editor_state(); self.compiled=None; self.compiled_stale=True
        self.refresh_all(); self.compile_now(); self.dirty=False; self.update_title()
        self.status.set(f'已打开：{self.path.name}')
        if self.workspaces.select()==str(self.modes_workspace):self.mode_tool_changed()
    def choose_save(self,save_as=False):
        if not self.require_applied_forms():
            messagebox.showwarning('暂时无法保存',self.status.get(),parent=self.root);return False
        starter=Path(__file__).resolve().parents[1]/'examples/studio-v2/studio-v2.mfusion'
        from_starter=self.path is not None and self.path.resolve()==starter
        documents=Path.home()/'Documents'
        path=str(self.path) if self.path and not save_as and not from_starter else filedialog.asksaveasfilename(parent=self.root,title='保存可编辑工程',
             initialfile='我的轨迹.mfusion' if from_starter else self.path.name if self.path else '未命名工程.mfusion',
             initialdir=str(documents if documents.is_dir() else Path.home()) if from_starter else str(self.path.parent) if self.path else str(Path.cwd()),
             defaultextension='.mfusion',filetypes=[('Studio 工程','*.mfusion')])
        if path:
            try: return self.save_to(path)
            except (OSError,ValueError) as error:
                self.report_error(error);messagebox.showerror('保存失败',str(error),parent=self.root)
        return False
    def confirm_unsaved(self):
        if self._flash_saving:
            messagebox.showwarning('请等待保存结束','设备正在保存Flash并重启，请等结果确认后再关闭。',parent=self.root);return False
        pending=self.has_pending_forms()
        if not self.dirty and not pending: return True
        answer=messagebox.askyesnocancel('未保存的工程','参数尚未应用，请取消后先应用参数再保存。\n选择“否”放弃未保存修改并继续。' if pending else '保存当前修改后继续？',parent=self.root)
        if answer is None: return False
        return self.choose_save() if answer else True
    def choose_open(self):
        path=filedialog.askopenfilename(parent=self.root,title='打开 Studio 工程',filetypes=[('Studio 工程','*.mfusion'),('JSON','*.json')])
        if path and self.confirm_unsaved():
            try: self.open_from(path)
            except (OSError,ValueError) as error: self.report_error(error)
    def new_project(self):
        if self.confirm_unsaved():
            if self.trigger_manager and self.trigger_manager.winfo_exists(): self.trigger_manager.destroy()
            for attr in ('run_panel','bypass_panel'):
                panel=getattr(self,attr,None)
                if panel is not None and panel.winfo_exists():panel.destroy()
            self._loaded_point_form=self._loaded_image_form=self._loaded_preview_form=None
            self.stop_playback(); self.editor=Editor(); self.path=None; self.compiled=None;self.edit_mode.set('轨迹编辑');self.after_edit()
            if self.workspaces.select()==str(self.modes_workspace):self.mode_tool_changed()
    def import_modes(self):
        if not self.confirm_mode_change():return
        path=filedialog.askopenfilename(parent=self.root,title='导入工程中的模式',filetypes=[('Studio 工程','*.mfusion')])
        if path:
            def action():
                imported=load_project(path)
                self.editor.merge_project(imported)
            try:
                self.edit(action)
                self.status.set('模式已导入；冲突的导入触发器默认禁用，请在触发器管理中检查')
            except OSError as error: self.report_error(error)
    def export_project(self):
        if not self.require_applied_forms():return
        path=filedialog.asksaveasfilename(parent=self.root,title='导出可编辑工程副本',defaultextension='.mfusion',filetypes=[('Studio 工程','*.mfusion')])
        if path:
            try: self.capture_editor_state(); save_project(self.editor.project,path); self.status.set('已导出工程副本（含嵌入底图）')
            except (OSError,ValueError) as error: self.report_error(error)
    def export_compiled(self):
        if not self.require_applied_forms():return
        if self.compiled_stale: self.compile_now()
        if not self.compiled: return
        path=filedialog.asksaveasfilename(parent=self.root,title='导出编译轨迹（不写设备）',defaultextension='.mfcompiled.json',filetypes=[('编译结果','*.mfcompiled.json')])
        if path:
            try: atomic_json(path,export_data(self.compiled,self.editor.mode)); self.status.set('编译 JSON 已导出 · MCU 容量请使用“MCU 离线执行”中的 MFT1 导出')
            except (OSError,ValueError) as error: self.report_error(error)

    def request_api(self,method,*args,**kwargs):
        try:
            if self.live_device and method in ('simulate_mouse_buttons','save_simulated_presets','restore_simulated_presets'):
                raise ValueError('实机不支持虚拟鼠标注入或模拟NOR；请使用独立的设备Flash保存操作')
            if self.device_capabilities is not None and method in ('get_bypass_status','set_bypass_config') and not self.device_capabilities&0x1000:
                raise ValueError('当前固件不支持 BYPASS；需更新 0.4.2-bypass 固件')
            if self.connection is None: self.connection=StudioConnection(endpoint=self.endpoint,c_simulator=self.c_simulator,preset_c_simulator=self.preset_c_simulator,
                allow_live_device=self.allow_live_device,live_com=self.live_com,board_id=self.board_id,simulate_device_flash=self.simulate_device_flash)
            self.connection.submit(method,*args,**kwargs)
            self.status.set(f'{self.backend_label}：{method} …')
        except (ValueError,OSError) as error: self.report_error(error)
    def send_move(self):
        try: self.request_api('move',int(self.device_vars['dx'].get()),int(self.device_vars['dy'].get()))
        except ValueError as error: self.report_error(error)
    def apply_device_config(self):
        try: self.request_api('set_config',{k:int(self.device_vars[k].get()) for k in ('default_ttl_ms','max_delta')})
        except ValueError as error: self.report_error(error)
    def poll_connection(self):
        if self._disposed: return
        self.tick_keyboard()
        if self.connection:
            while True:
                item=self.connection.poll()
                if not item: break
                method,result,error=item
                if hasattr(self,'run_panel') and self.run_panel.winfo_exists():self.run_panel.receive(method,result,error)
                if hasattr(self,'bypass_panel') and self.bypass_panel.winfo_exists():self.bypass_panel.receive(method,result,error)
                if method=='get_status': self._status_pending=False
                if method=='save_project_flash':
                    self.finish_flash(result,error);continue
                if error:
                    if method in ('apply_project_ram','connect','restore_simulated_presets'):self._preset_applied_signature=None
                    self.report_error(error)
                    if getattr(error,'code','') in ('NOT_CONNECTED','TIMEOUT','DEVICE_OFFLINE','DEVICE_DISCONNECTED'):
                        self.connected=False; self.device_capabilities=None;self.device_text.set(f'{self.backend_label} · 断线，请显式连接')
                    continue
                self.status_label.configure(foreground=MUTED)
                if method=='connect':
                    self._preset_applied_signature=None
                    self._preset_applied_revision=None;self._device_persistence_status=result.get('device_persistence',{})
                    self.live_device=result.get('source')=='LIVE_COM'
                    self.backend_label='实机 J4' if self.live_device else '模拟后端'
                    self.device_capabilities=result.get('capabilities',0)
                    self.connected=True; self.device_text.set(f'{self.backend_label} · 已连接 · '+('Flash 可保存' if self.device_capabilities&0x20000 else 'RAM 配置'))
                    self.info_label.configure(text=f'固件：{result.get("firmware_version",result.get("firmware","UNKNOWN"))}\n'
                        f'协议：{result.get("protocol","UNKNOWN")}\n设备：{result.get("board_id","UNKNOWN")}\n'
                        f'后端：{result.get("source","UNKNOWN")}\nBYPASS：{"支持" if self.device_capabilities&0x1000 else "不支持"}\nFlash 保存：{"支持永久保存" if self.device_capabilities&0x20000 else "未开放"}')
                    self.request_api('get_config'); self.request_api('get_status')
                elif method=='disconnect': self.connected=False;self.device_capabilities=None; self.device_text.set(f'{self.backend_label} · 已断开')
                elif method=='get_config':
                    for k in ('default_ttl_ms','max_delta'):
                        if k in result: self.device_vars[k].set(str(result[k]))
                elif method=='get_status':
                    aux=result.get('auxiliary',result.get('aux',{}))
                    mode=aux.get('mode_id',result.get('mode_id'))
                    if mode is not None: self.device_vars['mode'].set(str(mode))
                elif method=='apply_project_ram':
                    self._preset_applied_signature=result['project_signature'];self._preset_applied_crcs=result['applied_crcs']
                    self._preset_applied_revision=result.get('ram_revision')
                elif method=='restore_simulated_presets':self._preset_applied_signature=None
                if hasattr(self,'preset_result') and method in ('get_preset_capabilities','get_preset_status','apply_project_ram',
                         'save_simulated_presets','restore_simulated_presets','start_trajectory','stop_trajectory'):
                    import json
                    self.preset_result.set(json.dumps(result,ensure_ascii=False,indent=2))
                if method=='move': self.status.set(f'{self.backend_label} MOVE ACK：{result.get("disposition","accepted")} · delivery_confirmed=false')
                elif method!='get_status': self.status.set(f'{self.backend_label} {method} 完成')
            now=time.perf_counter()
            if self.connected and not self._status_pending and now>self._next_status:
                try:
                    self.connection.submit('get_status'); self._status_pending=True; self._next_status=now+.75
                except ValueError as error: self.report_error(error)
        if hasattr(self,'run_panel') and self.run_panel.winfo_exists():self.run_panel.tick()
        if hasattr(self,'bypass_panel') and self.bypass_panel.winfo_exists():self.bypass_panel.tick()
        self.sync_flash_ui();self._poll_id=self.after(50,self.poll_connection)

    def show_shortcuts(self):
        messagebox.showinfo('Studio 快捷键','Ctrl+S 保存  ·  Ctrl+Shift+S 另存为\nCtrl+O 打开  ·  Ctrl+Z 撤销  ·  Ctrl+Y 重做\nCtrl+A 全选点  ·  Ctrl+D 复制  ·  Delete 删除\nF 适应画布  ·  Space 本地播放/暂停\n双击空白添加点  ·  Shift 点击增选\n空白拖动框选  ·  右键/中键拖动画布\n滚轮缩放视图  ·  图片模式 Alt+滚轮缩放底图',parent=self.root)

    def show_presets(self):
        self.show_mode_tool('run')

    def show_bypass(self):
        self.show_mode_tool('bypass')

    def export_executable(self):
        if not self.require_applied_forms():return
        from .device_data import executable_mode
        try:
            image,info=executable_mode(self.editor.mode)
            path=filedialog.asksaveasfilename(parent=self.root,title='导出 MFT2 关键点轨迹',defaultextension='.mft2',filetypes=[('MFT2','*.mft2')])
            if path:
                Path(path).write_bytes(image)
                self.status.set(f'MFT2 已导出：{len(image)} 字节 / {info["duration_ms"]}ms（图片不包含在内）')
        except (ValueError,OSError) as error:self.report_error(error)

    def apply_project_ram(self):
        if not self.require_applied_forms():return
        if hasattr(self,'run_panel') and self.run_panel.winfo_exists() and not self.run_panel.require_saved_rule():return
        if hasattr(self,'bypass_panel') and self.bypass_panel.winfo_exists() and not self.bypass_panel.require_saved_config():return
        if not self.require_applied_forms():return
        from .device_data import prepare_project,execution_signature,project_run_policies
        self._preset_applied_signature=None
        try:self.request_api('apply_project_ram',*prepare_project(self.editor.project),project_signature=execution_signature(self.editor.project),
                             run_policies=project_run_policies(self.editor.project),bypass=self.editor.project.bypass)
        except ValueError as error:self.report_error(error)

    def start_current_preset(self):
        if hasattr(self,'run_panel') and self.run_panel.winfo_exists() and not self.run_panel.require_saved_rule():return
        if hasattr(self,'bypass_panel') and self.bypass_panel.winfo_exists() and not self.bypass_panel.require_saved_config():return
        from .device_data import execution_signature
        if getattr(self,'_preset_applied_signature',None)!=execution_signature(self.editor.project):
            self.report_error(ValueError('当前工程尚未确认应用到 RAM，或轨迹/顺序/触发配置已改变；请先重新应用 RAM'));return
        slot=self.editor.project.modes.index(self.editor.mode)
        if slot not in self._preset_applied_crcs:self.report_error(ValueError('该模式尚未应用到设备 RAM'));return
        self.request_api('start_trajectory',slot,expected_crc32=int(self._preset_applied_crcs[slot],16))

    def show_triggers(self):
        self.show_mode_tool('triggers')

    def capture_screenshot(self,path=None,window=None):
        """Capture this running Tk window. No input injection or screen activation."""
        from PIL import ImageGrab
        import ctypes
        from ctypes import wintypes
        window=window or self.root
        path=Path(path or Path.cwd()/'output'/'studio_20261012_01'/('studio-triggers.png' if window is not self.root else 'studio-canvas.png'))
        try:
            window.update_idletasks()
            user32=ctypes.WinDLL('user32',use_last_error=True)
            user32.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT]; user32.GetAncestor.restype=wintypes.HWND
            hwnd=user32.GetAncestor(window.winfo_id(),2)
            capture=ImageGrab.grab(window=hwnd)
            path.parent.mkdir(parents=True,exist_ok=True); capture.save(path)
            self.status.set(f'实际窗口截图已保存：{path.name}')
            return path
        except (OSError,ValueError) as error: self.report_error(error); return None

    def dispatch_trigger(self,event):
        """Only local mode/preview actions. Never calls device APIs."""
        if event.action=='bypass':
            if event.phase=='activate':self._mode_control_enabled=False;self.stop_playback();self.status.set('本地 BYPASS 预览 · 辅助播放已停止')
            return
        if event.action=='toggle_mode':
            if event.phase!='activate' or not self.require_applied_forms():return
            index=next((i for i,m in enumerate(self.editor.project.modes) if m.mode_id==event.target_mode),None)
            if index is None:return
            enabled=not self._mode_control_enabled or index!=self.editor.project.active_mode
            self.stop_playback();self.editor.project.active_mode=index;self.editor.selection.clear()
            self._mode_control_enabled=enabled;self.after_edit()
            self.status.set(f'本地模式预览：{self.editor.mode.name} · '+('已开启，等待执行键' if enabled else '已关闭，轨迹不叠加'))
            return
        if event.action=='start_current_trajectory':
            if event.phase=='release':
                if getattr(self,'_current_hold_trigger',None)==event.trigger_id:self.stop_playback()
                return
            if not self._mode_control_enabled:return
            from dataclasses import replace
            self._current_hold_trigger=event.trigger_id
            event=replace(event,action='start_trajectory',target_mode=self.editor.mode.mode_id)
        if event.phase=='release':
            if event.behavior=='hold' and self.active_trajectory_id==event.target_mode: self.stop_playback()
            return
        modes=self.editor.project.modes
        if event.action in ('next_mode','previous_mode'):
            index=(self.editor.project.active_mode+(1 if event.action=='next_mode' else -1))%len(modes)
        else:
            index=next((i for i,m in enumerate(modes) if m.mode_id==event.target_mode),None)
            if index is None: return self.report_error(ValueError('触发器目标模式不存在'))
        if event.action=='stop_trajectory':
            if self.active_trajectory_id==event.target_mode: self.stop_playback()
            return
        if event.action=='start_trajectory' and event.behavior=='toggle' and self.playing and self.active_trajectory_id==event.target_mode:
            self.stop_playback(); return
        if not self.require_applied_forms():return
        self.stop_playback(); self.editor.project.active_mode=index; self.editor.selection.clear(); self.after_edit()
        if event.action=='start_trajectory':
            self.compile_now(); self.playhead.set(0); self.toggle_playback()
        self.status.set(f'本地模拟触发：{event.action} → {modes[index].name}（未发送设备）')

    def reset_keyboard_if_changed(self):
        bindings=[b for m in self.editor.project.modes for b in m.triggers if b.device=='keyboard']
        signature=repr(bindings)
        if signature!=self._keyboard_signature:
            machine=self._keyboard_machine
            self._keyboard_machine=TriggerMachine(bindings); self._keyboard_signature=signature
            if machine:
                for event in machine.reset(): self.dispatch_trigger(event)

    def keyboard_setting_changed(self):
        if not self.keyboard_enabled.get() and self._keyboard_machine:
            for event in self._keyboard_machine.reset(): self.dispatch_trigger(event)
        self.reset_keyboard_if_changed(); self.mark_view_changed()

    def _keyboard_event(self,event,down):
        if not self.keyboard_enabled.get() or isinstance(event.widget,(tk.Entry,ttk.Entry,ttk.Combobox,tk.Text)): return
        try:
            key=normalize_keys([event.keysym])[0]; self.reset_keyboard_if_changed()
            for result in self._keyboard_machine.feed(key,down,time.perf_counter()*1000,device='keyboard'): self.dispatch_trigger(result)
        except ValueError: pass
    def keyboard_press(self,event): self._keyboard_event(event,True)
    def keyboard_release(self,event): self._keyboard_event(event,False)
    def keyboard_focus_out(self,event):
        if self._keyboard_machine:
            for result in self._keyboard_machine.reset(): self.dispatch_trigger(result)
    def tick_keyboard(self):
        if self.keyboard_enabled.get():
            self.reset_keyboard_if_changed()
            for event in self._keyboard_machine.advance(time.perf_counter()*1000): self.dispatch_trigger(event)

    def dispose(self):
        self._disposed=True; self.stop_playback(); self.cancel_compile(); self.canvas.dispose()
        if self._poll_id: self.after_cancel(self._poll_id); self._poll_id=None
        for seq,fid in self._bindings: self.root.unbind(seq,fid)
        self._bindings=[]
        if self.trigger_manager and self.trigger_manager.winfo_exists(): self.trigger_manager.destroy()
        if hasattr(self,'run_panel') and self.run_panel.winfo_exists():self.run_panel.destroy()
        if hasattr(self,'bypass_panel') and self.bypass_panel.winfo_exists():self.bypass_panel.destroy()
        if self.connection: self.connection.close()


class StudioApp(StudioV2Layout,LegacyStudioApp):
    """Keep the established editor intact while V2 owns navigation and drafts."""
    pass
