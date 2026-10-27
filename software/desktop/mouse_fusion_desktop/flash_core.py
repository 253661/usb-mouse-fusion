"""Bounded manual firmware writes. Construction never accesses USB hardware."""
from contextlib import contextmanager,nullcontext
from dataclasses import dataclass
from pathlib import Path
import base64,binascii,hashlib,json,os,re,struct,subprocess,uuid,zipfile
from datetime import datetime,timezone
from mouse_fusion_studio.project_io import atomic_json

BASE=0x10000000
LIMIT=524288
SELECTION=['--vid','0x2e8a','--pid','0x0003']
def checksum(data):return hashlib.sha256(data).hexdigest()
def board_id(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9A-Fa-f]{16}',value):raise ValueError('Flash板号须为16位十六进制')
    return value.upper()
def serial_number(value):
    if isinstance(value,str):
        if re.fullmatch('MF-USBM-[0-9]{6}',value) and not value.endswith('000000'):return value
        if re.fullmatch('M[A-Z2-7]{13}',value):
            raw=base64.b32decode(value[1:]+'===')
            if len(raw)==8 and base64.b32encode(raw).decode().rstrip('=')==value[1:]:return value
    raise ValueError('USB串号格式不正确')
def serial_for_board(uid):
    uid=board_id(uid)
    # All 64 bits fit in 13 uppercase Base32 characters; Windows identities ignore case.
    return 'M'+base64.b32encode(bytes.fromhex(uid)).decode().rstrip('=')
def validate_binary(data):
    if not 512<=len(data)<=LIMIT:raise ValueError('固件必须位于512KiB程序区')
    def rev(v,n):return int(f'{v:0{n}b}'[::-1],2)
    crc=rev((binascii.crc32(bytes(rev(b,8) for b in data[:252]))^0xffffffff)&0xffffffff,32)
    if struct.unpack_from('<I',data,252)[0]!=crc:raise ValueError('boot2 CRC不正确')
    stack,reset=struct.unpack_from('<II',data,256)
    if not 0x20000000<stack<=0x20042000 or stack%8 or not reset&1 or not BASE<=reset&~1<BASE+len(data):raise ValueError('固件启动向量不正确')
def make_uf2(data):
    validate_binary(data);total=(len(data)+255)//256;out=bytearray()
    for i in range(total):
        out+=struct.pack('<8I',0x0a324655,0x9e5d5157,0x2000,BASE+i*256,256,i,total,0xe48bff56)
        out+=data[i*256:(i+1)*256].ljust(256,b'\0')+bytes(220)+struct.pack('<I',0x0ab16f30)
    return bytes(out)

@dataclass(frozen=True)
class FirmwarePackage:
    binary:bytes
    manifest:dict
    @property
    def serial_offset(self):return self.manifest['serial_offset']
    @classmethod
    def load(cls,path):
        with zipfile.ZipFile(path) as z:
            if set(z.namelist())!={'manifest.json','firmware.bin'}:raise ValueError('不是受支持的Mouse Fusion固件包')
            if z.getinfo('manifest.json').file_size>65536 or z.getinfo('firmware.bin').file_size>LIMIT:raise ValueError('固件包过大')
            m=json.loads(z.read('manifest.json'));b=z.read('firmware.bin')
        if m.get('format')!='mouse-fusion-firmware' or m.get('version')!=1 or m.get('board_family')!='C4-L4-RP2040-2MB':raise ValueError('固件板型不匹配')
        if checksum(b)!=m.get('binary_sha256'):raise ValueError('固件文件校验失败')
        if not isinstance(m.get('firmware'),str) or not re.fullmatch('[A-Za-z0-9.-]{1,24}',m['firmware']):raise ValueError('固件版本字段不正确')
        validate_binary(b)
        offset=m.get('serial_offset')
        if type(offset) is not int or not 256<=offset<=len(b)-15 or m.get('serial_bytes')!=15:raise ValueError('串号字段位置不正确')
        serial=serial_number(m.get('base_serial'))
        if b[offset:offset+15]!=serial.encode('ascii')+b'\0':raise ValueError('串号字段与固件不匹配')
        return cls(b,m)
    def personalize(self,serial):
        value=serial_number(serial).encode('ascii')+b'\0';o=self.serial_offset
        result=self.binary[:o]+value+self.binary[o+15:];validate_binary(result);return result

class BoardRegistry:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
    @contextmanager
    def _lock(self):
        with self.path.with_suffix('.lock').open('a+b') as f:
            if not f.tell():f.write(b'0');f.flush()
            f.seek(0)
            if os.name=='nt':
                import msvcrt
                try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
                except OSError as e:raise ValueError('另一个工具正在修改板子记录，请稍后重试') from e
                try:yield
                finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(f,fcntl.LOCK_EX)
                try:yield
                finally:fcntl.flock(f,fcntl.LOCK_UN)
    def _read(self):
        if not self.path.exists():return dict(version=2,boards={})
        data=json.loads(self.path.read_text(encoding='utf8'))
        if not isinstance(data,dict) or data.get('version') not in (1,2) or not isinstance(data.get('boards'),dict):raise ValueError('板子记录格式不正确；请保留原文件后检查')
        numbers=[]
        for uid,record in data['boards'].items():
            if board_id(uid)!=uid:raise ValueError('记录板号须使用规范大写格式')
            if not isinstance(record,dict) or record.get('serial')!=serial_for_board(uid):raise ValueError('板子记录串号与唯一板号不匹配；请保留原文件后检查')
            numbers.append(serial_number(record['serial']))
        if len(set(numbers))!=len(numbers):raise ValueError('记录存在重复USB串号')
        data['version']=2;data.pop('next_number',None)
        return data
    def records(self):
        with self._lock():return self._read()['boards']
    def reserve(self,uid):
        uid=board_id(uid)
        with self._lock():
            data=self._read()
            if uid in data['boards']:return data['boards'][uid]['serial']
            serial=serial_for_board(uid)
            data['boards'][uid]=dict(serial=serial,result='RESERVED');atomic_json(self.path,data);return serial
    def update(self,uid,**record):
        with self._lock():
            data=self._read();data['boards'][board_id(uid)].update(record);atomic_json(self.path,data)

@dataclass(frozen=True)
class Target:
    rom_serial:str
    flash_id:str=''
    status:str='OK'
@dataclass(frozen=True)
class CommandResult:
    exit_code:int
    output:str

class NativeBackend:
    def __init__(self,picotool):self.picotool=Path(picotool);self.connection=None
    def _connect(self):
        from .usb_connection import UsbConnection
        self.connection=UsbConnection(self.picotool.parent/'libusb-1.0.dll').open()
    @contextmanager
    def session(self):
        if self.connection:raise ValueError('正在操作USB连接，请等待')
        try:self._connect();yield
        finally:
            if self.connection:self.connection.close();self.connection=None
    def reconnect_after_probe(self):
        if self.connection:self.connection.close();self.connection=None
        self._connect()
    @staticmethod
    def _run(args):
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        p=subprocess.run([str(a) for a in args],capture_output=True,text=True,encoding='utf8',errors='replace',timeout=30,creationflags=flags)
        return CommandResult(p.returncode,p.stdout+p.stderr)
    def devices(self):
        command=r"@(Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match '^USB\\VID_2E8A&PID_0003\\' } | Select-Object Status,InstanceId) | ConvertTo-Json -Compress"
        result=self._run(['powershell.exe','-NoProfile','-Command',command])
        if result.exit_code:raise ValueError('无法读取BOOTSEL设备：'+result.output)
        data=json.loads(result.output or '[]');data=data if isinstance(data,list) else [data]
        return [Target(d['InstanceId'].rsplit('\\',1)[-1],status=d['Status']) for d in data]
    def command(self,args):
        if not self.picotool.is_file():raise ValueError('picotool不存在，请完整解压软件文件夹')
        select=[]
        if self.connection:self.connection.check();select=self.connection.selection()
        result=self._run([self.picotool,*args,*select])
        probe_reboot=args[0]=='load' and '-x' in args and '-t' in args and args[args.index('-t')+1]=='elf'
        if self.connection and not probe_reboot:self.connection.check()
        return result
    def probe_identity(self,target,folder,progress):
        from .flash_probe import run_probe
        return run_probe(self,target,folder,progress)

def parse_identity(result):
    if result.exit_code:raise ValueError('BOOTSEL通信失败；未写入：'+result.output.strip())
    if not re.search(r'(?im)^\s*type:\s*RP2040\s*$',result.output):raise ValueError('未识别为RP2040；未写入')
    if not re.search(r'(?im)^\s*flash size:\s*2048K\s*$',result.output):raise ValueError('Flash容量未确认是2MB；未写入')
    ids=re.findall(r'(?im)^\s*flash id:\s*0x([0-9a-f]{16})\s*$',result.output)
    if len(ids)!=1:raise ValueError('无法确定唯一Flash板号；未写入')
    return ids[0].upper()

class Flasher:
    def __init__(self,backend,registry,logs,progress=None):self.backend=backend;self.registry=registry;self.logs=Path(logs);self.progress=progress or (lambda s:None)
    def _rom(self,expected=None):
        devices=self.backend.devices()
        if len(devices)!=1 or devices[0].status!='OK' or not re.fullmatch('[0-9A-Fa-f]{12}',devices[0].rom_serial):raise ValueError('请只连接一块健康的BOOTSEL板子')
        target=devices[0]
        if expected and target.rom_serial.upper()!=expected.rom_serial.upper():raise ValueError('目标板已改变，请重新检测；未继续写入')
        return target
    def _identity(self,target):
        folder=self.logs/(datetime.now(timezone.utc).strftime('identity_%Y%m%dT%H%M%SZ_')+uuid.uuid4().hex[:8]);folder.mkdir(parents=True)
        report=dict(rom_serial=target.rom_serial,result='FAILED_OR_UNKNOWN',flash_write=False)
        try:
            result=self.backend.command(['info','-d',*SELECTION])
            report.update(exit_code=result.exit_code,output=result.output)
            blank=(not result.exit_code and re.search(r'(?im)^\s*type:\s*RP2040\s*$',result.output)
                   and not re.search(r'(?im)^\s*flash (size|id):',result.output))
            if blank:
                self._rom(target)
                if not hasattr(self.backend,'probe_identity'):raise ValueError('Flash身份缺失，当前工具不支持空白新板；未写入')
                uid=board_id(self.backend.probe_identity(target,folder,self.progress));report['source']='RAM_READ_ONLY'
            else:uid=parse_identity(result);report['source']='PICOTOOL_INFO'
            report.update(result='IDENTITY_PASS',flash_id=uid);return uid
        except Exception as e:report['error']=str(e);raise
        finally:atomic_json(folder/'identity.json',report)
    def detect(self):
        with (self.backend.session() if hasattr(self.backend,'session') else nullcontext()):return self._detect()
    def _detect(self):
        target=self._rom();self.progress('读取板子身份…');uid=self._identity(target)
        self._rom(target);return Target(target.rom_serial,uid)
    def flash(self,target,package,*,authorized=False,j1_only=False):
        if authorized is not True or j1_only is not True:raise ValueError('必须明确确认板型、仅J1连接并手动开始烧录')
        if not target.flash_id:raise ValueError('先检测并核对板子身份')
        with (self.backend.session() if hasattr(self.backend,'session') else nullcontext()):return self._flash(target,package)
    def _flash(self,target,package):
        self._rom(target);self.progress('再次核对目标板…')
        uid=self._identity(target)
        if uid!=board_id(target.flash_id):raise ValueError('Flash板号已改变；未写入')
        serial=self.registry.reserve(uid);binary=package.personalize(serial)
        folder=self.logs/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uid+'_'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True)
        bin_path=folder/'firmware.bin';uf2_path=folder/'firmware.uf2';bin_path.write_bytes(binary);uf2_path.write_bytes(make_uf2(binary))
        report=dict(rom_serial=target.rom_serial,flash_id=uid,usb_serial=serial,firmware=package.manifest['firmware'],bin_sha256=checksum(binary),bin_bytes=len(binary),
                    result='FAILED_OR_UNKNOWN',automatic_retry=False,automatic_reboot=False,commands=[])
        atomic_json(folder/'result.json',report);self.registry.update(uid,result='IN_PROGRESS',firmware=report['firmware'],last_log=str(folder))
        try:
            for phase,args in [('load',['load','-v',str(uf2_path),'-t','uf2',*SELECTION]),('verify',['verify',str(bin_path),'-t','bin','-o','0x10000000',*SELECTION])]:
                self._rom(target);self.progress('正在写入固件…' if phase=='load' else '正在独立读回校验…')
                result=self.backend.command(args);report['commands'].append(dict(phase=phase,args=args,exit_code=result.exit_code,output=result.output))
                (folder/(phase+'.log')).write_text(result.output,encoding='utf8')
                if result.exit_code or 'Verifying Flash' not in result.output or not re.search(r'\bOK\b',result.output) or re.search(r'(Loading into|Verifying) RAM',result.output) or phase=='load' and 'Loading into Flash' not in result.output:raise ValueError(f'{phase}未确认成功，已停止；不自动重试或重启')
            report['result']='FLASH_LOAD_INDEPENDENT_VERIFY_PASS';return report
        except Exception as e:report['error']=str(e);raise
        finally:
            atomic_json(folder/'result.json',report)
            self.registry.update(uid,result=report['result'],firmware=report['firmware'],last_log=str(folder),updated_utc=datetime.now(timezone.utc).isoformat())
