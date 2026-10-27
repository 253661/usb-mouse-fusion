"""Local planner v1. Not a firmware upload protocol or realtime scheduler."""
from dataclasses import dataclass
import json
import math
import zlib


def segment_handles(points, i):
    p, q = points[i], points[i+1]
    if p.curve == 'smooth':
        prev, nxt = points[max(0,i-1)], points[min(len(points)-1,i+2)]
        return ((p.x+(q.x-prev.x)/6, p.y+(q.y-prev.y)/6),
                (q.x-(nxt.x-p.x)/6, q.y-(nxt.y-p.y)/6))
    return (p.handle_out or (p.x+(q.x-p.x)/3,p.y+(q.y-p.y)/3),
            q.handle_in or (p.x+2*(q.x-p.x)/3,p.y+2*(q.y-p.y)/3))


def ease(t, easing):
    if easing == 'ease_in': return t*t
    if easing == 'ease_out': return 1-(1-t)**2
    if easing == 'ease_in_out': return t*t*(3-2*t)
    return t


def curve_position(points, i, t, *, timed=False):
    p, q = points[i], points[i+1]
    if timed: t = ease(t,p.easing)
    if p.curve == 'linear': return p.x+(q.x-p.x)*t, p.y+(q.y-p.y)*t
    a,b = segment_handles(points,i); u=1-t
    return (u**3*p.x+3*u*u*t*a[0]+3*u*t*t*b[0]+t**3*q.x,
            u**3*p.y+3*u*u*t*a[1]+3*u*t*t*b[1]+t**3*q.y)


def round_delta(value): return math.floor(value+.5) if value >= 0 else math.ceil(value-.5)


@dataclass(frozen=True)
class Sample:
    t_ms: int
    x: float
    y: float
    qx: int
    qy: int
    dx: int
    dy: int
    speed: float


@dataclass
class Compiled:
    samples: list
    preview: list
    step_ms: int
    duration_ms: int


def compile_mode(mode, step_ms=1):
    mode.validate()
    if type(step_ms) is not int or not 1 <= step_ms <= 100: raise ValueError('采样间隔须为 1..100ms')
    if len(mode.points) < 2: raise ValueError('至少需要两个点才能编译')
    origin = mode.points[0]
    samples = [Sample(0,origin.x,origin.y,0,0,0,0,0)]
    elapsed = 0
    for i,p in enumerate(mode.points[:-1]):
        times = list(range(step_ms,p.duration_ms,step_ms)) + [p.duration_ms]
        for t in times:
            x,y = curve_position(mode.points,i,t/p.duration_ms,timed=True)
            qx,qy = round_delta(x-origin.x),round_delta(y-origin.y)
            previous = samples[-1]; dx,dy = qx-previous.qx,qy-previous.qy
            if not -32768 <= dx <= 32767 or not -32768 <= dy <= 32767:
                raise ValueError(f'{elapsed+t}ms 的整数位移超出 signed16 范围；请增加时长')
            dt = elapsed+t-previous.t_ms
            speed = math.hypot(x-previous.x,y-previous.y)*1000/dt
            samples.append(Sample(elapsed+t,x,y,qx,qy,dx,dy,speed))
        elapsed += p.duration_ms
    preview = []
    if 'amplitude' in mode.perturbation:
        amp,freq,seed = (mode.perturbation[k] for k in ('amplitude','frequency_hz','seed'))
        phase = seed * 2.3999632297
        for s in samples:
            gate = math.sin(math.pi*s.t_ms/elapsed)**2 if 0 < s.t_ms < elapsed else 0
            a = 2*math.pi*freq*s.t_ms/1000
            preview.append((s.x+amp*gate*math.sin(a+phase), s.y+amp*gate*math.sin(a*.73+phase+1.1)))
    else:
        from mouse_fusion.perturbation import normalize_perturbation,perturb_offset
        import secrets
        config=normalize_perturbation(mode.perturbation)
        seed=config['seed'] if config['fixed_seed_enabled'] else secrets.randbits(32)
        for s in samples:
            x,y=perturb_offset(config,seed,s.t_ms,elapsed)
            preview.append((s.x+x,s.y-y))
    return Compiled(samples,preview,step_ms,elapsed)


def export_data(compiled, mode):
    execution = dict(mode_name=mode.name, sample_target_ms=compiled.step_ms,
                     duration_ms=compiled.duration_ms, origin=[mode.points[0].x,mode.points[0].y],
                     records=[[s.t_ms-compiled.samples[i-1].t_ms,s.dx,s.dy]
                              for i,s in enumerate(compiled.samples) if i])
    wire = json.dumps(execution,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
    return dict(format='mouse-fusion-compiled',data_version=1,compiler_version=1,
                execution=execution,checksum_crc32=f'{zlib.crc32(wire):08x}',
                estimated_bytes=32+6*(len(compiled.samples)-1),
                estimate_layout='APP proposal: 32-byte header + uint16 dt/int16 dx/int16 dy',
                device_storage_confirmed=False,hardware_verified=False,
                preview_perturbation_exported=False)
