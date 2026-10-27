#include <string.h>
#include "atomic_u32.h"
#include "event_bus.h"
_Static_assert((PHYSICAL_QUEUE_CAPACITY & (PHYSICAL_QUEUE_CAPACITY-1))==0,"SPSC power-of-two capacity");
_Static_assert(sizeof(physical_snapshot_t)%sizeof(uint32_t)==0,"Atomic mailbox words");
static physical_record_t records[PHYSICAL_QUEUE_CAPACITY];
static uint32_t write_index, read_index, target_generation, high_water;
static physical_snapshot_t producer; // Core1 only
static uint32_t revision, mailbox[sizeof(physical_snapshot_t)/sizeof(uint32_t)];
static bool newer(uint32_t value,uint32_t previous) {
    const uint32_t difference=value-previous;
    return difference!=0 && difference<UINT32_C(0x80000000);
}
static void publish(void) {
    uint32_t words[sizeof(mailbox)/sizeof(mailbox[0])];
    memcpy(words,&producer,sizeof(words));
    const uint32_t version=fusion_load_u32(&revision);
    fusion_store_u32(&revision,version+1);
    for (unsigned i=0;i<sizeof(words)/sizeof(words[0]);++i) fusion_store_u32(&mailbox[i],words[i]);
    fusion_store_u32(&revision,version+2);
}
void event_bus_init(void) {
    // Startup only; runtime reset never writes the other core's index.
    memset(records,0,sizeof(records)); memset(&producer,0,sizeof(producer));
    memset(mailbox,0,sizeof(mailbox));
    write_index=read_index=high_water=revision=0; target_generation=1;
}
bool event_bus_physical_begin(uint32_t epoch) {
    if (!epoch || (producer.epoch && !newer(epoch,producer.epoch))) return false;
    producer.epoch=epoch; producer.connected=1;
    producer.target_generation=fusion_load_u32(&target_generation);
    producer.buttons=producer.sequence=0;
    memset(producer.button_presses,0,sizeof producer.button_presses);
    producer.barrier_cutoff=producer.snapshot_cutoff=fusion_load_u32(&write_index);
    ++producer.barrier; publish(); return true;
}
bool event_bus_offer(const mouse_delta_t *event) {
    if (!event || !producer.connected || !event->epoch || event->epoch!=producer.epoch ||
        producer.target_generation!=fusion_load_u32(&target_generation) ||
        ((event->buttons | event->buttons_valid) & ~(uint32_t)MOUSE_SUPPORTED_BUTTON_MASK)) return false;
    // The latest BUTTON snapshot is independent of FIFO admission, including
    // axis-only report IDs. Relative increments never enter this mailbox.
    const uint32_t next=(producer.buttons & ~event->buttons_valid) | (event->buttons & event->buttons_valid);
    const uint32_t rising=next & ~producer.buttons;
    for(unsigned i=0;i<5;i++)if(rising&(1u<<i))++producer.button_presses[i];
    producer.buttons=next;
    producer.sequence=event->sequence;
    const uint32_t write=fusion_load_u32(&write_index), read=fusion_load_u32(&read_index);
    const uint32_t depth=write-read;
    const bool admitted=depth<PHYSICAL_QUEUE_CAPACITY;
    if (admitted) {
        records[write & (PHYSICAL_QUEUE_CAPACITY-1)]=(physical_record_t){*event,producer.target_generation};
        fusion_store_u32(&write_index,write+1); // release AFTER the value copy
        if (depth+1>fusion_load_u32(&high_water)) fusion_store_u32(&high_water,depth+1);
    } else ++producer.overflow;
    producer.snapshot_cutoff=write+(admitted ? 1u : 0u);
    publish(); return admitted;
}
void event_bus_invalidate(uint32_t epoch) {
    if (!producer.connected || epoch!=producer.epoch) return;
    producer.connected=0; producer.buttons=0;
    producer.barrier_cutoff=producer.snapshot_cutoff=fusion_load_u32(&write_index);
    ++producer.barrier; publish();
}
bool event_bus_snapshot(physical_snapshot_t *out) {
    if (!out) return false;
    for (unsigned attempt=0;attempt<PHYSICAL_SNAPSHOT_ATTEMPTS;++attempt) {
        uint32_t words[sizeof(mailbox)/sizeof(mailbox[0])];
        const uint32_t before=fusion_load_u32(&revision);
        for (unsigned i=0;i<sizeof(words)/sizeof(words[0]);++i) words[i]=fusion_load_u32(&mailbox[i]);
        const uint32_t after=fusion_load_u32(&revision);
        if (!(before&1u) && before==after) { memcpy(out,words,sizeof(words)); return true; }
    }
    return false; // no partially coherent mailbox is applied
}
bool event_bus_peek(physical_record_t *out) {
    if (!out) return false;
    const uint32_t read=fusion_load_u32(&read_index), write=fusion_load_u32(&write_index);
    if (read==write) return false;
    *out=records[read & (PHYSICAL_QUEUE_CAPACITY-1)]; return true;
}
bool event_bus_pop(void) {
    const uint32_t read=fusion_load_u32(&read_index);
    if (read==fusion_load_u32(&write_index)) return false;
    fusion_store_u32(&read_index,read+1); return true; // release AFTER consumption
}
uint32_t event_bus_discard_before(uint32_t cutoff) {
    const uint32_t read=fusion_load_u32(&read_index), write=fusion_load_u32(&write_index);
    const uint32_t count=cutoff-read;
    if (count>write-read) return 0; // cutoff already consumed; never rewind
    fusion_store_u32(&read_index,cutoff); return count;
}
void event_bus_target_reset(void) {
    uint32_t generation=fusion_load_u32(&target_generation)+1;
    if (!generation) ++generation;
    fusion_store_u32(&target_generation,generation);
    // A producer already copying a record may publish after this cutoff; its
    // old target_generation is rejected later without touching write_index.
    fusion_store_u32(&read_index,fusion_load_u32(&write_index));
}
uint32_t event_bus_target_generation(void) { return fusion_load_u32(&target_generation); }
uint32_t event_bus_depth(void) {
    const uint32_t read=fusion_load_u32(&read_index), write=fusion_load_u32(&write_index);
    const uint32_t depth=write-read;
    return depth>PHYSICAL_QUEUE_CAPACITY ? PHYSICAL_QUEUE_CAPACITY : depth;
}
uint32_t event_bus_high_water(void) { return fusion_load_u32(&high_water); }
