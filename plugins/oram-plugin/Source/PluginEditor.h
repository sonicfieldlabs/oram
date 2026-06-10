#pragma once

#include <array>
#include <memory>

#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_gui_basics/juce_gui_basics.h>

#include "PluginProcessor.h"

class OramAudioProcessorEditor final : public juce::AudioProcessorEditor,
                                       private juce::Timer
{
public:
    explicit OramAudioProcessorEditor (OramAudioProcessor&);
    ~OramAudioProcessorEditor() override;

    void paint (juce::Graphics&) override;
    void resized() override;

private:
    void timerCallback() override;
    void configureSlider (juce::Slider& slider);
    void refreshLayerLabels();

    OramAudioProcessor& audioProcessor;

    juce::ComboBox layerSelector;
    juce::TextButton undoButton { "Undo" };
    juce::TextButton redoButton { "Redo" };
    juce::TextButton recordButton { "Record" };
    juce::TextButton overdubButton { "Overdub" };
    juce::TextButton stopButton { "Stop" };
    juce::TextButton clearButton { "Clear" };
    juce::TextButton resetButton { "Reset" };
    juce::TextButton reversePlaybackButton { "Reverse Play" };
    juce::TextButton fadeInButton { "Fade In" };
    juce::TextButton fadeOutButton { "Fade Out" };
    juce::TextButton generateButton { "Generate" };
    juce::TextButton commandButton { "Run" };
    juce::TextEditor promptEditor;
    juce::TextEditor commandEditor;
    juce::Slider durationSlider;
    juce::Label durationLabel;
    juce::Label titleLabel;
    juce::Label taglineLabel;
    juce::Label statusLabel;
    std::array<juce::Label, OramAudioCore::maxLayers> layerLabels;

    juce::Slider inputMonitorSlider;
    juce::Slider loopLevelSlider;
    juce::Label inputMonitorLabel;
    juce::Label loopLevelLabel;

    using SliderAttachment = juce::AudioProcessorValueTreeState::SliderAttachment;
    std::unique_ptr<SliderAttachment> inputMonitorAttachment;
    std::unique_ptr<SliderAttachment> loopLevelAttachment;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (OramAudioProcessorEditor)
};
