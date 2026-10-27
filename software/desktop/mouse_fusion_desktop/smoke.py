"""Explicit packaged self-test mode. Fake USB backend and production C simulator only."""
from pathlib import Path
import json,sys,time
from unittest.mock import patch
from .flash_core import CommandResult,Target,NativeBackend
from .runtime import assets

def dwell(root,seconds=.04):
    end=time.perf_counter()+seconds
    while time.perf_counter()<end:root.update();time.sleep(.004)
def wait(root,condition,seconds=8):
    end=time.perf_counter()+seconds
    while time.perf_counter()<end:
        root.update()
        if condition():return
        time.sleep(.006)
    raise AssertionError('packaged operation timed out')
def studio_smoke(root,app,out):
    from mouse_fusion_studio.model import Point
    from mouse_fusion_studio.project_io import load_project
    from mouse_fusion_studio.device_data import execution_signature
    from .flash_core import FirmwarePackage
    out=Path(out);out.mkdir(parents=True,exist_ok=True);report=dict(device_access='NONE',backend='production C simulator',frozen=bool(getattr(sys,'frozen',False)))
    try:
        root.geometry('1366x768+20+20');root.update()
        assert app.path is None and len(app.editor.project.modes)==8 and all(not m.points for m in app.editor.project.modes)
        def draw_modes():
            for i,mode in enumerate(app.editor.project.modes):mode.points=[Point(0,0,200),Point(12+i,-6-i)]
        app.editor.apply(draw_modes);app.after_edit()
        target=out/'我的轨迹.mfusion'
        with patch('mouse_fusion_studio.app.filedialog.asksaveasfilename',return_value=str(target)) as chooser:
            app.save_button.invoke();assert chooser.call_count==1
        assert load_project(target).modes==app.editor.project.modes and str(target) in app.status.get()
        app.request_api('connect');wait(root,lambda:app.connected)
        package=FirmwarePackage.load(assets()/'latest.mffw')
        info=app.connection.client.get_device_info();assert info['source']=='SIMULATION' and info['firmware']==package.manifest['firmware']
        app.apply_project_ram();wait(root,lambda:app._preset_applied_signature==execution_signature(app.editor.project))
        assert app.connection.client.get_preset_status()['loaded_mask']==255
        def physical(mask):app.request_api('simulate_mouse_buttons',mask);dwell(root)
        physical(8);physical(0);assert app.connection.client.get_mode_control_status()['enabled']
        physical(1);physical(0);wait(root,lambda:app.connection.client.get_run_status()['phase']=='idle')
        run=app.connection.client.get_run_status();assert (run['preset_x'],run['preset_y'])==(0,0)
        if package.manifest.get('device_flash'):
            draft=app.editor.project.to_dict()
            app.navigate('device');app.sync_flash_ui();root.lift();dwell(root,1.2)
            assert 'disabled' not in app.flash_button.state() and app.flash_button.winfo_ismapped()
            assert app.capture_screenshot(out/'studio_flash_ready.png')
            before=app.connection.client.get_device_persistence()
            with patch('mouse_fusion_studio.flash_ui.messagebox.askyesno',return_value=True):app.flash_button.invoke()
            wait(root,lambda:not app._flash_saving and app._device_persistence_status.get('confirmed'))
            saved=app._device_persistence_status
            assert saved['generation']==before['generation']+1 and saved['cookie']!=before['cookie']
            assert app.editor.project.to_dict()==draft and not app.connection.client.get_mode_control_status()['enabled']
            assert app.connection.client.get_preset_status()['loaded_mask']==255
            dwell(root,1.2);assert app.capture_screenshot(out/'studio_flash_saved.png')
            # End the C process and start a new one using only the NOR file.
            app.request_api('disconnect');wait(root,lambda:not app.connected)
            app.connection._stop_owned_process(app.connection.process)
            app.request_api('connect');wait(root,lambda:app.connected)
            restored=app.connection.client.get_device_persistence()
            assert restored['boot_result']==0 and restored['cookie']==saved['cookie'] and restored['generation']==saved['generation']
            assert app.connection.client.get_preset_status()['loaded_mask']==255
            assert app.connection.client.get_preset_status()['trigger_count']==16
            assert not app.connection.client.get_mode_control_status()['enabled']
            report.update(device_flash=True,flash_generation=saved['generation'],cold_process_restore=True,draft_preserved=True)
        app.navigate('home');root.lift();dwell(root,1.2);assert app.capture_screenshot(out/'studio.png')
        report.update(passed=True,save_reopen=True,frozen_service=True,firmware=info['firmware'],trigger_records=16,return_xy=[run['preset_x'],run['preset_y']])
    except Exception as e:report.update(passed=False,error=str(e));__import__('traceback').print_exc()
    finally:
        app.dispose();root.destroy();(out/'studio_smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return 0 if report.get('passed') else 1

class FakeUsbBackend:
    def __init__(self):self.calls=[];self.blank=False
    def devices(self):return [Target('AABBCCDDEE12')]
    def probe_identity(self,target,folder,progress):
        self.calls.append('probe');return 'E66590050F39A940'
    def command(self,args):
        self.calls.append(args[0])
        if args[0]=='info':return CommandResult(0,'Type: RP2040\n' if self.blank else 'Type: RP2040\nFlash size: 2048K\nFlash ID: 0xE66590050F39A940\n')
        return CommandResult(0,('Loading into Flash\n' if args[0]=='load' else '')+'Verifying Flash\nOK\n')
def flasher_smoke(root,app,out):
    from PIL import ImageGrab
    out=Path(out);out.mkdir(parents=True,exist_ok=True);report=dict(device_access='NONE',backend='explicit fake USB',frozen=bool(getattr(sys,'frozen',False)))
    try:
        fake=FakeUsbBackend();app.backend=fake;assert not fake.calls
        app.j1_only.set(True);app.detect_button.invoke();wait(root,lambda:not app.busy and app.target is not None)
        app.j1_only.set(True)
        with patch('mouse_fusion_desktop.flash_gui.messagebox.askyesno',return_value=True):app.flash_button.invoke()
        wait(root,lambda:not app.busy)
        record=app.registry.records()['E66590050F39A940']
        assert record['serial']=='M4ZSZABIPHGUUA' and record['result']=='FLASH_LOAD_INDEPENDENT_VERIFY_PASS'
        assert fake.calls==['info','info','load','verify']
        old_phases=fake.calls.copy();fake.calls.clear();fake.blank=True
        app.detect_button.invoke();wait(root,lambda:not app.busy)
        assert app.target is not None
        with patch('mouse_fusion_desktop.flash_gui.messagebox.askyesno',return_value=True):app.flash_button.invoke()
        wait(root,lambda:not app.busy)
        assert fake.calls==['info','probe','info','probe','load','verify']
        helper=NativeBackend._run([assets()/'picotool/picotool.exe','help'])
        (out/'picotool_help.txt').write_text(helper.output,encoding='utf8');assert helper.exit_code==0
        app.status.set('离线模拟验收：写入/校验流程已通过（没有访问设备）。')
        root.lift();dwell(root,1.2)
        assert app.flash_button.winfo_ismapped()
        # Capture only this self-test's own Tk window, like Studio's screenshot.
        import ctypes
        from ctypes import wintypes
        user32=ctypes.WinDLL('user32',use_last_error=True)
        user32.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];user32.GetAncestor.restype=wintypes.HWND
        hwnd=user32.GetAncestor(root.winfo_id(),2)
        ImageGrab.grab(window=hwnd).save(out/'flasher.png')
        from .flash_probe import PROBE_SHA256
        import hashlib
        assert hashlib.sha256((assets()/'flash_identity.elf').read_bytes()).hexdigest()==PROBE_SHA256
        report.update(passed=True,serial=record['serial'],phases=old_phases,blank_phases=fake.calls,picotool_help=True,probe_asset_verified=True)
    except Exception as e:report.update(passed=False,error=str(e));__import__('traceback').print_exc()
    finally:
        app.close();(out/'flasher_smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return 0 if report.get('passed') else 1
