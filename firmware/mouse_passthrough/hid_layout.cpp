#include "hid_layout.h"
#include <cstring>
#include <limits>
#include "hid_report_parser.h"
const uint32_t fusion_hid_layout_contract[12]={0x48494432,HID_LAYOUT_DESCRIPTOR_MAX,HID_LAYOUT_INPUT_MAX,
    HID_LAYOUT_REPORT_MAX,HID_LAYOUT_FIELD_MAX,HID_LAYOUT_APPLICATION_MAX,HID_LAYOUT_COLLECTION_MAX,
    HID_LAYOUT_GLOBAL_MAX,HID_LAYOUT_USAGE_RANGE_MAX,sizeof(hid_layout_t),sizeof(hid_field_t),sizeof(hid::DescriptorParser)};

namespace {
static_assert(HRP_MAX_USAGE_RANGES_PER_MAIN_ITEM==HID_LAYOUT_USAGE_RANGE_MAX,"Shared parser capacity");
static_assert(HRP_MAX_PUSH_POP_STACK_SIZE==HID_LAYOUT_GLOBAL_MAX,"Shared global depth");
// Reject unsupported spans before entering the upstream parser. Do not truncate.
hid_layout_result_t preflight(const uint8_t *d,size_t length) {
    if (!d) return HID_LAYOUT_BAD_ARGUMENT;
    if (!length) return HID_LAYOUT_EMPTY;
    if (length>HID_LAYOUT_DESCRIPTOR_MAX) return HID_LAYOUT_DESCRIPTOR_LIMIT;
    for (size_t off=0;off<length;) {
        const uint8_t b=d[off++];
        if (b==0xfe) return HID_LAYOUT_LONG_ITEM;
        const unsigned size=(b&3)==3 ? 4 : b&3;
        if (size>length-off) return HID_LAYOUT_TRUNCATED_ITEM;
        const uint8_t item=b&0xfc;
        if (item==0xa8) return HID_LAYOUT_DELIMITER;
        switch (item) {
        case 0xc0: case 0xa4: case 0xb4:
            if (size!=0) return HID_LAYOUT_MALFORMED;
            break;
        case 0x80: case 0x90: case 0xb0: case 0xa0:
        case 0x04: case 0x14: case 0x24: case 0x34: case 0x44: case 0x54: case 0x64:
        case 0x74: case 0x84: case 0x94:
        case 0x08: case 0x18: case 0x28: case 0x38: case 0x48: case 0x58: case 0x78: case 0x88: case 0x98:
            if (!size) return HID_LAYOUT_MALFORMED;
            break;
        default: return HID_LAYOUT_UNSUPPORTED_ITEM;
        }
        off+=size;
    }
    return HID_LAYOUT_OK;
}

class Builder final : public hid::DescriptorParser::EventHandler {
    hid_layout_t &layout;
    uint8_t active[HID_LAYOUT_COLLECTION_MAX]{};
    uint32_t depth=0;
    uint16_t collection_number=0;
    int report(uint8_t id) {
        for (unsigned i=0;i<layout.report_count;++i) if (layout.reports[i].id==id) return static_cast<int>(i);
        if (layout.report_count==HID_LAYOUT_REPORT_MAX) return -1;
        const unsigned index=layout.report_count++;
        layout.reports[index].id=id;
        layout.has_report_ids |= id!=0;
        return static_cast<int>(index);
    }
    // Advance Input, Output and Feature independently, including padding/ignored apps.
    int advance(hid::ReportType type,uint8_t id,uint64_t bits,uint32_t &start,unsigned &index) {
        const int found=report(id);
        if (found<0) return HID_LAYOUT_REPORT_LIMIT;
        index=static_cast<unsigned>(found);
        auto &r=layout.reports[index];
        uint32_t *value=type==hid::ReportType::input ? &r.input_bits :
                        type==hid::ReportType::output ? &r.output_bits : &r.feature_bits;
        start=*value;
        if (bits>std::numeric_limits<uint32_t>::max()-start) return HID_LAYOUT_ARITHMETIC;
        if (type==hid::ReportType::input && bits+start>(HID_LAYOUT_INPUT_MAX-(id ? 1u : 0u))*8u)
            return HID_LAYOUT_INPUT_LIMIT;
        *value+=static_cast<uint32_t>(bits);
        return HID_LAYOUT_OK;
    }
    uint8_t application() const { return depth ? active[depth-1] : static_cast<uint8_t>(HID_LAYOUT_NO_APPLICATION); }
    void reject(uint8_t app,hid_layout_result_t reason) {
        if (layout.applications[app].result==HID_LAYOUT_OK) layout.applications[app].result=reason;
    }
    void map(uint8_t app,unsigned report_index,uint32_t offset,const hid::DescriptorParser::FieldParams &fp,
             uint16_t page,uint16_t usage) {
        const bool button=page==9;
        const bool axis=(page==1 && (usage==0x30 || usage==0x31 || usage==0x38)) || (page==0x0c && usage==0x238);
        if (!button && !axis) return;
        if (button && (usage<1 || usage>16)) { reject(app,HID_LAYOUT_BUTTON_LIMIT); return; }
        if (!(fp.flags&hid::FLAG_FIELD_VARIABLE)) {
            reject(app,button ? HID_LAYOUT_BUTTON_ARRAY : HID_LAYOUT_FIELD_FLAGS); return;
        }
        if (axis && !(fp.flags&hid::FLAG_FIELD_RELATIVE)) { reject(app,HID_LAYOUT_ABSOLUTE_AXIS); return; }
        if ((fp.flags & ~uint16_t(0x26)) || (button && (fp.flags&hid::FLAG_FIELD_RELATIVE))) {
            reject(app,HID_LAYOUT_FIELD_FLAGS); return;
        }
        const auto &g=*fp.globals;
        if (!g.report_size || g.report_size>32) { reject(app,HID_LAYOUT_FIELD_WIDTH); return; }
        const int64_t high=g.logical_min<0 ? int64_t(g.logical_max) : int64_t(uint32_t(g.logical_max));
        const int64_t low=g.logical_min;
        const int64_t representable_high=g.logical_min<0 ? (int64_t(1)<<(g.report_size-1))-1 : (int64_t(1)<<g.report_size)-1;
        const int64_t representable_low=g.logical_min<0 ? -(int64_t(1)<<(g.report_size-1)) : 0;
        if (low<representable_low || high>representable_high || high<low || high>INT32_MAX ||
            (button && (low!=0 || high!=1))) { reject(app,HID_LAYOUT_LOGICAL_RANGE); return; }
        for (unsigned i=0;i<layout.field_count;++i) {
            const auto &f=layout.fields[i];
            if (f.application==app && f.usage_page==page && f.usage==usage) {
                reject(app,HID_LAYOUT_DUPLICATE_USAGE); return;
            }
        }
        auto &a=layout.applications[app];
        // A full table is a global failure, handled in Field before every mapped insert.
        if (layout.field_count==HID_LAYOUT_FIELD_MAX) return;
        auto &f=layout.fields[layout.field_count++];
        f.bit_offset=static_cast<uint16_t>(offset); f.bits=static_cast<uint8_t>(g.report_size);
        f.usage_page=page; f.usage=usage; f.flags=fp.flags;
        f.logical_min=g.logical_min; f.logical_max=static_cast<uint32_t>(g.logical_max);
        f.report_count=static_cast<uint16_t>(g.report_count);
        f.report_index=static_cast<uint8_t>(report_index); f.application=app;
        ++a.fields;
        if (button) a.buttons |= uint32_t(1)<<(usage-1);
        else if (page==1 && usage==0x30) a.has_x=1;
        else if (page==1 && usage==0x31) a.has_y=1;
        else if (page==1 && usage==0x38) a.has_wheel=1;
        else a.has_pan=1;
    }
public:
    explicit Builder(hid_layout_t &out):layout(out) {}
    int BeginCollection(uint8_t type,uint16_t page,uint16_t usage,uint32_t new_depth) override {
        if (new_depth>HID_LAYOUT_COLLECTION_MAX) return HID_LAYOUT_COLLECTION_LIMIT;
        uint8_t app=application();
        ++collection_number;
        if (type==hid::COLLECTION_TYPE_APPLICATION) {
            app=HID_LAYOUT_NO_APPLICATION;
            if (page==1 && usage==2) {
                if (layout.application_count==HID_LAYOUT_APPLICATION_MAX) return HID_LAYOUT_APPLICATION_LIMIT;
                app=layout.application_count++;
                layout.applications[app].collection_number=collection_number;
            }
        }
        active[new_depth-1]=app; depth=new_depth;
        return HID_LAYOUT_OK;
    }
    int EndCollection(uint32_t closing_depth) override { depth=closing_depth-1; return HID_LAYOUT_OK; }
    int Padding(hid::ReportType type,uint8_t id,uint32_t bits) override {
        uint32_t start=0; unsigned index=0;
        return advance(type,id,bits,start,index);
    }
    int Field(const hid::DescriptorParser::FieldParams &fp) override {
        uint32_t start=0; unsigned index=0;
        const auto &g=*fp.globals;
        int result=advance(fp.report_type,g.report_id,uint64_t(g.report_size)*g.report_count,start,index);
        if (result) return result;
        const uint8_t app=application();
        if (app==HID_LAYOUT_NO_APPLICATION || (fp.flags&hid::FLAG_FIELD_CONST)) return HID_LAYOUT_OK;
        if (fp.report_type==hid::ReportType::feature) {
            for (unsigned i=0;i<fp.num_usage_ranges;++i) {
                const auto &r=fp.usage_ranges[i];
                if (r.usage_page==1 && r.usage_min<=0x48 && r.usage_max>=0x48) reject(app,HID_LAYOUT_WHEEL_FEATURE);
            }
        }
        if (fp.report_type!=hid::ReportType::input) return HID_LAYOUT_OK;
        unsigned range=0;
        uint32_t usage=fp.usage_ranges[0].usage_min;
        for (uint32_t i=0;i<g.report_count;++i) {
            const auto &r=fp.usage_ranges[range];
            const bool mapped=r.usage_page==9 || (r.usage_page==1 && (usage==0x30 || usage==0x31 || usage==0x38)) ||
                              (r.usage_page==0x0c && usage==0x238);
            if (mapped && layout.field_count==HID_LAYOUT_FIELD_MAX) return HID_LAYOUT_FIELD_LIMIT;
            map(app,index,start+i*g.report_size,fp,r.usage_page,static_cast<uint16_t>(usage));
            if (usage<r.usage_max) ++usage;
            else if (range+1<fp.num_usage_ranges) usage=fp.usage_ranges[++range].usage_min;
            // HID Variable fields repeat the final declared usage when count exceeds usages.
        }
        return HID_LAYOUT_OK;
    }
};
}

hid_layout_result_t hid_layout_build(const uint8_t *descriptor,size_t length,hid_layout_t *out) {
    if (!out) return HID_LAYOUT_BAD_ARGUMENT;
    std::memset(out,0,sizeof(*out));
    out->selected_application=HID_LAYOUT_NO_APPLICATION;
    out->result=preflight(descriptor,length);
    if (out->result!=HID_LAYOUT_OK) return out->result;
    hid::DescriptorParser parser;
    Builder builder(*out);
    const int error=parser.Parse(descriptor,length,&builder);
    out->parser_error=error;
    if (error) {
        out->result=error>0 ? static_cast<hid_layout_result_t>(error) :
            error==hid::ERR_PUSH_STACK_OVERFLOW ? HID_LAYOUT_GLOBAL_LIMIT :
            error==hid::ERR_LOGICAL_MIN_IS_GREATER_THAN_MAX ? HID_LAYOUT_LOGICAL_RANGE :
            error==hid::ERR_TOO_MANY_USAGES ? HID_LAYOUT_USAGE_LIMIT : HID_LAYOUT_MALFORMED;
        return out->result;
    }
    out->parsed=true;
    if (!out->application_count) return out->result=HID_LAYOUT_NO_MOUSE;
    for (unsigned i=0;i<out->application_count;++i) {
        auto &a=out->applications[i];
        if (a.result==HID_LAYOUT_OK && (!a.has_x || !a.has_y)) a.result=HID_LAYOUT_MISSING_XY;
        if (a.result==HID_LAYOUT_OK && out->selected_application==HID_LAYOUT_NO_APPLICATION)
            out->selected_application=static_cast<uint8_t>(i);
    }
    if (out->selected_application==HID_LAYOUT_NO_APPLICATION) return out->result=out->applications[0].result;
    out->partial=out->application_count>1;
    out->ready=true;
    return out->result=HID_LAYOUT_OK;
}

const char *hid_layout_result_name(hid_layout_result_t result) {
    static const char *const names[]={"OK","NOT_IMPLEMENTED","BAD_ARGUMENT","EMPTY","DESCRIPTOR_LIMIT",
        "TRUNCATED_ITEM","LONG_ITEM","DELIMITER","UNSUPPORTED_ITEM","MALFORMED","COLLECTION_LIMIT",
        "GLOBAL_LIMIT","USAGE_LIMIT","REPORT_LIMIT","FIELD_LIMIT","INPUT_LIMIT","ARITHMETIC",
        "NO_MOUSE","MISSING_XY","ABSOLUTE_AXIS","BUTTON_ARRAY","BUTTON_LIMIT","FIELD_WIDTH",
        "LOGICAL_RANGE","FIELD_FLAGS","DUPLICATE_USAGE","WHEEL_FEATURE","APPLICATION_LIMIT"};
    return static_cast<unsigned>(result)<sizeof(names)/sizeof(names[0]) ? names[result] : "UNKNOWN";
}
