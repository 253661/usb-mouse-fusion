"""Editor projects, executable RAM images and simulated persistence stay distinct."""
from dataclasses import asdict
import hashlib
import json
from mouse_fusion.trajectory import from_compiled, inspect_image
from .compiler import compile_mode, export_data,round_delta
from mouse_fusion.run_policy import normalize_policy

def executable_mode(mode):
    data=export_data(compile_mode(mode),mode)
    image=from_compiled(data,format_version=2,target_ms=10,perturbation=mode.perturbation)
    return image,inspect_image(image)

def prepare_project(project):
    project.validate()
    if len(project.modes)>8:raise ValueError('MCU 支持 8 个固定 RAM 模式；请使用至多 8 个模式的工程')
    images=[];bindings=[];mapping={}
    for slot,mode in enumerate(project.modes):
        mapping[mode.mode_id]=slot
        bindings.extend(asdict(trigger) for trigger in mode.triggers)
        if not mode.points:continue
        image,info=executable_mode(mode)
        if not info['fits_ram_slot']:raise ValueError(f'{mode.name} 可执行数据 {len(image)} 字节，超出 4096 字节 RAM 槽；请缩短轨迹或减少形状变化')
        images.append((slot,image))
    return images,bindings,mapping

def execution_signature(project):
    # Image pose and view state stay local; perturbation is executable MFT2 metadata.
    data=[dict(mode_id=m.mode_id,points=[asdict(p) for p in m.points],
               triggers=[asdict(t) for t in m.triggers if t.device=='mouse'],run_policy=m.run_policy,perturbation=m.perturbation) for m in project.modes]
    return hashlib.sha256(json.dumps(dict(modes=data,bypass=project.bypass,bypass_schema_version=1),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def project_run_policies(project):
    policies={}
    for slot,mode in enumerate(project.modes):
        rule=normalize_policy(mode.run_policy)
        if mode.points:
            rule.update(origin_x=round_delta(mode.points[0].x),origin_y=-round_delta(mode.points[0].y))
        policies[slot]=rule
    return policies
