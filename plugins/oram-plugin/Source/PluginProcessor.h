#pragma once

#include <atomic>
#include <memory>
#include <vector>

#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_audio_utils/juce_audio_utils.h>

#include "OramAudioCore.h"
#include "OramDaemonClient.h"

class OramAudioProcessor final : public juce::AudioProcessor
{
public:
    OramAudioProcessor();
    ~OramAudioProcessor() override;

    void prepareToPlay (double sampleRate, int samplesPerBlock) override;
    void releaseResources() override;
    bool isBusesLayoutSupported (const BusesLayout& layouts) const override;
    void processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) override;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override { return true; }

    const juce::String getName() const override { return JucePlugin_Name; }
    bool acceptsMidi() const override { return false; }
    bool producesMidi() const override { return false; }
    bool isMidiEffect() const override { return false; }
    double getTailLengthSeconds() const override { return 0.0; }

    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram (int) override {}
    const juce::String getProgramName (int) override { return {}; }
    void changeProgramName (int, const juce::String&) override {}

    void getStateInformation (juce::MemoryBlock& destData) override;
    void setStateInformation (const void* data, int sizeInBytes) override;

    juce::AudioProcessorValueTreeState& parameters() noexcept { return parameterState; }
    OramAudioCore& audioCore() noexcept { return core; }

    void selectLayer (int oneBasedLayer);
    void startRecordingSelected (bool overdub);
    void stopRecording();
    void clearSelectedLayer();
    void undo();
    void redo();
    void resetAll();
    void toggleReversePlaybackSelected();
    void setSelectedLoopFades (float fadeInPct, float fadeOutPct);
    void requestGenerate (
        const juce::String& prompt,
        const juce::String& provider,
        const juce::String& model,
        double durationSeconds);
    void requestCommand (const juce::String& text);

    juce::String status() const;
    void setStatus (const juce::String& message);

private:
    class GenerateJob;
    class CommandJob;

    static juce::AudioProcessorValueTreeState::ParameterLayout createParameterLayout();
    static juce::String generationModelForProvider (const juce::String& provider);
    bool applyAction (const juce::var& action);
    int targetLayerFromAction (const juce::var& action) const;
    bool importAudioFile (const juce::String& path, int& assignedLayer);
    juce::MemoryBlock coreSnapshot() const;
    void restoreCoreSnapshot (const juce::MemoryBlock& snapshot);
    void pushUndo (const juce::String& label);

    OramAudioCore core;
    OramDaemonClient daemonClient;
    juce::AudioFormatManager formatManager;
    juce::ThreadPool backgroundPool { 1 };

    juce::AudioProcessorValueTreeState parameterState;
    mutable juce::CriticalSection statusLock;
    juce::String statusText = "daemon not connected";
    mutable juce::CriticalSection historyLock;
    std::vector<juce::MemoryBlock> undoStack;
    std::vector<juce::MemoryBlock> redoStack;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (OramAudioProcessor)
};
