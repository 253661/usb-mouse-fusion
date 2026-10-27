"""Current physical left hold, dedicated to AI movement (no button injection)."""
import struct
from .contract import integer
from .errors import FusionError

CAP_AI_GATE=0x40000
GET_AI_GATE=0x16
MOVE_LEFT_HELD=0x17
CAP_AI_SMOOTH=0x100000
MOVE_SMOOTH,CANCEL_SMOOTH=0x18,0x19
REALTIME_MOVES=(0x10,MOVE_LEFT_HELD,MOVE_SMOOTH)

def normalize_gate(gate):
    if not isinstance(gate,(tuple,list)) or len(gate)!=4:
        raise FusionError('INVALID_ARGUMENT','gate requires epoch, target generation, left press count, overflow')
    return [integer(v,'gate component',0,0xffffffff) for v in gate]

def decode_gate(body):
    if len(body)!=20:raise FusionError('PROTOCOL_ERROR','Wrong AI gate status size')
    valid,held,buttons,*gate=struct.unpack('<BBH4I',body)
    if valid not in (0,1) or held not in (0,1) or bool(held)!=bool(valid and buttons&1) or buttons>31:
        raise FusionError('PROTOCOL_ERROR','Invalid AI gate status')
    return dict(input_valid=bool(valid),left_held=bool(held),raw_buttons=buttons,gate=gate)
