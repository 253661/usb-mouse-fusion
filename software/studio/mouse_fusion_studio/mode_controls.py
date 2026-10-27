"""Atomic editor recipe. Shared execution stays separate from per-mode selectors."""
from copy import deepcopy
from .triggers import Trigger,MOUSE_BUTTONS

EIGHT_MODE_SELECTORS=(('side1',),('side2',),('side1','wheel_up'),('side1','wheel_down'),
                      ('side2','wheel_up'),('side2','wheel_down'),('side1','right'),('side2','right'))

def configure_eight_mode_template(project):
    """Replace mode controls atomically; retain geometry, policies and advanced rules."""
    from dataclasses import asdict
    from mouse_fusion.trajectory import encode_triggers
    from .model import Mode
    if len(project.modes)>8:raise ValueError('八模式模板最多用于 8 个模式的工程')
    if project.bypass['button'] in ('left','right','side1','side2'):
        raise ValueError('八模式模板与全局 Bypass 按键冲突；请先关闭该绑定或改用中键。工程未改变。')
    candidate=deepcopy(project)
    while len(candidate.modes)<8:
        candidate.modes.append(Mode(f'轨迹 {len(candidate.modes)+1:02}'))
    state=deepcopy(candidate.editor_state.get('v2_controls',{}))
    owned={tid for config in state.values() if isinstance(config,dict) for tid in config.get('ids',[])}
    if state.get('_shared_id'):owned.add(state['_shared_id'])
    executions={m.mode_id:next((t for owner in candidate.modes for t in owner.triggers if t.device=='mouse' and
        (t.action in ('start_mode_trajectory','start_trajectory') and t.target_mode==m.mode_id or t.action=='start_current_trajectory')),None) for m in candidate.modes}
    for mode in candidate.modes:
        mode.triggers=[t for t in mode.triggers if t.trigger_id not in owned]
    state.pop('_shared_id',None);state['_global_execute']=['left']
    swap=candidate.editor_state.get('side_label_swap',False)
    for mode,keys in zip(candidate.modes,EIGHT_MODE_SELECTORS):
        keys=[('side2' if k=='side1' else 'side1' if k=='side2' else k) if swap else k for k in keys]
        select=Trigger(buttons=keys,gesture='click',action='toggle_mode',target_mode=mode.mode_id)
        previous=executions[mode.mode_id]
        held=mode.run_policy['run_mode']=='hold'
        gesture=previous.gesture if previous else 'chord' if held else 'click'
        behavior=previous.behavior if previous else 'hold' if held else 'once'
        if behavior=='toggle':behavior='once'
        run=Trigger(buttons=['left'],gesture=gesture,action='start_mode_trajectory',target_mode=mode.mode_id,behavior=behavior)
        mode.triggers.extend((select,run))
        state[mode.mode_id]=dict(ids=[select.trigger_id,run.trigger_id],selector=keys,execute=['left'],independent=False,
                                 gesture='click',execute_gesture=gesture,behavior=behavior)
    candidate.editor_state['v2_controls']=state
    candidate.validate()
    bindings=[asdict(t) for m in candidate.modes for t in m.triggers]
    if sum(t['device']=='mouse' for t in bindings)>16:
        raise ValueError('保留现有高级规则后超过设备 16 条绑定容量；请先自行调整高级规则。工程未改变。')
    encode_triggers(bindings,{m.mode_id:i for i,m in enumerate(candidate.modes)})
    # Validation/capacity errors publish nothing. Existing controllers keep mode identities.
    for original,changed in zip(project.modes,candidate.modes):original.triggers=changed.triggers
    project.modes.extend(candidate.modes[len(project.modes):]);project.editor_state=candidate.editor_state

def configure_hold_mode(project,index,mode_button,execute_button,return_ms):
    if mode_button not in MOUSE_BUTTONS or execute_button not in MOUSE_BUTTONS or mode_button==execute_button:
        raise ValueError('模式开关键与执行键必须是两个不同的鼠标键')
    if project.bypass['button'] in (mode_button,execute_button):raise ValueError('所选按键已用于纯物理旁路，请先改用其他键')
    if type(return_ms) is not int or not 1<=return_ms<=60000:raise ValueError('返回时间须为 1..60000 ms')
    candidate=deepcopy(project);mode=candidate.modes[index]
    for m in candidate.modes:
        m.triggers=[b for b in m.triggers if not (b.device=='mouse' and
            (set(b.buttons) in ({mode_button},{execute_button}) or b.action=='start_current_trajectory'))]
    mode.triggers.append(Trigger(buttons=[mode_button],action='toggle_mode',target_mode=mode.mode_id))
    candidate.modes[0].triggers.append(Trigger(buttons=[execute_button],action='start_current_trajectory',behavior='hold'))
    mode.run_policy.update(run_mode='hold',stop_action='return',return_ms=return_ms)
    candidate.validate()
    # Keep existing mode objects/geometry references stable for GUI controllers.
    for original,changed in zip(project.modes,candidate.modes):
        original.triggers=changed.triggers;original.run_policy=changed.run_policy
