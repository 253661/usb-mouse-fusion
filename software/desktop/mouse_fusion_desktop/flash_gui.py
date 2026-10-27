"""User-operated BOOTSEL UI; there are no automatic device calls at startup."""
from pathlib import Path
import queue,threading,tkinter as tk
from tkinter import ttk,filedialog,messagebox
from .flash_core import BoardRegistry,FirmwarePackage,Flasher,NativeBackend
from .runtime import assets

class FlashToolApp(ttk.Frame):
    def __init__(self,root,data,backend=None):
        super().__init__(root,padding=22);self.pack(fill='both',expand=True);self.root=root;self.data=Path(data)
        root.title('Mouse Fusion · 固件烧录工具');root.geometry('960x740');root.minsize(820,700)
        ttk.Style(root).configure('TLabel',font=('Microsoft YaHei UI',10))
        self.registry=BoardRegistry(self.data/'boards.json');self.backend=backend or NativeBackend(assets()/'picotool/picotool.exe')
        self.package=FirmwarePackage.load(assets()/'latest.mffw');self.target=None;self.busy=False;self.events=queue.Queue()
        self.package_text=tk.StringVar(value='随附最新固件：'+self.package.manifest['firmware'])
        self.identity_text=tk.StringVar(value='尚未检测设备');self.status=tk.StringVar(value='软件就绪。先让板子进入 BOOTSEL，再点击“检测板子”。')
        self.j1_only=tk.BooleanVar(value=False)
        ttk.Label(self,text='手动烧录 Mouse Fusion 固件',font=('Microsoft YaHei UI',18,'bold')).pack(anchor='w')
        ttk.Label(self,text='适用于 C4-L4 · RP2040 · 2MB Flash。每次只连接一块板。').pack(anchor='w',pady=(8,20))
        firmware=ttk.Labelframe(self,text='1  固件',padding=12);firmware.pack(fill='x',pady=6)
        ttk.Label(firmware,textvariable=self.package_text).pack(side='left')
        self.choose_button=ttk.Button(firmware,text='选择其他固件包…',command=self.choose_package);self.choose_button.pack(side='right')
        board=ttk.Labelframe(self,text='2  板子',padding=12);board.pack(fill='x',pady=6)
        ttk.Label(board,text='断开其他连接，按住板上 SW2/BOOTSEL，只接 J1 到电脑，再松开按钮。',wraplength=800).pack(anchor='w')
        ttk.Label(board,textvariable=self.identity_text,wraplength=800).pack(anchor='w',pady=12)
        self.check=ttk.Checkbutton(board,text='确认是 Mouse Fusion C4-L4，只有 J1 连接，J2/J3/J4 已断开；J5 保持原样',variable=self.j1_only);self.check.pack(anchor='w',pady=(0,10))
        self.detect_button=ttk.Button(board,text='检测板子',command=self.detect);self.detect_button.pack(anchor='w')
        confirm=ttk.Labelframe(self,text='3  开始',padding=12);confirm.pack(fill='x',pady=6)
        self.flash_button=ttk.Button(confirm,text='开始烧录并校验',command=self.flash,state='disabled');self.flash_button.pack(anchor='w',pady=(14,0))
        self.bar=ttk.Progressbar(self,mode='indeterminate');self.bar.pack(fill='x',pady=14)
        ttk.Label(self,textvariable=self.status,wraplength=850).pack(anchor='w')
        self.next_step=tk.StringVar();self.sync_next_step()
        ttk.Label(self,textvariable=self.next_step,wraplength=850).pack(anchor='w',pady=16)
        ttk.Label(self,text='板子串号和每次烧录记录自动保存到：'+str(self.data),wraplength=850).pack(anchor='w')
        root.protocol('WM_DELETE_WINDOW',self.close);self.job=self.after(60,self.poll)
    def progress(self,text):self.events.put(('progress',text))
    def controller(self):return Flasher(self.backend,self.registry,self.data/'flash-logs',self.progress)
    def run(self,action,callback):
        self.busy=True;self.bar.start(12)
        for w in (self.detect_button,self.choose_button,self.flash_button,self.check):w.state(['disabled'])
        def work():
            try:self.events.put(('done',(callback,action())))
            except Exception as e:self.events.put(('error',str(e)))
        threading.Thread(target=work,daemon=True).start()
    def detect(self):
        if not self.j1_only.get():return messagebox.showwarning('先确认接线','请确认仅J1连接并勾选。检测新板会临时运行RAM只读探测，完成后自动回到BOOTSEL，不写入Flash。',parent=self.root)
        self.run(self.controller().detect,self.detected)
    def detected(self,target):
        self.target=target;serial=self.registry.reserve(target.flash_id)
        self.identity_text.set(f'BOOTSEL：{target.rom_serial}    Flash板号：{target.flash_id}\nUSB串号：{serial}（更新同一块板时保持此串号）')
        self.status.set('身份核对完成。确认接线后，点击“开始烧录并校验”。')
    def sync_next_step(self):
        text='成功后：拔下 J1，再正常插回（不要按 BOOTSEL），然后接回 J2 鼠标和 J4。\n'
        text+=('烧录保留已有永久配置。新板请在 Studio 编辑 → 应用到 RAM → 保存到设备 Flash，以后断电仍可使用。'
               if self.package.manifest.get('device_flash') else '当前固件只支持临时轨迹。请在 Studio 应用到 RAM，断电后需重新应用。')
        self.next_step.set(text)
    def choose_package(self):
        path=filedialog.askopenfilename(parent=self.root,filetypes=[('Mouse Fusion固件包','*.mffw')])
        if path:
            try:self.package=FirmwarePackage.load(path);self.package_text.set(str(path)+' · '+self.package.manifest['firmware']);self.sync_next_step()
            except Exception as e:messagebox.showerror('固件包无法使用',str(e),parent=self.root)
    def flash(self):
        if not self.target or not self.j1_only.get():return messagebox.showwarning('尚未准备好','请先检测板子，并勾选接线确认。',parent=self.root)
        try:serial=self.registry.reserve(self.target.flash_id)
        except Exception as e:
            self.status.set(str(e));messagebox.showerror('记录失败',str(e),parent=self.root);return
        if not messagebox.askyesno('确认烧录',f'固件：{self.package.manifest["firmware"]}\n板号：{self.target.flash_id}\nUSB串号：{serial}\n\n开始写入程序区并独立校验？',parent=self.root):return
        self.run(lambda:self.controller().flash(self.target,self.package,authorized=True,j1_only=True),self.flashed)
    def flashed(self,record):self.status.set(f'烧录与独立校验成功 · {record["usb_serial"]}。现在可以正常重插 J1。')
    def poll(self):
        while not self.events.empty():
            kind,value=self.events.get()
            if kind=='progress':self.status.set(value);continue
            self.busy=False;self.bar.stop()
            for w in (self.detect_button,self.choose_button,self.check):w.state(['!disabled'])
            if kind=='error':
                self.target=None;self.status.set(value);messagebox.showerror('已停止操作',value,parent=self.root)
            else:
                callback,result=value
                try:callback(result)
                except Exception as e:self.target=None;self.status.set(str(e));messagebox.showerror('记录失败',str(e),parent=self.root)
            self.flash_button.state(['!disabled'] if self.target else ['disabled'])
        self.job=self.after(60,self.poll)
    def close(self):
        if self.busy:return messagebox.showwarning('请等待','正在操作板子，请等本次写入/校验结束后再关闭。',parent=self.root)
        if self.job:self.after_cancel(self.job)
        self.root.destroy()
