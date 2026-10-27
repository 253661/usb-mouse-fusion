"""Task-based Studio layout; existing editor and device commands are reused."""
import tkinter as tk
from tkinter import ttk
from .ui_components import Disclosure,ScrollFrame,Tooltip
from .canvas import TrajectoryCanvas,BG
from .flash_ui import FlashPersistenceUI


class WorkspaceLayout(FlashPersistenceUI):
    def build_workspaces(self):
        header=ttk.Frame(self,padding=(14,8));header.pack(fill='x')
        ttk.Label(header,text='Mouse Fusion Studio V2',style='Title.TLabel').pack(side='left')
        self.project_caption=tk.StringVar(value='未命名工程');self.compile_details=tk.StringVar(value='尚未编译')
        ttk.Label(header,textvariable=self.project_caption,style='Muted.TLabel').pack(side='left',padx=16)
        ttk.Label(header,textvariable=self.device_text,style='Accent.TLabel').pack(side='right')
        tools=ttk.Frame(self,padding=(12,0,12,8));tools.pack(fill='x')
        file_menu=tk.Menu(tools,tearoff=False)
        for label,command in [('新建工程',self.new_project),('打开工程…',self.choose_open),('保存工程',self.choose_save),
                ('另存为…',lambda:self.choose_save(True)),('导入模式…',self.import_modes),('导出工程副本…',self.export_project),
                ('导出编译数据…',self.export_compiled),('导出设备关键点 MFT2…',self.export_executable)]:
            file_menu.add_command(label=label,command=command)
        self.file_menu=file_menu
        ttk.Menubutton(tools,text='文件 ▾',menu=file_menu).pack(side='left',padx=(0,8))
        self.button(tools,'打开',self.choose_open,tip='Ctrl+O · 打开已有 .mfusion 工程').pack(side='left',padx=2)
        self.save_button=self.button(tools,'保存工程',self.choose_save,style='Accent.TButton',tip='Ctrl+S · 保存到电脑，不写入设备')
        self.save_button.pack(side='left',padx=2)
        self.undo_button=self.button(tools,'撤销',self.undo,tip='Ctrl+Z · 撤销上一次工程编辑');self.undo_button.pack(side='left',padx=(14,2))
        self.redo_button=self.button(tools,'重做',self.redo,tip='Ctrl+Y · 恢复撤销的工程编辑');self.redo_button.pack(side='left',padx=2)
        self.button(tools,'帮助与快捷键',self.show_shortcuts).pack(side='right')
        self.workspaces=ttk.Notebook(self);self.workspaces.pack(fill='both',expand=True,padx=12,pady=(0,8))
        self.edit_workspace=ttk.Frame(self.workspaces)
        self.modes_workspace=ttk.Frame(self.workspaces)
        self.device_workspace=ttk.Frame(self.workspaces)
        for panel,title in [(self.edit_workspace,'轨迹编辑'),(self.modes_workspace,'模式与按键'),(self.device_workspace,'设备与保存')]:
            self.workspaces.add(panel,text=title)
        self._build_editor_workspace();self._build_modes_workspace();self._build_device_workspace()
        footer=ttk.Frame(self,padding=(14,4));footer.pack(side='bottom',fill='x',before=self.workspaces)
        self.status_label=ttk.Label(footer,textvariable=self.status,style='Muted.TLabel',wraplength=730)
        self.status_label.pack(side='left',fill='x',expand=True)
        ttk.Label(footer,textvariable=self.compile_text,style='Accent.TLabel').pack(side='right',padx=(10,0))
        self.workspaces.bind('<<NotebookTabChanged>>',self.workspace_changed)
        self.edit_mode.trace_add('write',lambda *_:self.view_option_changed())
        self.inspector_context='empty'

    def _build_editor_workspace(self):
        bar=ttk.Frame(self.edit_workspace,padding=(4,9));bar.pack(fill='x')
        for label in ('轨迹编辑','图片编辑'):
            ttk.Radiobutton(bar,text=label,value=label,variable=self.edit_mode).pack(side='left',padx=(0,12))
        self.button(bar,'导入底图',self.choose_image,tip='PNG / JPG · 导入后自动显示底图参数').pack(side='left',padx=4)
        self.button(bar,'适应画布',self.fit_view,tip='F · 将轨迹与底图完整放入视图').pack(side='left',padx=4)
        self.button(bar,'原点',self.origin_view).pack(side='left',padx=4)
        ttk.Checkbutton(bar,text='吸附网格',variable=self.flags['snap'],command=self.view_option_changed).pack(side='left',padx=8)
        menu=tk.Menu(bar,tearoff=False)
        for key,label in [('grid','坐标网格'),('points','控制点编号'),('curves','轨迹曲线'),('samples','编译采样点'),('perturbed','扰动预览')]:
            menu.add_checkbutton(label=label,variable=self.flags[key],command=self.view_option_changed)
        ttk.Menubutton(bar,text='显示选项 ▾',menu=menu).pack(side='right')
        self.editor_panes=ttk.Panedwindow(self.edit_workspace,orient='horizontal')
        # Playback is allocated before the expanding editor, even at small heights.
        bottom=ttk.Frame(self.edit_workspace,padding=(4,5));bottom.pack(side='bottom',fill='x')
        line=ttk.Frame(bottom);line.pack(fill='x')
        self.play_button=self.button(line,'▶ 本地播放',self.toggle_playback,tip='Space · 仅在画布预览，不启动设备运动')
        self.play_button.pack(side='left')
        self.button(line,'■ 停止预览',self.reset_playback).pack(side='left',padx=5)
        ttk.Label(line,textvariable=self.time_text,style='Muted.TLabel',width=17).pack(side='left',padx=6)
        self.timeline=ttk.Scale(line,from_=0,to=1,variable=self.playhead,command=self.seek)
        self.timeline.pack(side='left',fill='x',expand=True,padx=8)
        self.button(line,'更新预览',self.compile_now,tip='重新计算曲线、1ms采样与速度图').pack(side='right')
        self.speed_canvas=tk.Canvas(bottom,height=54,bg=BG,highlightthickness=0)
        self.speed_canvas.pack(fill='x',pady=(6,0));self.speed_canvas.bind('<Configure>',lambda e:self.draw_speed())
        self.editor_panes.pack(fill='both',expand=True)
        left=ttk.Frame(self.editor_panes,width=218,padding=(4,8,8,4))
        left.columnconfigure(0,weight=1);left.rowconfigure(4,weight=1)
        self.editor_panes.add(left,weight=0)
        ttk.Label(left,text='模式 / 轨迹',style='Section.TLabel').grid(row=0,column=0,sticky='w',pady=(0,8))
        self.mode_list=tk.Listbox(left,height=2,width=16,background='#101c2a',foreground='#e4edf6',selectbackground='#375a76',
                 font=('Microsoft YaHei UI',11),borderwidth=0,highlightthickness=0,exportselection=False)
        self.mode_list.grid(row=1,column=0,sticky='ew');self.mode_list.bind('<<ListboxSelect>>',self.mode_selected)
        row=ttk.Frame(left);row.grid(row=2,column=0,sticky='ew',pady=4)
        mode_menu=tk.Menu(row,tearoff=False)
        for label,cmd in [('复制模式',self.copy_mode),('重命名',self.rename_mode),('删除模式',self.delete_mode)]:mode_menu.add_command(label=label,command=cmd)
        self.button(row,'＋ 模式',self.add_mode).pack(side='left')
        ttk.Menubutton(row,text='管理 ▾',menu=mode_menu).pack(side='right')
        ttk.Label(left,text='控制点 · 执行顺序',style='Section.TLabel').grid(row=3,column=0,sticky='w',pady=(6,4))
        point_holder=ttk.Frame(left);point_holder.grid(row=4,column=0,sticky='nsew')
        self.point_list=tk.Listbox(point_holder,selectmode='extended',background='#101c2a',foreground='#e4edf6',
                selectbackground='#375a76',font=('Consolas',11),height=4,borderwidth=0,highlightthickness=0,exportselection=False)
        point_scroll=ttk.Scrollbar(point_holder,command=self.point_list.yview)
        self.point_list.configure(yscrollcommand=point_scroll.set);point_scroll.pack(side='right',fill='y')
        self.point_list.pack(fill='both',expand=True);self.point_list.bind('<<ListboxSelect>>',self.points_selected)
        row=ttk.Frame(left);row.grid(row=5,column=0,sticky='ew',pady=4)
        self.button(row,'＋ 点',self.add_point,tip='双击空白处也可添加控制点').pack(side='left')
        self.button(row,'删除',self.delete_points,tip='Delete · 删除已选控制点').pack(side='right')
        row=ttk.Frame(left);row.grid(row=6,column=0,sticky='ew')
        edit_menu=tk.Menu(row,tearoff=False)
        for label,cmd in [('在所选点后插入',self.insert_point),('复制选中点 · Ctrl+D',self.duplicate_points),
                         ('上移',lambda:self.reorder(-1)),('下移',lambda:self.reorder(1)),('全选 · Ctrl+A',self.select_all)]:edit_menu.add_command(label=label,command=cmd)
        ttk.Menubutton(row,text='点操作 ▾',menu=edit_menu).pack(fill='x')
        center=ttk.Frame(self.editor_panes);center.columnconfigure(0,weight=1);center.rowconfigure(0,weight=1)
        self.editor_panes.add(center,weight=1)
        self.canvas=TrajectoryCanvas(center,self);self.canvas.grid(row=0,column=0,sticky='nsew')
        ttk.Label(center,textvariable=self.coordinates,style='Muted.TLabel',padding=(8,4)).grid(row=1,column=0,sticky='ew')
        dpi=float(self.tk.call('tk','scaling'))/(96/72)
        inspector_width=round(318*max(1,min(dpi,1.45)))
        inspector=ttk.Frame(self.editor_panes,width=inspector_width);self.editor_panes.add(inspector,weight=0)
        self.notebook=ttk.Notebook(inspector,width=inspector_width);self.notebook.pack(fill='both',expand=True)
        self.inspector_scrolls={}
        for name,title in [('empty','开始编辑'),('point','控制点'),('transform','点组变换'),('image','底图'),('preview','扰动与网格')]:
            scroll=ScrollFrame(self.notebook);self.inspector_scrolls[name]=scroll
            setattr(self,name+'_panel',scroll.body);self.notebook.add(scroll,text=title)
        self._build_point_panel();self._build_transform_panel();self._build_image_panel();self._build_preview_panel()
        ttk.Label(self.empty_panel,text='从这里开始',style='Section.TLabel').pack(anchor='w',pady=(0,14))
        ttk.Label(self.empty_panel,text='1  选择左侧模式\n2  点击或拖动控制点\n3  播放预览，再保存工程',style='Muted.TLabel',justify='left').pack(anchor='w')
        self.button(self.empty_panel,'添加控制点',self.add_point,style='Accent.TButton').pack(fill='x',pady=(20,8))
        self.button(self.empty_panel,'导入参考底图',self.choose_image).pack(fill='x')
        ttk.Label(self.empty_panel,text='双击空白添加 · 拖动空白框选\nShift 增选 · 滚轮缩放 · 右键平移',style='Muted.TLabel',wraplength=265).pack(anchor='w',pady=18)
        for name,scroll in self.inspector_scrolls.items():
            if name not in ('empty','preview'):self.notebook.hide(scroll)

    def _build_modes_workspace(self):
        row=ttk.Frame(self.modes_workspace,padding=(12,10));row.pack(fill='x')
        ttk.Label(row,text='当前模式').pack(side='left',padx=(0,10))
        self.settings_mode_box=ttk.Combobox(row,state='readonly',width=30)
        self.settings_mode_box.pack(side='left');self.settings_mode_box.bind('<<ComboboxSelected>>',self.settings_mode_selected)
        ttk.Label(row,text='修改自动保留在草稿；应用 RAM 与保存文件分别操作。',style='Muted.TLabel').pack(side='left',padx=16)
        self.mode_tools=ttk.Notebook(self.modes_workspace);self.mode_tools.pack(fill='both',expand=True,padx=6,pady=(0,6))
        self.mode_hosts={}
        for key,title in [('triggers','模式切换与执行键'),('run','运行与停止'),('bypass','纯物理旁路')]:
            host=ttk.Frame(self.mode_tools);self.mode_hosts[key]=host;self.mode_tools.add(host,text=title)
        self.mode_tools.bind('<<NotebookTabChanged>>',self.mode_tool_changed)

    def _build_device_workspace(self):
        panes=ttk.Panedwindow(self.device_workspace,orient='horizontal');panes.pack(fill='both',expand=True,pady=8)
        self.device_scroll=ScrollFrame(panes);self.device_panel=self.device_scroll.body;panes.add(self.device_scroll,weight=1)
        self._build_device_panel()
        actions=ScrollFrame(panes);panes.add(actions,weight=1)
        p=actions.body
        ttk.Label(p,text='保存与应用',style='Section.TLabel').pack(anchor='w',pady=(0,16))
        for title,description,button,command in [
            ('电脑上的工程','保存控制点、底图、按键和运行设置。','保存工程',self.choose_save),
            ('设备的临时配置','应用全部模式与规则；断电后丢失，不自动开始运动。','应用工程到 RAM',self.apply_project_ram)]:
            box=ttk.Labelframe(p,text=title,padding=12);box.pack(fill='x',pady=(0,14))
            ttk.Label(box,text=description,wraplength=430,style='Muted.TLabel').pack(anchor='w',pady=(0,10))
            action=self.button(box,button,command,style='Accent.TButton');action.pack(anchor='w')
            if button=='应用工程到 RAM':self.ram_button=action
        flash=ttk.Labelframe(p,text='设备的永久配置',padding=12);flash.pack(fill='x')
        self.flash_description=tk.StringVar(value='连接支持Flash保存的固件后，先应用当前工程到RAM。')
        ttk.Label(flash,textvariable=self.flash_description,wraplength=430,style='Muted.TLabel').pack(anchor='w',pady=(0,10))
        self.flash_button=self.button(flash,'保存到设备 Flash',self.save_project_flash,state='disabled',style='Accent.TButton');self.flash_button.pack(anchor='w')
        self.button(p,'打开运行与停止设置',self.show_presets).pack(anchor='w',pady=14)
        advanced=Disclosure(p,'高级：导出与模拟存储');advanced.pack(fill='x',pady=8)
        for title,command in [('导出工程副本',self.export_project),('导出编译数据',self.export_compiled),
                 ('导出设备关键点 MFT2',self.export_executable),('保存模拟 NOR',lambda:self.request_api('save_simulated_presets',safe_stopped=True)),
                 ('恢复模拟 NOR',lambda:self.request_api('restore_simulated_presets')),('保存界面截图',self.capture_screenshot)]:
            b=self.button(advanced.body,title,command);b.pack(fill='x',pady=3)
            if self.live_device and '模拟 NOR' in title:b.state(['disabled'])
        ttk.Label(advanced.body,textvariable=self.compile_details,wraplength=420,style='Muted.TLabel').pack(anchor='w',pady=8)

    def sync_inspector(self):
        if not hasattr(self,'inspector_scrolls'):return
        context='image' if self.edit_mode.get()=='图片编辑' else ('point' if len(self.editor.selection)==1 else 'group' if self.editor.selection else 'empty')
        name='transform' if context=='group' else context
        target=self.inspector_scrolls[name]
        if context!=self.inspector_context:
            for key,scroll in self.inspector_scrolls.items():
                if key not in (name,'preview'):self.notebook.hide(scroll)
            self.notebook.add(target);self.notebook.select(target)
            self.inspector_context=context
            target.canvas.yview_moveto(0)

    def workspace_changed(self,event=None):
        if event and event.widget is not self.workspaces:return
        if self.workspaces.select()==str(self.modes_workspace):self.mode_tool_changed()
        if hasattr(self,'run_panel') and self.run_panel.winfo_exists():self.run_panel.virtual_up()
        if hasattr(self,'bypass_panel') and self.bypass_panel.winfo_exists():self.bypass_panel.release_virtual()

    def mode_tool_changed(self,event=None):
        if event and event.widget is not self.mode_tools:return
        selected=self.mode_tools.select()
        if hasattr(self,'run_panel') and self.run_panel.winfo_exists():self.run_panel.virtual_up()
        if hasattr(self,'bypass_panel') and self.bypass_panel.winfo_exists():self.bypass_panel.release_virtual()
        for key,host in self.mode_hosts.items():
            if selected==str(host):self.ensure_mode_tool(key)

    def settings_mode_selected(self,event=None):
        if self.has_pending_forms():
            self.require_applied_forms();self.settings_mode_box.current(self.editor.project.active_mode);return False
        self.mode_list.selection_clear(0,'end');self.mode_list.selection_set(self.settings_mode_box.current())
        self.mode_selected()
        if hasattr(self,'run_panel') and self.run_panel.winfo_exists():
            self.run_panel.slot=self.editor.project.active_mode;self.run_panel.sync_project()
        return True

    def ensure_mode_tool(self,key):
        attr={'triggers':'trigger_manager','run':'run_panel','bypass':'bypass_panel'}[key]
        panel=getattr(self,attr,None)
        if panel is not None and panel.winfo_exists():return panel
        if key=='triggers':
            from .trigger_ui import TriggerManager
            panel=TriggerManager(self,parent=self.mode_hosts[key])
        elif key=='run':
            from .run_ui import RunPanel
            panel=RunPanel(self,parent=self.mode_hosts[key]);self.preset_window=panel
        else:
            from .bypass_ui import BypassPanel
            panel=BypassPanel(self,parent=self.mode_hosts[key])
        setattr(self,attr,panel)
        return panel

    def show_mode_tool(self,key):
        if self.root.state()=='withdrawn':
            self.root.geometry(f'{min(1366,self.root.winfo_screenwidth()-40)}x{min(768,self.root.winfo_screenheight()-80)}')
        self.root.deiconify();self.workspaces.select(self.modes_workspace)
        self.mode_tools.select(self.mode_hosts[key]);return self.ensure_mode_tool(key)

    def show_device(self):self.workspaces.select(self.device_workspace)
    def show_editor(self):self.workspaces.select(self.edit_workspace)
