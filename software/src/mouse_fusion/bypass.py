"""Capability-gated schema-1 global BYPASS; independent of MFT1/slot IDs."""
import struct

CAP_BYPASS=0x1000
SET_BYPASS,GET_BYPASS=0x2e,0x2f
BUTTONS=(None,'left','right','middle','side1','side2')
MASKS=(0,1,2,4,8,16)
STATUS=struct.Struct('<8BII')

def normalize_config(config):
    if not isinstance(config,dict) or set(config)-{'button','behavior'}:
        raise ValueError('BYPASS accepts button and fixed hold behavior only')
    button=config.get('button')
    if button not in BUTTONS or config.get('behavior','hold')!='hold':
        raise ValueError('BYPASS requires one mouse button (or None) and hold behavior')
    return dict(button=button,behavior='hold')

def encode_config(config):
    return bytes([1,MASKS[BUTTONS.index(normalize_config(config)['button'])]])

def decode_status(body):
    if len(body)!=STATUS.size:raise ValueError('Wrong BYPASS status size')
    v=STATUS.unpack(body)
    if v[0]!=1 or v[1] not in MASKS or any(x not in (0,1) for x in v[2:6]+v[7:8]) or v[6]>31 or not v[8]:
        raise ValueError('Unsupported or invalid BYPASS status')
    return dict(schema=1,button=BUTTONS[MASKS.index(v[1])],behavior='hold',mask=v[1],
                active=bool(v[2]),inhibited=bool(v[3]),input_valid=bool(v[4]),rearm=bool(v[5]),
                raw_buttons=v[6],submitted_auxiliary_inflight=bool(v[7]),generation=v[8],entries=v[9])
