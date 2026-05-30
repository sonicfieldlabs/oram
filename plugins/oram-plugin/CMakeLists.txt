cmake_minimum_required(VERSION 3.22)

project(ORAM_PLUGIN VERSION 2.0.0)

if (EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/JUCE/CMakeLists.txt")
    add_subdirectory(JUCE)
else()
    find_package(JUCE CONFIG REQUIRED)
endif()

juce_add_plugin(ORAMPlugin
    COMPANY_NAME "Momoto"
    BUNDLE_ID "wtf.momoto.oram.plugin"
    IS_SYNTH FALSE
    NEEDS_MIDI_INPUT FALSE
    NEEDS_MIDI_OUTPUT FALSE
    NEEDS_CURL TRUE
    IS_MIDI_EFFECT FALSE
    EDITOR_WANTS_KEYBOARD_FOCUS TRUE
    COPY_PLUGIN_AFTER_BUILD FALSE
    PLUGIN_MANUFACTURER_CODE Momo
    PLUGIN_CODE Oram
    FORMATS AU VST3 Standalone
    PRODUCT_NAME "ORAM"
    VST3_CATEGORIES Fx Tools
    AU_MAIN_TYPE kAudioUnitType_Effect)

target_sources(ORAMPlugin
    PRIVATE
        Source/OramAudioCore.cpp
        Source/OramDaemonClient.cpp
        Source/PluginEditor.cpp
        Source/PluginProcessor.cpp)

target_compile_features(ORAMPlugin PRIVATE cxx_std_17)

target_compile_definitions(ORAMPlugin
    PUBLIC
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=1
        JUCE_VST3_CAN_REPLACE_VST2=0)

target_link_libraries(ORAMPlugin
    PRIVATE
        juce::juce_audio_utils
        juce::juce_gui_extra
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_lto_flags
        juce::juce_recommended_warning_flags)
