if(NOT Python3_EXECUTABLE)
    set(Python3_EXECUTABLE "C:/Users/pxh25/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe")
endif()
set(FUSION_USBH_SOURCE "${CMAKE_CURRENT_BINARY_DIR}/generated/host_session/usbh.c")
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/patch_usbh.py" --output "${FUSION_USBH_SOURCE}"
    RESULT_VARIABLE fusion_patch_result OUTPUT_VARIABLE fusion_patch_output ERROR_VARIABLE fusion_patch_error)
if(NOT fusion_patch_result EQUAL 0)
    message(FATAL_ERROR "Pinned USBH session adaptation failed: ${fusion_patch_error}")
endif()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_LIST_DIR}/patch_usbh.py" "${CMAKE_CURRENT_LIST_DIR}/usbh_session.inc" "${CMAKE_CURRENT_LIST_DIR}/upstream.json")
