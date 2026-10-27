from pathlib import Path
import os,sys

def assets():
    if not getattr(sys,'frozen',False) and os.environ.get('FUSION_DESKTOP_ASSETS'):return Path(os.environ['FUSION_DESKTOP_ASSETS'])
    return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))/'assets'
def data_directory(override=None):
    path=Path(override) if override else Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'MouseFusionStudio'
    path.mkdir(parents=True,exist_ok=True);return path
def streams(directory):
    if sys.stdout is None:sys.stdout=open(os.devnull,'w')
    if sys.stderr is None:
        (directory/'logs').mkdir(exist_ok=True)
        sys.stderr=(directory/'logs'/f'application-{os.getpid()}.log').open('a',encoding='utf8',buffering=1)

def launch_gui(main):
    try:return main()
    except Exception as e:
        import tkinter as tk,traceback
        from tkinter import messagebox
        traceback.print_exc()
        root=tk._default_root;owned=root is None
        if owned:root=tk.Tk();root.withdraw()
        try:messagebox.showerror('Mouse Fusion 无法启动',f'{e}\n\n请完整解压软件包，并检查记录目录或应用日志。',parent=root)
        finally:
            if owned:root.destroy()
        return 1
def ports():
    if os.name!='nt':return []
    import winreg
    result=[]
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'HARDWARE\DEVICEMAP\SERIALCOMM') as key:
            for i in range(winreg.QueryInfoKey(key)[1]):result.append(winreg.EnumValue(key,i)[1])
    except OSError:pass
    return sorted(set(result),key=lambda p:int(p[3:]) if p.startswith('COM') else 1000000)
