#ifndef FUSION_LOG_QUEUE_H
#define FUSION_LOG_QUEUE_H
#include <stdbool.h>
#include <stdint.h>
enum { LOG_QUEUE_CAPACITY = 32, LOG_RECORD_BYTES = 160 };
typedef struct { char text[LOG_RECORD_BYTES]; } log_record_t;
typedef struct {
    uint32_t write_index, read_index, dropped;
    log_record_t records[LOG_QUEUE_CAPACITY];
} log_queue_t;
#ifdef __cplusplus
extern "C" {
#endif
// Dedicated diagnostic SPSC: Core1 produces; Core0 consumes. Never stores physical events.
void log_queue_init(log_queue_t *queue);
bool log_queue_push(log_queue_t *queue, const char *text);
bool log_queue_pop(log_queue_t *queue, log_record_t *record);
uint32_t log_queue_dropped(const log_queue_t *queue);
#ifdef __cplusplus
}
#endif
#endif
