// Local session reset for pinned TinyUSB 0.18.0, single root / no Hub.
// Called only from Core1 main after its frame IRQ has returned. No init/claim/abort wait.
bool fusion_usbh_quiesce(uint8_t rhport) {
  if (!tuh_rhport_is_active(rhport)) return false;
  _fusion_quiesced = true;
  ++_fusion_event_generation;
  hcd_event_t removed = {.rhport=rhport, .event_id=HCD_EVENT_DEVICE_REMOVE};
  // Use the actual remove handler, without pumping arbitrary queued attach/events.
  process_removing_device(removed.rhport, 0, 0);
  for (uint8_t i=0; i<TOTAL_DEVICES; ++i) {
    // Also close partially configured interfaces whose connected flag was never set.
    for (uint8_t d=0; d<TOTAL_DRIVER_COUNT; ++d) {
      usbh_class_driver_t const* driver=get_driver(d);
      if (driver) driver->close(i+1);
    }
    hcd_device_close(rhport,i+1);
    clear_device(&_usbh_devices[i]);
  }
  hcd_device_close(rhport,0);
  tu_memclr(&_dev0,sizeof(_dev0));
  tu_memclr(&_ctrl_xfer,sizeof(_ctrl_xfer)); // drops callback/user_data/buffer, including address zero
  tu_memclr(&_usbh_epbuf,sizeof(_usbh_epbuf));
  fusion_queued_event_t dropped;
  for (unsigned i=0; i<CFG_TUH_TASK_QUEUE_SZ; ++i)
    if (!osal_queue_receive(_usbh_q,&dropped,0)) break;
  return osal_queue_empty(_usbh_q);
}
bool fusion_usbh_rearm(uint8_t rhport) {
  if (!tuh_rhport_is_active(rhport) || !_fusion_quiesced || !osal_queue_empty(_usbh_q) ||
      _dev0.enumerating || _ctrl_xfer.stage!=CONTROL_STAGE_IDLE) return false;
  for (uint8_t i=0; i<TOTAL_DEVICES; ++i) if (_usbh_devices[i].connected) return false;
  ++_fusion_event_generation;
  _fusion_quiesced=false; // controller, class drivers and queue locks retain their original claims
  return true;
}
uint32_t fusion_usbh_generation(void) { return _fusion_event_generation; }
void fusion_usbh_probe(fusion_usbh_probe_t *out) {
  if (!out) return;
  *out=(fusion_usbh_probe_t){
    .generation=_fusion_event_generation, .enumerating=_dev0.enumerating,
    .configured=_usbh_devices[0].configured, .stage=_ctrl_xfer.stage,
    .next_step=_ctrl_xfer.complete_cb==process_enumeration ? (uint32_t)_ctrl_xfer.user_data : 0,
    .daddr=_ctrl_xfer.daddr, .request=_usbh_epbuf.request.bRequest,
    .value=_usbh_epbuf.request.wValue, .length=_usbh_epbuf.request.wLength,
    .actual_len=_ctrl_xfer.actual_len
  };
}
#if FUSION_HOST_SESSION_TEST
// Test-only delayed queue injection uses the actual production generation filter.
void fusion_usbh_test_defer(hcd_event_t const* event,uint32_t generation) {
  fusion_queued_event_t record={.event=*event,.generation=generation};
  (void)osal_queue_send(_usbh_q,&record,false);
}
#endif
