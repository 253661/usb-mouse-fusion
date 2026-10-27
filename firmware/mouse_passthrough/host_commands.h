#ifndef FUSION_HOST_COMMANDS_H
#define FUSION_HOST_COMMANDS_H
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct { uint32_t request, quiesce_ack, rearm_request, rearm_ack, frame_ticket, serial; } host_commands_t;
void host_commands_quiesce(host_commands_t *c, uint32_t reason); // Core0
bool host_commands_quiesced(const host_commands_t *c);
bool host_commands_rearm(host_commands_t *c);
bool host_commands_armed(const host_commands_t *c);
void host_commands_frames(host_commands_t *c, bool enabled);
bool host_commands_ack_quiesce(host_commands_t *c, uint32_t ticket); // Core1
bool host_commands_ack_rearm(host_commands_t *c, uint32_t ticket);
bool host_commands_run(const host_commands_t *c, uint32_t ticket);
#ifdef __cplusplus
}
#endif
#endif
