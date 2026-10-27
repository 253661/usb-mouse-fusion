"""Explicit permanent device configuration; local file saving stays independent."""
from tkinter import messagebox
from .device_data import execution_signature
class FlashPersistenceUI:
    def sync_flash_ui(self):
        if not hasattr(self,'flash_button'):return
        capable=bool(self.connected and (self.device_capabilities or 0)&0x20000)
        current=bool(self._preset_applied_signature and self._preset_applied_signature==execution_signature(self.editor.project) and self._preset_applied_revision is not None)
        ready=capable and current and not self._flash_saving
        self.flash_button.state(['!disabled'] if ready else ['disabled'])
        self.ram_button.state(['disabled'] if self._flash_saving else ['!disabled'])
        if self._flash_saving:text='正在保存并重启，请保持供电。修改的草稿继续留在APP。'
        elif not capable:text='当前连接未提供设备Flash保存能力；需要支持此功能的新固件。'
        else:
            saved=self._device_persistence_status
            state=f'开机恢复成功 · 第 {saved["generation"]} 版。' if saved.get('boot_result')==0 else '设备尚无有效的永久配置。' if saved.get('boot_result')==9 else '请检查设备恢复状态。'
            text=state+'\n'+('可永久保存当前RAM配置；保存时短暂暂停鼠标并重启。' if current else '请先将当前工程应用到RAM，再保存到Flash。')
        self.flash_description.set(text)
    def save_project_flash(self):
        if not self.require_applied_forms():return
        self.sync_flash_ui()
        if 'disabled' in self.flash_button.state():return messagebox.showwarning('尚未准备好','请连接支持Flash保存的固件，并先应用当前工程到RAM。',parent=self.root)
        if not messagebox.askyesno('永久保存设备配置','将当前已应用的轨迹、按键和运行规则保存到设备Flash。\n\n请松开鼠标按键。保存会停止辅助、短暂断开鼠标并重启设备；期间保持供电。\n重启后仍是纯物理控制，选择模式并按执行键才运行。\n\n电脑上的可编辑工程请另行保存。现在开始？',parent=self.root):return
        self._flash_saving=True;self.sync_flash_ui();self.status.set('正在保存设备Flash并等待重启确认，请勿断电…')
        try:self.connection.submit('save_project_flash',expected_ram_revision=self._preset_applied_revision)
        except (ValueError,OSError) as e:self.finish_flash(error=e)
    def finish_flash(self,result=None,error=None):
        self._flash_saving=False;self._preset_applied_signature=None;self._preset_applied_revision=None
        if error:
            self.report_error(error);messagebox.showerror('Flash保存未确认',str(error)+'\n草稿保留，没有自动重写。',parent=self.root)
        else:
            self._device_persistence_status=result
            self.connected=True;self.device_capabilities=result['info']['capabilities']
            self.status.set(f'设备 Flash 保存成功 · 第 {result["generation"]} 版 · 已重启恢复，模式关闭')
        self.sync_flash_ui()
