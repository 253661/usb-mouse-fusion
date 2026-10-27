add_library(fusion_hid_parser STATIC hid_report_parser.cpp)
target_include_directories(fusion_hid_parser SYSTEM PUBLIC ${CMAKE_CURRENT_LIST_DIR})
target_compile_definitions(fusion_hid_parser PUBLIC HRP_DESCRIPTOR_ONLY=1
    HRP_MAX_USAGE_RANGES_PER_MAIN_ITEM=32 HRP_MAX_PUSH_POP_STACK_SIZE=4
    HRP_IGNORE_LONELY_USAGE_MIN_OR_MAX=0)
if(MSVC)
    target_compile_options(fusion_hid_parser PRIVATE /W3)
else()
    target_compile_options(fusion_hid_parser PRIVATE -Wall -Wextra -fno-exceptions -fno-rtti
        -Wno-unused-parameter -Wno-parentheses -Wno-implicit-fallthrough)
endif()
