"""Generate one project-local USBH session adaptation. Never modifies the SDK."""
import argparse
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[3]
SOURCE=REPO/'.build-deps/pico-sdk/lib/tinyusb/src/host/usbh.c'

def once(text, old, new):
    if text.count(old)!=1: raise ValueError('Pinned USBH context changed: '+old[:70])
    return text.replace(old,new,1)

def generate():
    raw=SOURCE.read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    manifest=json.loads((HERE/'upstream.json').read_text())
    if digest!=manifest['source_sha256']: raise ValueError('Pinned USBH source SHA256 changed')
    text=raw.decode('utf-8').replace('\r\n','\n')
    text=once(text,'#include "hub.h"','#include "hub.h"\n#include "host_session.h"')
    # With no DATA stage, SETUP falls through with its eight-byte length.
    # Callback actual_len counts DATA payload only, including zero for SET_PROTOCOL.
    text=once(text,'        _ctrl_xfer.actual_len = (uint16_t) xferred_bytes;',
        '        _ctrl_xfer.actual_len = request->wLength ? (uint16_t) xferred_bytes : 0;')
    text=once(text,'OSAL_QUEUE_DEF(usbh_int_set, _usbh_qdef, CFG_TUH_TASK_QUEUE_SZ, hcd_event_t);',
        'typedef struct { hcd_event_t event; uint32_t generation; } fusion_queued_event_t;\n'
        'static volatile uint32_t _fusion_event_generation=1;\nstatic bool _fusion_quiesced;\n'
        'OSAL_QUEUE_DEF(usbh_int_set, _usbh_qdef, CFG_TUH_TASK_QUEUE_SZ, fusion_queued_event_t);')
    text=once(text,'  TU_ASSERT(osal_queue_send(_usbh_q, event, in_isr));',
        '  if (_fusion_quiesced || fusion_host_cancel_requested()) return false;\n'
        '  fusion_queued_event_t record={.event=*event,.generation=_fusion_event_generation};\n'
        '  tuh_event_hook_cb(event->rhport, event->event_id, in_isr);\n'
        '  if (!osal_queue_send(_usbh_q, &record, in_isr)) { fusion_host_event_lost(); return false; }')
    # Publish hotplug notices even when queue capacity is exhausted, once only.
    text=once(text,'  tuh_event_hook_cb(event->rhport, event->event_id, in_isr);\n  return true;', '  return true;')
    text=once(text,'    hcd_event_t event;\n    if (!osal_queue_receive(_usbh_q, &event, timeout_ms)) return;',
        '    if (_fusion_quiesced || fusion_host_cancel_requested()) return;\n'
        '    fusion_queued_event_t record;\n'
        '    if (!osal_queue_receive(_usbh_q, &record, timeout_ms)) return;\n'
        '    hcd_event_t event=record.event;\n'
        '    if (record.generation!=_fusion_event_generation) continue;')
    text=once(text,'  while (1) {\n    if (_fusion_quiesced',
        '  for (unsigned budget=0; budget<CFG_TUH_TASK_QUEUE_SZ; ++budget) {\n    if (_fusion_quiesced')
    text=once(text,'            tuh_edpt_abort_xfer(0, 0);\n            enum_new_device(&event);',
        '            break; // duplicate attach does not abort/restart a live enumeration')
    text=once(text,'          _dev0.enumerating = 1;',
        '          if (_usbh_devices[0].connected) break; // duplicate attach on this single-root profile\n'
        '          fusion_host_connection_event(true);\n          _dev0.enumerating = 1;')
    text=once(text,'        process_removing_device(event.rhport, event.connection.hub_addr, event.connection.hub_port);',
        '        fusion_host_connection_event(false);\n'
        '        process_removing_device(event.rhport, event.connection.hub_addr, event.connection.hub_port);\n'
        '        if (event.connection.hub_addr==0) {\n'
        '          hcd_device_close(event.rhport,0);\n'
        '          tu_memclr(&_dev0,sizeof(_dev0)); tu_memclr(&_ctrl_xfer,sizeof(_ctrl_xfer));\n        }')
    text=once(text,'TU_ATTR_FAST_FUNC void hcd_event_handler(hcd_event_t const* event, bool in_isr) {',
        'TU_ATTR_FAST_FUNC void hcd_event_handler(hcd_event_t const* event, bool in_isr) {\n'
        '  if (_fusion_quiesced || fusion_host_cancel_requested()) return;\n'
        '  if (event->event_id==HCD_EVENT_DEVICE_REMOVE) ++_fusion_event_generation;')
    text=once(text,'      tusb_time_delay_ms_api(ATTEMPT_DELAY_MS);',
        '      if (!fusion_host_delay_ms(ATTEMPT_DELAY_MS)) return;')
    # Retain all normal protocol waits; make the two root waits cancellable.
    text=once(text,'\n    tusb_time_delay_ms_api(ENUM_RESET_DELAY_MS);',
        '\n    if (!fusion_host_delay_ms(ENUM_RESET_DELAY_MS)) return true;')
    text=text.replace('    tusb_time_delay_ms_api(ENUM_DEBOUNCING_DELAY_MS);',
        '    if (!fusion_host_delay_ms(ENUM_DEBOUNCING_DELAY_MS)) return true;')
    text=once(text,'      tusb_time_delay_ms_api(2);','      if (!fusion_host_delay_ms(2)) return;')
    # Address-zero re-reset uses an enum callback (void).
    text=text.replace('      tusb_time_delay_ms_api(ENUM_RESET_DELAY_MS);',
        '      if (!fusion_host_delay_ms(ENUM_RESET_DELAY_MS)) return;')
    end=text.rfind('#endif')
    text=text[:end]+(HERE/'usbh_session.inc').read_text(encoding='utf-8')+'\n'+text[end:]
    return text

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
    result=generate(); a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(result,encoding='utf-8',newline='\n')
    print('Pinned project-local USBH session source generated; original SDK unchanged')
