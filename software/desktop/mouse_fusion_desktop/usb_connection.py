"""Pin bus/address, retain libusb device reference and latch USB reconnect events."""
import ctypes as c
from pathlib import Path

class Descriptor(c.Structure):
    _fields_=[('length',c.c_uint8),('type',c.c_uint8),('usb',c.c_uint16),
        ('device_class',c.c_uint8),('subclass',c.c_uint8),('protocol',c.c_uint8),('packet',c.c_uint8),
        ('vid',c.c_uint16),('pid',c.c_uint16),('release',c.c_uint16),
        ('manufacturer',c.c_uint8),('product',c.c_uint8),('serial',c.c_uint8),('configs',c.c_uint8)]

class UsbConnection:
    def __init__(self,dll):
        self.dll=c.CDLL(str(Path(dll)));self.context=c.c_void_p();self.handle=c.c_void_p()
        self.device=None;self.watch=None
        specs={
            'libusb_init':([c.POINTER(c.c_void_p)],c.c_int),
            'libusb_exit':([c.c_void_p],None),
            'libusb_get_device_list':([c.c_void_p,c.POINTER(c.POINTER(c.c_void_p))],c.c_ssize_t),
            'libusb_free_device_list':([c.POINTER(c.c_void_p),c.c_int],None),
            'libusb_get_device_descriptor':([c.c_void_p,c.POINTER(Descriptor)],c.c_int),
            'libusb_get_bus_number':([c.c_void_p],c.c_uint8),
            'libusb_get_device_address':([c.c_void_p],c.c_uint8),
            'libusb_open':([c.c_void_p,c.POINTER(c.c_void_p)],c.c_int),
            'libusb_ref_device':([c.c_void_p],c.c_void_p),
            'libusb_unref_device':([c.c_void_p],None),
            'libusb_close':([c.c_void_p],None),
            'libusb_get_configuration':([c.c_void_p,c.POINTER(c.c_int)],c.c_int),
        }
        for name,(args,result) in specs.items():
            fn=getattr(self.dll,name);fn.argtypes=args;fn.restype=result
    def open(self):
        from .usb_watch import UsbWatch
        devices=c.POINTER(c.c_void_p)()
        try:
            self.watch=UsbWatch()
            if self.dll.libusb_init(c.byref(self.context)):
                self.context=c.c_void_p()
                raise ValueError('无法初始化USB连接保护')
            count=self.dll.libusb_get_device_list(self.context,c.byref(devices))
            if count<0:raise ValueError('无法枚举USB连接')
            try:
                found=[]
                for i in range(count):
                    desc=Descriptor()
                    if not self.dll.libusb_get_device_descriptor(devices[i],c.byref(desc)) and (desc.vid,desc.pid)==(0x2e8a,0x0003):found.append(devices[i])
                if len(found)!=1:raise ValueError('请只连接一块BOOTSEL板子')
                device=found[0];self.bus=self.dll.libusb_get_bus_number(device);self.address=self.dll.libusb_get_device_address(device)
                self.device=self.dll.libusb_ref_device(device)
            finally:self.dll.libusb_free_device_list(devices,1)
            self.check();return self
        except BaseException:self.close();raise
    def check(self):
        self.watch.check()
        config=c.c_int()
        try:
            if not self.device or self.dll.libusb_open(self.device,c.byref(self.handle)) or self.dll.libusb_get_configuration(self.handle,c.byref(config)):
                raise ValueError('检测期间USB连接已断开或换板；请重新检测，未继续写入')
        finally:
            if self.handle:self.dll.libusb_close(self.handle);self.handle=c.c_void_p()
        self.watch.check()
    def close(self):
        if self.handle:self.dll.libusb_close(self.handle);self.handle=c.c_void_p()
        if self.device:self.dll.libusb_unref_device(self.device);self.device=None
        if self.context:self.dll.libusb_exit(self.context);self.context=c.c_void_p()
        if self.watch:self.watch.close();self.watch=None
    def selection(self):return ['--bus',str(self.bus),'--address',str(self.address)]
