"""MFT1 executable data: exact 1ms signed16 HID deltas, independent of Studio JSON."""
import struct
import zlib

TRIGGER_RECORD=struct.Struct('<6BHH2B')
def encode_triggers(bindings,mode_slots):
    """APP trigger schemas 1..3 -> bounded records. Keyboard entries remain local."""
    if not isinstance(bindings,list) or len(bindings)>64:raise ValueError('bindings must contain at most 64 APP trigger objects')
    if not isinstance(mode_slots,dict):raise ValueError('mode_slots must map APP mode IDs to RAM slots')
    buttons=('left','right','middle','side1','side2','wheel_up','wheel_down');gestures=('click','hold','chord')
    actions=('switch_mode','next_mode','previous_mode','start_trajectory','stop_trajectory','toggle_mode','start_current_trajectory','start_mode_trajectory');behaviors=('once','hold','toggle')
    records=[];seen=[]
    for b in bindings:
        if not isinstance(b,dict):raise ValueError('trigger must be an APP schema object')
        if b.get('device','mouse')=='keyboard':continue
        if b.get('device','mouse')!='mouse':raise ValueError('invalid trigger device')
        if b.get('button_policy','passthrough')!='passthrough':raise ValueError('suppress_requested is NOT_SUPPORTED by MCU')
        keys=b.get('buttons',[])
        if not isinstance(keys,list) or not 1<=len(keys)<=6 or len(set(keys))!=len(keys) or any(k not in buttons for k in keys):raise ValueError('invalid mouse buttons')
        try:
            mask=sum(1<<buttons.index(k) for k in keys);gesture=gestures.index(b.get('gesture','chord'))
            action=actions.index(b.get('action','next_mode'));behavior=behaviors.index(b.get('behavior','once'))
        except ValueError as e:raise ValueError('invalid trigger gesture/action/behavior') from e
        target=mode_slots.get(b.get('target_mode'),0)
        if action in (0,3,4,5,7) and b.get('target_mode') not in mode_slots:raise ValueError('target mode is not mapped to a loaded RAM slot')
        if type(target) is not int or not 0<=target<8:raise ValueError('target slot must be 0..7')
        window,hold,debounce=(b.get(k,d) for k,d in [('window_ms',200),('hold_ms',400),('debounce_ms',12)])
        if any(type(v) is not int for v in (window,hold,debounce)) or not 10<=window<=2000 or not 50<=hold<=5000 or not 1<=debounce<=50 or debounce>window or debounce>=hold:raise ValueError('invalid trigger timings')
        if behavior==1 and (gesture==0 or action not in (3,6,7)) or behavior==2 and action!=3:raise ValueError('invalid trigger execution behavior')
        if mask&96 and (mask&96==96 or gesture==1 or behavior):raise ValueError('wheel is an instantaneous event; use one direction and click/chord once')
        enabled=b.get('enabled',True)
        if type(enabled) is not bool:raise ValueError('enabled must be boolean')
        if enabled:
            for oldmask,oldgesture,oldaction,oldtarget in seen:
                if mask!=oldmask or (gesture!=oldgesture and not mask&96):continue
                if action==oldaction==7 and target!=oldtarget:continue
                if {action,oldaction}=={6,7}:continue
                raise ValueError('duplicate mouse combo/gesture')
            seen.append((mask,gesture,action,target))
        records.append(TRIGGER_RECORD.pack(mask,gesture,action,target,behavior,0,window,hold,debounce,int(enabled)))
    if len(records)>16:raise ValueError('MCU supports at most 16 mouse triggers')
    return b''.join(records)

HEADER=struct.Struct('<4sHHIIIIII')
BLOCK_SAMPLES=256
MAX_SAMPLES=300000
MAX_RAM_IMAGE=4096
MODE_SLOTS=8
CAP_KEYPOINTS=0x4000
CAP_TRAJECTORY=0x100
CAP_TRIGGER=0x200
CAP_TRIGGER_EXTENDED=0x8000
CAP_MODIFIER_SELECTORS=0x10000

def requires_modifier_selectors(wire):
    """The same wire records need newer recognition semantics, even if disabled."""
    return any(wire[i+2]==5 and (wire[i]&31 in (8,16) and wire[i]&96 or
               wire[i] in (10,18) and wire[i+1]==0) for i in range(0,len(wire),12))
CAP_PRESET_STORAGE=0x400
PRESET_CAPS,UPLOAD_BEGIN,UPLOAD_CHUNK,UPLOAD_COMMIT,PRESET_CONTROL,PRESET_STATUS,TRIGGER_SET,PRESET_SAVE,PRESET_RESTORE=range(0x20,0x29)


def _count(value):
    if type(value) is not int or not 1<=value<=MAX_SAMPLES: raise ValueError('sample count must be 1..300000')
    return value


def worst_case_size(samples):
    n=_count(samples); blocks=(n+255)//256
    return HEADER.size+4*(blocks+1)+blocks+4*n


def _block(values):
    count=len(values)
    choices=[]
    if all(value==(0,0) for value in values): choices.append(b'\0')
    if all(value==values[0] for value in values): choices.append(b'\1'+struct.pack('<hh',*values[0]))
    compact=all(-128<=v<=127 for xy in values for v in xy)
    choices.append(bytes([2 if compact else 3])+b''.join(struct.pack('<bb' if compact else '<hh',*v) for v in values))
    runs=[]; i=0
    while i<count:
        j=i+1
        while j<count and values[j]==values[i]: j+=1
        runs.append(struct.pack('<Bbb' if compact else '<Bhh',j-i-1,*values[i])); i=j
    choices.append(bytes([4 if compact else 5])+b''.join(runs))
    return min(choices,key=len)


def encode_motion(motion):
    """Input is already quantized HID (X right/Y down), one delta per 1ms tick."""
    values=list(motion); n=_count(len(values))
    for xy in values:
        if not isinstance(xy,(tuple,list)) or len(xy)!=2 or any(type(v) is not int or not -32768<=v<=32767 for v in xy):
            raise ValueError('motion requires signed16 integer dx/dy')
    values=[tuple(xy) for xy in values]
    parts=[_block(values[i:i+256]) for i in range(0,n,256)]
    offsets=[0]
    for part in parts: offsets.append(offsets[-1]+len(part))
    image=bytearray(HEADER.pack(b'MFT1',1,HEADER.size,1000,n,len(parts),offsets[-1],1,0))
    image.extend(struct.pack('<'+'I'*len(offsets),*offsets)); image.extend(b''.join(parts))
    struct.pack_into('<I',image,28,zlib.crc32(image))
    return bytes(image)


def _decode_block(data,count):
    if not data: raise ValueError('empty block')
    code=data[0]; body=data[1:]
    if code==0:
        if body: raise ValueError('zero block size')
        return [(0,0)]*count
    if code==1:
        if len(body)!=4: raise ValueError('constant block size')
        return [struct.unpack('<hh',body)]*count
    if code in (2,3):
        width=2 if code==2 else 4
        if len(body)!=width*count: raise ValueError('literal block size')
        return list(struct.iter_unpack('<bb' if code==2 else '<hh',body))
    if code in (4,5):
        width=3 if code==4 else 5
        if not body or len(body)%width: raise ValueError('RLE block size')
        values=[]
        for run,dx,dy in struct.iter_unpack('<Bbb' if code==4 else '<Bhh',body):
            if len(values)+run+1>count: raise ValueError('RLE sample overflow')
            values.extend([(dx,dy)]*(run+1))
        if len(values)!=count: raise ValueError('RLE sample count')
        return values
    raise ValueError('unknown block codec')


def decode_motion(image):
    image=bytes(image)
    if image[:4]==b'MFT2':return _decode_keypoint_motion(image)
    if len(image)<HEADER.size: raise ValueError('short executable header')
    magic,version,header,tick,n,blocks,body_size,flags,crc=HEADER.unpack_from(image)
    _count(n)
    if (magic,version,header,tick,flags)!=(b'MFT1',1,32,1000,1) or blocks!=(n+255)//256:
        raise ValueError('unsupported executable format')
    start=HEADER.size+4*(blocks+1)
    if len(image)!=start+body_size: raise ValueError('executable length mismatch')
    if zlib.crc32(image[:28]+b'\0'*4+image[32:])!=crc: raise ValueError('executable CRC mismatch')
    offsets=struct.unpack_from('<'+'I'*(blocks+1),image,HEADER.size)
    if offsets[0]!=0 or offsets[-1]!=body_size or any(a>=b for a,b in zip(offsets,offsets[1:])):
        raise ValueError('invalid executable block index')
    values=[]
    for i,(a,b) in enumerate(zip(offsets,offsets[1:])):
        values.extend(_decode_block(image[start+a:start+b],min(256,n-i*256)))
    return values


def inspect_image(image):
    decode_motion(image)
    _,_,_,tick,n,blocks,_,_,crc=HEADER.unpack_from(image)
    version=2 if image[:4]==b'MFT2' else 1
    return dict(format=f'MFT{version}',version=version,tick_us=tick,sample_count=n,duration_ms=n,
                blocks=blocks,encoded_bytes=len(image),crc32=f'{crc:08x}',
                fits_ram_slot=len(image)<=MAX_RAM_IMAGE,ram_slot_bytes=MAX_RAM_IMAGE,
                **(dict(keypoint_count=blocks,sampling_target_ms=struct.unpack_from('<H',image,48)[0]) if version==2 else {}),
                units='relative HID counts: X right, Y down',hardware_verified=False)


def from_compiled(data,*,format_version=1,target_ms=10,perturbation=None):
    """Adapt existing world-Y-up Studio samples; no re-quantization or resampling."""
    if not isinstance(data,dict) or data.get('data_version')!=1: raise ValueError('compiled data version must be 1')
    execution=data.get('execution',{})
    records=execution.get('records')
    if execution.get('sample_target_ms')!=1 or not isinstance(records,list): raise ValueError('MFT1 requires 1ms compiled records')
    motion=[]
    for row in records:
        if not isinstance(row,list) or len(row)!=3 or type(row[0]) is not int or row[0]!=1:
            raise ValueError('every executable record must span exactly 1ms')
        dx,dy=row[1:]
        if type(dx) is not int or type(dy) is not int: raise ValueError('compiled increments must be integers')
        motion.append((dx,-dy))
    if execution.get('duration_ms')!=len(motion): raise ValueError('compiled duration mismatch')
    if format_version==1:return encode_motion(motion)
    if format_version==2:return keypoints_from_motion(motion,target_ms=target_ms,perturbation=perturbation)
    raise ValueError('unsupported executable format version')


KEYPOINT=struct.Struct('<Iii')
PERTURB=struct.Struct('<BBBBHHHHI')

def _round_ratio(value,divisor):
    return -((-value+divisor//2)//divisor) if value<0 else (value+divisor//2)//divisor

def _position(a,b,t):
    span=b[0]-a[0]
    return tuple(a[axis]+_round_ratio((b[axis]-a[axis])*(t-a[0]),span) for axis in (1,2))

def _perturb_wire(config):
    from .perturbation import normalize_config
    config=normalize_config(config)
    return PERTURB.pack(1,int(config['enabled']),int(config['fixed_seed_enabled']),config['smoothness'],
                        config['amplitude_x'],config['amplitude_y'],config['period_ms'],config['max_deviation'],config['seed'])

def _keypoints(image):
    if len(image)<88:raise ValueError('short keypoint executable header')
    magic,version,header,tick,duration,count,body_size,flags,crc=HEADER.unpack_from(image)
    _count(duration)
    if (magic,version,header,tick,flags)!=(b'MFT2',2,64,1000,1) or count<2 or count>MAX_SAMPLES+1 or body_size!=count*12 or len(image)!=64+body_size:
        raise ValueError('unsupported keypoint executable format')
    if zlib.crc32(image[:28]+b'\0'*4+image[32:])!=crc:raise ValueError('executable CRC mismatch')
    schema,enabled,fixed,smoothness,ax,ay,period,deviation,seed=PERTURB.unpack_from(image,32)
    if schema!=1 or enabled>1 or fixed>1:raise ValueError('invalid keypoint perturbation schema')
    _perturb_wire(dict(enabled=bool(enabled),fixed_seed_enabled=bool(fixed),smoothness=smoothness,
                       amplitude_x=ax,amplitude_y=ay,period_ms=period,max_deviation=deviation,seed=seed))
    if struct.unpack_from('<H',image,48)[0] not in (5,10) or any(image[50:64]):raise ValueError('invalid keypoint sampling metadata')
    points=list(KEYPOINT.iter_unpack(image[64:]))
    if points[0]!=(0,0,0) or points[-1][0]!=duration:raise ValueError('keypoint origin or endpoint time mismatch')
    for a,b in zip(points,points[1:]):
        span=b[0]-a[0]
        if span<=0 or b[0]>duration:raise ValueError('keypoint times must increase')
        for axis in (1,2):
            delta=b[axis]-a[axis]
            if (abs(delta)+span-1)//span>(32768 if delta<0 else 32767):raise ValueError('keypoint per-tick delta exceeds signed16')
    return points

def encode_keypoints(points,*,target_ms=10,perturbation=None):
    """Fixed MFT2: cumulative HID counts at absolute integer ms, MCU linear interpolation."""
    points=list(points)
    if target_ms not in (5,10) or type(target_ms) is not int:raise ValueError('keypoint target must be 5 or 10ms')
    if len(points)<2:raise ValueError('at least two keypoints required')
    if any(not isinstance(p,(tuple,list)) or len(p)!=3 or any(type(v) is not int for v in p) or
           not 0<=p[0]<=MAX_SAMPLES or any(not -(1<<31)<=v<(1<<31) for v in p[1:]) for p in points):
        raise ValueError('keypoints require integer time and signed32 positions')
    image=bytearray(HEADER.pack(b'MFT2',2,64,1000,points[-1][0],len(points),len(points)*12,1,0))
    image.extend(_perturb_wire(perturbation));image.extend(struct.pack('<H',target_ms));image.extend(b'\0'*14)
    image.extend(b''.join(KEYPOINT.pack(*p) for p in points));struct.pack_into('<I',image,28,zlib.crc32(image))
    _keypoints(image)
    return bytes(image)

def _decode_keypoint_motion(image):
    points=_keypoints(image);motion=[]
    for a,b in zip(points,points[1:]):
        previous=a[1:]
        for t in range(a[0]+1,b[0]+1):
            position=_position(a,b,t);motion.append(tuple(x-y for x,y in zip(position,previous)));previous=position
    return motion

def keypoints_from_motion(motion,*,target_ms=10,max_error=1,perturbation=None):
    """Use 5/10ms points, splitting a span when cumulative path error exceeds one count."""
    values=list(motion);_count(len(values))
    if type(max_error) is not int or not 0<=max_error<=1:raise ValueError('keypoint error must be 0 or 1 count')
    if target_ms not in (5,10):raise ValueError('keypoint target must be 5 or 10ms')
    cumulative=[(0,0,0)];x=y=0
    for t,xy in enumerate(values,1):
        if not isinstance(xy,(tuple,list)) or len(xy)!=2 or any(type(v) is not int or not -32768<=v<=32767 for v in xy):raise ValueError('motion requires signed16 integer dx/dy')
        x+=xy[0];y+=xy[1];cumulative.append((t,x,y))
    points=[cumulative[0]]
    def split(start,end):
        a,b=cumulative[start],cumulative[end];worst=max_error;at=None
        for t in range(start+1,end):
            p=_position(a,b,t);error=max(abs(p[i]-cumulative[t][i+1]) for i in (0,1))
            if error>worst:worst=error;at=t
        if at is None:points.append(b)
        else:split(start,at);split(at,end)
    for start in range(0,len(values),target_ms):split(start,min(len(values),start+target_ms))
    return encode_keypoints(points,target_ms=target_ms,perturbation=perturbation)
