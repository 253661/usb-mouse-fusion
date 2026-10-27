"""SRAM-only identity probe protocol. Never substitutes a ROM serial for Flash UID."""
import binascii,struct,time,hashlib,os
from pathlib import Path

MAILBOX=0x20040000
SIZE=96
PROBE_SHA256='b22e552776aa124029706946bed5b970908655fb6e912ec69eee28b327d3edd3'

def ram_segments(data):
    if len(data)<52 or data[:7]!=b'\x7fELF\x01\x01\x01':raise ValueError('RAM探测ELF格式不正确')
    h=struct.unpack_from('<16sHHIIIIIHHHHHH',data)
    if h[1:4]!=(2,40,1) or h[8:10]!=(52,32):raise ValueError('RAM探测ELF头不正确')
    entry,offset,count=h[4],h[5],h[10]
    if not count or offset<52 or offset+count*32>len(data):raise ValueError('RAM探测段表不正确')
    headers=[data[offset+i*32:offset+(i+1)*32] for i in range(count)]
    executable=False
    for raw in headers:
        kind,filepos,vaddr,paddr,filesz,memsz,flags,align=struct.unpack('<8I',raw)
        if kind!=1:continue
        if not 0x20000000<=paddr<0x20042000 or memsz>0x20042000-paddr or vaddr!=paddr or filesz>memsz or filepos+filesz>len(data):raise ValueError('RAM探测包含非法加载地址')
        if memsz and paddr<MAILBOX+SIZE and paddr+memsz>MAILBOX:raise ValueError('RAM探测占用了身份信箱')
        executable|=bool(flags&1 and paddr<=entry&~1<paddr+filesz)
    if not entry&1 or not executable:raise ValueError('RAM探测入口不正确')
    return headers

def make_request_elf(template,nonce):
    if len(nonce)!=16:raise ValueError('探测随机码必须是16字节')
    headers=ram_segments(template)
    request=struct.pack('<4sI16s',b'MFIQ',1,nonce)+bytes(SIZE-24)
    result=bytearray(template);position=len(result);result+=request
    headers.append(struct.pack('<8I',1,position,MAILBOX,MAILBOX,SIZE,SIZE,6,4))
    table=len(result);result+=b''.join(headers)
    struct.pack_into('<I',result,28,table);struct.pack_into('<H',result,44,len(headers))
    return bytes(result)

def parse_response(data,nonce):
    if len(data)!=SIZE:raise ValueError('Flash身份响应不完整；未写入')
    magic,version,echo,status=struct.unpack_from('<4sI16sI',data)
    if (magic,version,echo,status)!=(b'MFIR',1,nonce,1):raise ValueError('Flash身份探测未完成或目标已变化；未写入')
    if binascii.crc32(data[:80])!=struct.unpack_from('<I',data,80)[0]:raise ValueError('Flash身份响应校验失败；未写入')
    if data[28:31]!=data[31:34] or data[40:48]!=data[48:56]:raise ValueError('Flash身份读取不稳定；检查连接；未写入')
    if data[28:31]!=b'\xef\x40\x15':raise ValueError('Flash型号/容量未确认：JEDEC='+data[28:31].hex().upper()+'，要求W25Q16/2MB；未写入')
    if any(struct.unpack_from('<6I',data,56)):raise ValueError('Flash通信引脚电平异常；未写入')
    uid=data[40:48]
    if uid in (bytes(8),b'\xff'*8):raise ValueError('Flash唯一编号无效；未写入')
    return uid.hex().upper()

def run_probe(backend,target,folder,progress):
    from .runtime import assets
    from .flash_core import SELECTION
    template=(assets()/'flash_identity.elf').read_bytes()
    if hashlib.sha256(template).hexdigest()!=PROBE_SHA256:raise ValueError('RAM身份探测文件校验失败，请完整解压新工具')
    nonce=os.urandom(16);folder=Path(folder)
    request=folder/'identity-request.elf';response=folder/'identity-response.bin'
    request.write_bytes(make_request_elf(template,nonce))
    # Windows PnP instance suffix is not necessarily picotool's USB serial filter.
    # The caller enforces exactly one ROM device; nonce and Flash UID bind the result.
    select=SELECTION.copy()
    progress('正在只读识别新板 Flash，完成后自动回到 BOOTSEL…')
    loaded=backend.command(['load','-v','-x',str(request),'-t','elf',*select])
    (folder/'probe-load.log').write_text(loaded.output,encoding='utf8')
    if loaded.exit_code or 'Loading into RAM' not in loaded.output or 'Verifying RAM' not in loaded.output or 'OK' not in loaded.output or 'Loading into Flash' in loaded.output:raise ValueError('RAM身份探测加载未确认；请重新进入BOOTSEL；未写入Flash')
    deadline=time.monotonic()+15
    while True:
        devices=backend.devices()
        if len(devices)>1:raise ValueError('出现多块BOOTSEL板，请只接一块；未写入')
        if len(devices)==1:
            if devices[0].rom_serial.upper()!=target.rom_serial.upper() or devices[0].status!='OK':raise ValueError('RAM探测后目标已变化或状态异常；未写入')
            break
        if time.monotonic()>deadline:raise ValueError('RAM探测后未回到BOOTSEL，请重新进入；未写入')
        time.sleep(.2)
    if hasattr(backend,'reconnect_after_probe'):backend.reconnect_after_probe()
    saved=backend.command(['save','-r',hex(MAILBOX),hex(MAILBOX+SIZE),str(response),'-t','bin',*select])
    (folder/'probe-save.log').write_text(saved.output,encoding='utf8')
    if saved.exit_code:raise ValueError('读取RAM身份结果失败；未写入')
    return parse_response(response.read_bytes(),nonce)
