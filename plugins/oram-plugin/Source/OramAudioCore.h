#pragma once

#include <array>

#include <juce_audio_basics/juce_audio_basics.h>

class OramAudioCore
{
public:
    static constexpr int maxLayers = 4;

    struct LayerView
    {
        int slot = 1;
        bool empty = true;
        bool muted = false;
        bool solo = false;
        bool recording = false;
        float volume = 1.0f;
        float pan = 0.0f;
        bool playbackReverse = false;
        double durationSeconds = 0.0;
        bool loopEnabled = false;
        double loopStartPct = 0.0;
        double loopEndPct = 100.0;
        double loopFadeInPct = 0.0;
        double loopFadeOutPct = 0.0;
    };

    void prepare (double newSampleRate, int maxBlockSize, int channelCount);
    void reset();
    void process (juce::AudioBuffer<float>& buffer, float inputMonitor, float loopLevel);

    void selectLayer (int oneBasedLayer);
    int selectedLayer() const noexcept { return selectedLayerIndex + 1; }

    void startRecordingSelected (bool shouldOverdub);
    void stopRecording();
    void clearSelectedLayer();
    void toggleMuteSelected();
    void toggleSoloSelected();
    void setSelectedVolume (float volume);
    void setSelectedPan (float pan);
    void setSelectedLoopRegion (float startPct, float endPct, bool enabled);
    void setSelectedLoopFades (float fadeInPct, float fadeOutPct);
    void setSelectedPlaybackReverse (bool enabled);
    bool selectedPlaybackReverse() const;
    void reverseSelected();
    void changeSelectedSpeed (float speed);
    void filterSelected (bool highpass, float cutoffHz);
    void reverbSelected (float wet);
    void fadeSelected (bool fadeIn, double seconds);
    void trimSelected (bool trimStart, double seconds);
    void silenceAll();
    void resetAll();

    int loadAudioToFirstEmpty (const juce::AudioBuffer<float>& source, double sourceSampleRate);
    std::array<LayerView, maxLayers> snapshot() const;
    void writeStateToStream (juce::OutputStream& stream) const;
    bool readStateFromStream (juce::InputStream& stream);

private:
    struct Layer
    {
        juce::AudioBuffer<float> audio;
        int lengthSamples = 0;
        int playhead = 0;
        float volume = 1.0f;
        float pan = 0.0f;
        bool muted = false;
        bool solo = false;
        bool loopEnabled = false;
        int loopStart = 0;
        int loopEnd = 0;
        int loopFadeIn = 0;
        int loopFadeOut = 0;
        bool playbackReverse = false;
    };

    Layer* currentRecordingLayer() noexcept;
    const Layer* currentRecordingLayer() const noexcept;
    void ensureLayerCapacity (Layer& layer, int requiredSamples);
    static int regionStart (const Layer& layer) noexcept;
    static int regionEnd (const Layer& layer) noexcept;
    static int regionLength (const Layer& layer) noexcept;
    static float loopFadeGain (const Layer& layer, int phase) noexcept;
    static void clampLoopFades (Layer& layer) noexcept;
    static void resetLayerMetadata (Layer& layer) noexcept;
    static float panLeftGain (float pan) noexcept;
    static float panRightGain (float pan) noexcept;

    mutable juce::SpinLock stateLock;
    std::array<Layer, maxLayers> layers;

    double sampleRate = 48000.0;
    int channels = 2;
    int maxRecordSamples = 0;
    int selectedLayerIndex = 0;
    int recordingLayerIndex = -1;
    int recordWritePosition = 0;
    bool overdub = false;
};
