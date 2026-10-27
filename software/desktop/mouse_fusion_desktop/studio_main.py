from pathlib import Path
import argparse,json,sys,tkinter as tk,uuid
from tkinter import ttk,messagebox
from mouse_fusion_studio.app import StudioApp
from mouse_fusion_studio.connection import StudioConnection
from mouse_fusion_studio.project_io import load_project,atomic_json
from .flash_core import BoardRegistry,FirmwarePackage
from .runtime import assets,data_directory,streams,ports

def connection_options(root,directory):
    try:records=BoardRegistry(directory/'boards.json').records()
    except Exception as e:
        records={};messagebox.showwarning('板子记录无法读取',f'{directory / "boards.json"}\n{e}\n\n可以继续离线编辑；实机连接前请检查并保留原记录。',parent=root)
    mapping={r['serial']:uid for uid,r in records.items()}
    prefs=directory/'connection.json'
    try:
        previous=json.loads(prefs.read_text(encoding='utf8')) if prefs.exists() else {}
        if not isinstance(previous,dict) or any(not isinstance(v,str) for v in previous.values()):raise ValueError('连接偏好格式不正确')
    except Exception as e:
        previous={};messagebox.showwarning('连接偏好无法读取',f'{prefs}\n{e}\n\n请重新选择使用方式。',parent=root)
    dialog=tk.Toplevel(root);dialog.title('打开 Mouse Fusion Studio');dialog.geometry('540x350');dialog.resizable(False,False)
    p=ttk.Frame(dialog,padding=24);p.pack(fill='both',expand=True)
    ttk.Label(p,text='选择这次怎样使用',font=('Microsoft YaHei UI',15,'bold')).pack(anchor='w',pady=(0,18))
    mode=tk.StringVar(value=previous.get('mode','离线编辑') if previous.get('mode','离线编辑') in ('离线编辑','连接真实设备','连接模拟设备') else '离线编辑');com=tk.StringVar(value=previous.get('com',''))
    board=tk.StringVar(value=previous.get('serial',next(iter(mapping),'')))
    options=ports()
    if not com.get() and len(options)==1:com.set(options[0])
    for label,var,values in [('使用方式',mode,['离线编辑','连接真实设备','连接模拟设备']),('J4串口',com,options),('板子USB串号',board,list(mapping))]:
        row=ttk.Frame(p);row.pack(fill='x',pady=6);ttk.Label(row,text=label,width=17).pack(side='left')
        ttk.Combobox(row,textvariable=var,values=values,width=29,state='readonly' if var is not com else 'normal').pack(side='left')
    ttk.Label(p,text='新板由烧录工具记录后，会出现在板子列表。\n实机启动只读取身份，不自动应用轨迹或发送运动。',wraplength=460).pack(anchor='w',pady=16)
    chosen={}
    def accept():
        if mode.get()=='连接真实设备' and (board.get() not in mapping or not __import__('re').fullmatch('COM[1-9][0-9]*',com.get(),__import__('re').I)):
            return messagebox.showerror('请选择设备','请选择板子串号，并填写正确的J4 COM编号。',parent=dialog)
        selection=dict(mode=mode.get(),com=com.get().upper(),serial=board.get(),board_id=mapping.get(board.get()))
        try:atomic_json(prefs,{k:v for k,v in selection.items() if k!='board_id'})
        except Exception as e:return messagebox.showerror('连接偏好保存失败',f'{prefs}\n{e}',parent=dialog)
        chosen.update(selection);dialog.destroy()
    ttk.Button(p,text='打开 Studio',command=accept).pack(anchor='e')
    dialog.protocol('WM_DELETE_WINDOW',dialog.destroy);dialog.grab_set();root.wait_window(dialog)
    return chosen or None

def main():
    if len(sys.argv)>1 and sys.argv[1]=='--service':
        sys.argv.pop(1)
        endpoint=Path(sys.argv[sys.argv.index('--endpoint')+1]).parent if '--endpoint' in sys.argv else data_directory()
        endpoint.mkdir(parents=True,exist_ok=True);streams(endpoint)
        from mouse_fusion.service import main as service_main
        return service_main()
    parser=argparse.ArgumentParser();parser.add_argument('--self-test',type=Path);parser.add_argument('--data-dir',type=Path);parser.add_argument('--edit-only',action='store_true')
    args=parser.parse_args();directory=data_directory(args.data_dir or (args.self_test/'Data' if args.self_test else None));streams(directory)
    root=tk.Tk();root.withdraw()
    if args.self_test or args.edit_only:options=dict(mode='离线编辑')
    else:
        options=connection_options(root,directory)
        if options is None:root.destroy();return 0
    sim=assets()/'simulator/j4_preset_simulator.exe'
    persistent=FirmwarePackage.load(assets()/'latest.mffw').manifest.get('device_flash',False)
    kwargs=dict(preset_c_simulator=sim,simulate_device_flash=persistent)
    if options['mode']=='连接真实设备':kwargs=dict(live_com=options['com'],board_id=options['board_id'],allow_live_device=True)
    project=load_project(assets()/'blank.mfusion')
    root.geometry(f'{min(1480,root.winfo_screenwidth()-60)}x{min(940,root.winfo_screenheight()-100)}');root.minsize(1000,640)
    app=StudioApp(root,project=project,**kwargs)
    app.connection=StudioConnection(runtime_dir=directory/'runtime'/uuid.uuid4().hex,**kwargs)
    root.deiconify()
    def close():
        if app.confirm_unsaved():app.dispose();root.destroy()
    root.protocol('WM_DELETE_WINDOW',close)
    if args.self_test:
        from .smoke import studio_smoke
        return studio_smoke(root,app,args.self_test)
    if options['mode']!='离线编辑':app.show_device();root.after(100,lambda:app.request_api('connect'))
    root.mainloop();return 0
