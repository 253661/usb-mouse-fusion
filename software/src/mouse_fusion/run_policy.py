"""Schema-1 run rules, separate from the unchanged MFT1 executable image."""
import struct

CAP_RUN_POLICY=0x800
SET_RULE,GET_RULE,EXEC_CONTROL,RUN_STATUS=range(0x2a,0x2e)
WIRE=struct.Struct('<4BIIHH4Bii')
STATUS=struct.Struct('<7B6Iqq')
ENUMS=dict(run_mode=('once','loop','count','duration','hold'),stop_action=('keep','return'),
           loop_join=('return','continue'),return_easing=('linear','ease_in','ease_out','smoothstep'),
           transition_action=('immediate','smooth'),transition_easing=('linear','ease_in','ease_out','smoothstep'))
DEFAULT_POLICY=dict(run_mode='once',stop_action='keep',loop_join='return',return_easing='smoothstep',
                    repeat_count=1,duration_ms=1000,return_ms=250,transition_ms=250,
                    transition_action='smooth',transition_easing='smoothstep',origin_x=0,origin_y=0)
PHASES=('idle','path','waiting','return','transition')
REASONS=('none','finished','stopped','returned','return_incomplete','target_reset','range_error','preset_changed','bypass','input_reset')

def normalize_policy(policy):
    if not isinstance(policy,dict) or set(policy)-set(DEFAULT_POLICY):raise ValueError('Unknown run-policy fields')
    value={**DEFAULT_POLICY,**policy}
    for key,options in ENUMS.items():
        if value[key] not in options:raise ValueError(f'{key} must be one of {options}')
    for key,low,high in [('repeat_count',1,1000000),('duration_ms',1,86400000),('return_ms',1,60000),
                         ('transition_ms',0,60000),('origin_x',-1000000000,1000000000),('origin_y',-1000000000,1000000000)]:
        if type(value[key]) is not int or not low<=value[key]<=high:raise ValueError(f'{key} must be an integer in {low}..{high}')
    return value

def encode_policy(policy):
    p=normalize_policy(policy)
    enum=lambda key:ENUMS[key].index(p[key])
    return WIRE.pack(enum('run_mode'),enum('stop_action'),enum('loop_join'),enum('return_easing'),
                     p['repeat_count'],p['duration_ms'],p['return_ms'],p['transition_ms'],
                     enum('transition_action'),enum('transition_easing'),1,0,p['origin_x'],p['origin_y'])

def decode_policy(data):
    v=WIRE.unpack(data)
    if v[10:12]!=(1,0):raise ValueError('Unsupported run-policy schema')
    try:
        return normalize_policy(dict(run_mode=ENUMS['run_mode'][v[0]],stop_action=ENUMS['stop_action'][v[1]],
            loop_join=ENUMS['loop_join'][v[2]],return_easing=ENUMS['return_easing'][v[3]],repeat_count=v[4],duration_ms=v[5],
            return_ms=v[6],transition_ms=v[7],transition_action=ENUMS['transition_action'][v[8]],
            transition_easing=ENUMS['transition_easing'][v[9]],origin_x=v[12],origin_y=v[13]))
    except IndexError as error:raise ValueError('Invalid run-policy enum') from error
