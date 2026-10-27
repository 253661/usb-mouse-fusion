"""Windows device-removal notification latch, independent of reusable USB IDs."""
import ctypes as c, uuid

class Filter(c.Structure):
    _fields_=[('size',c.c_uint32),('flags',c.c_uint32),('kind',c.c_uint32),('reserved',c.c_uint32),('data',c.c_byte*400)]

class UsbWatch:
    def __init__(self):
        self.changed=False;self.token=c.c_void_p();self.api=c.WinDLL('cfgmgr32')
        callback_type=c.WINFUNCTYPE(c.c_uint32,c.c_void_p,c.c_void_p,c.c_uint32,c.c_void_p,c.c_uint32)
        def callback(notification,context,action,event,size):
            # CM_NOTIFY_EVENT_DATA device-interface symbolic link starts at byte24.
            if action in (0,1) and size>=26:
                raw=c.string_at(event,size)
                name=raw[24:].decode('utf-16-le',errors='ignore').lower()
                if 'vid_2e8a&pid_0003' in name:self.changed=True
            return 0
        self.callback=callback_type(callback)
        self.api.CM_Register_Notification.argtypes=[c.POINTER(Filter),c.c_void_p,callback_type,c.POINTER(c.c_void_p)]
        self.api.CM_Register_Notification.restype=c.c_uint32
        self.api.CM_Unregister_Notification.argtypes=[c.c_void_p];self.api.CM_Unregister_Notification.restype=c.c_uint32
        f=Filter();f.size=c.sizeof(f);f.kind=0
        guid=uuid.UUID('a5dcbf10-6530-11d2-901f-00c04fb951ed').bytes_le
        for i,b in enumerate(guid):f.data[i]=b if b<128 else b-256
        if self.api.CM_Register_Notification(c.byref(f),None,self.callback,c.byref(self.token)):
            raise ValueError('无法建立USB断开监视；未写入')
    def check(self):
        if self.changed:raise ValueError('操作期间BOOTSEL连接已断开或换板，请重新检测；未继续写入')
    def close(self):
        if self.token:self.api.CM_Unregister_Notification(self.token);self.token=c.c_void_p()
