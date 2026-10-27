#include <string.h>
#include "atomic_u32.h"
#include "log_queue.h"
_Static_assert((LOG_QUEUE_CAPACITY & (LOG_QUEUE_CAPACITY-1)) == 0, "Power-of-two capacity");
void log_queue_init(log_queue_t *queue) { memset(queue, 0, sizeof(*queue)); }
static bool drop(log_queue_t *queue) {
    fusion_store_u32(&queue->dropped, fusion_load_u32(&queue->dropped)+1);
    return false;
}
bool log_queue_push(log_queue_t *queue, const char *text) {
    unsigned len=0;
    while (len < LOG_RECORD_BYTES && text[len]) ++len;
    if (len == LOG_RECORD_BYTES) return drop(queue);
    const uint32_t write=fusion_load_u32(&queue->write_index), read=fusion_load_u32(&queue->read_index);
    if (write-read >= LOG_QUEUE_CAPACITY) return drop(queue);
    memcpy(queue->records[write & (LOG_QUEUE_CAPACITY-1)].text, text, len+1);
    fusion_store_u32(&queue->write_index,write+1);
    return true;
}
bool log_queue_pop(log_queue_t *queue, log_record_t *record) {
    const uint32_t read=fusion_load_u32(&queue->read_index), write=fusion_load_u32(&queue->write_index);
    if (read == write) return false;
    *record=queue->records[read & (LOG_QUEUE_CAPACITY-1)];
    fusion_store_u32(&queue->read_index,read+1);
    return true;
}
uint32_t log_queue_dropped(const log_queue_t *queue) { return fusion_load_u32(&queue->dropped); }
