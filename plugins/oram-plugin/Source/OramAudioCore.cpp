#include "OramAudioCore.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

namespace
{
constexpr double maxRecordSeconds = 120.0;
constexpr uint32_t stateMagic = 0x4f52414d; // ORAM
constexpr uint32_t stateVersion = 2;
}

void OramAudioCore::prepare (double newSampleRate, int, int channelCount)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);

    sampleRate = newSampleRate > 0.0 ? newSampleRate : 44100.0;
    channels = juce::jlimit (1, 2, channelCount);
    maxRecordSamples = juce::roundToInt (sampleRate * maxRecordSeconds);

    for (auto& layer : layers)
    {
        layer.audio.setSize (2, maxRecordSamples, true, true, true);
        layer.lengthSamples = juce::jlimit (0, maxRecordSamples, layer.lengthSamples);
        layer.playhead = layer.lengthSamples > 0 ? layer.playhead % layer.lengthSamples : 0;
        if (layer.loopEnd > layer.lengthSamples)
            layer.loopEnd = layer.lengthSamples;
        clampLoopFades (layer);
    }

    selectedLayerIndex = juce::jlimit (0, maxLayers - 1, selectedLayerIndex);
}

void OramAudioCore::reset()
{
    const juce::SpinLock::ScopedLockType lock (stateLock);

    for (auto& layer : layers)
    {
        layer.lengthSamples = 0;
        layer.playhead = 0;
        resetLayerMetadata (layer);
    }

    recordingLayerIndex = -1;
    recordWritePosition = 0;
    overdub = false;
}

void OramAudioCore::process (juce::AudioBuffer<float>& buffer, float inputMonitor, float loopLevel)
{
    const juce::SpinLock::ScopedTryLockType lock (stateLock);
    if (! lock.isLocked())
        return;

    const auto numSamples = buffer.getNumSamples();
    const auto numChannels = juce::jmin (2, buffer.getNumChannels());
    if (numSamples <= 0 || numChannels <= 0)
        return;

    auto* left = buffer.getWritePointer (0);
    auto* right = buffer.getNumChannels() > 1 ? buffer.getWritePointer (1) : left;

    if (auto* recordingLayer = currentRecordingLayer())
    {
        for (int sample = 0; sample < numSamples; ++sample)
        {
            if (overdub && recordingLayer->lengthSamples > 0)
            {
                const auto start = regionStart (*recordingLayer);
                const auto length = regionLength (*recordingLayer);
                const auto position = start + ((recordingLayer->playhead - start + sample) % length);
                recordingLayer->audio.setSample (
                    0, position, recordingLayer->audio.getSample (0, position) + left[sample] * 0.7f);
                recordingLayer->audio.setSample (
                    1, position, recordingLayer->audio.getSample (1, position) + right[sample] * 0.7f);
                continue;
            }

            if (recordWritePosition >= maxRecordSamples)
                break;

            recordingLayer->audio.setSample (0, recordWritePosition, left[sample]);
            recordingLayer->audio.setSample (1, recordWritePosition, right[sample]);
            ++recordWritePosition;
            recordingLayer->lengthSamples = juce::jmax (recordingLayer->lengthSamples, recordWritePosition);
        }
    }

    const auto anySolo = std::any_of (layers.begin(), layers.end(), [] (const Layer& layer)
    {
        return layer.solo && layer.lengthSamples > 0;
    });

    // per-block gain smoothing coefficient (~5 ms time constant)
    const float smoothing = (float) std::exp (-1.0 / (0.005 * juce::jmax (1.0, sampleRate)));

    for (int sample = 0; sample < numSamples; ++sample)
    {
        auto loopLeft = 0.0f;
        auto loopRight = 0.0f;

        for (auto& layer : layers)
        {
            if (layer.lengthSamples <= 0)
                continue;

            const bool audible = anySolo ? layer.solo : ! layer.muted;
            const auto targetLeft = audible ? panLeftGain (layer.pan) * layer.volume : 0.0f;
            const auto targetRight = audible ? panRightGain (layer.pan) * layer.volume : 0.0f;
            layer.smoothedLeftGain = targetLeft + (layer.smoothedLeftGain - targetLeft) * smoothing;
            layer.smoothedRightGain = targetRight + (layer.smoothedRightGain - targetRight) * smoothing;

            // skip the read only once the layer is inaudible and fully ramped
            if (! audible && layer.smoothedLeftGain < 1.0e-5f && layer.smoothedRightGain < 1.0e-5f)
                continue;

            const auto start = regionStart (layer);
            const auto end = regionEnd (layer);
            const auto length = regionLength (layer);
            const auto phase = (layer.playhead - start + sample) % length;
            const auto position = layer.playbackReverse ? end - 1 - phase : start + phase;
            const auto fadeGain = loopFadeGain (layer, phase);
            loopLeft += layer.audio.getSample (0, position) * layer.smoothedLeftGain * fadeGain;
            loopRight += layer.audio.getSample (1, position) * layer.smoothedRightGain * fadeGain;
        }

        left[sample] = left[sample] * inputMonitor + loopLeft * loopLevel;
        right[sample] = right[sample] * inputMonitor + loopRight * loopLevel;
    }

    // advance every non-empty layer so muted layers stay in time (and a
    // just-muted layer resumes at the right position when unmuted)
    for (auto& layer : layers)
    {
        if (layer.lengthSamples <= 0)
            continue;
        const auto start = regionStart (layer);
        const auto length = regionLength (layer);
        layer.playhead = start + ((layer.playhead - start + numSamples) % length);
    }

    for (int channel = 2; channel < buffer.getNumChannels(); ++channel)
        buffer.clear (channel, 0, numSamples);
}

void OramAudioCore::selectLayer (int oneBasedLayer)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    selectedLayerIndex = juce::jlimit (0, maxLayers - 1, oneBasedLayer - 1);
}

void OramAudioCore::startRecordingSelected (bool shouldOverdub)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);

    recordingLayerIndex = selectedLayerIndex;
    recordWritePosition = 0;
    overdub = shouldOverdub;

    auto& layer = layers[(size_t) selectedLayerIndex];
    if (! overdub)
    {
        layer.lengthSamples = 0;
        layer.playhead = 0;
        resetLayerMetadata (layer);
    }
}

void OramAudioCore::stopRecording()
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    recordingLayerIndex = -1;
    recordWritePosition = 0;
    overdub = false;
}

void OramAudioCore::clearSelectedLayer()
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    auto& layer = layers[(size_t) selectedLayerIndex];
    layer.lengthSamples = 0;
    layer.playhead = 0;
    resetLayerMetadata (layer);

    if (recordingLayerIndex == selectedLayerIndex)
    {
        recordingLayerIndex = -1;
        recordWritePosition = 0;
        overdub = false;
    }
}

void OramAudioCore::toggleMuteSelected()
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    auto& layer = layers[(size_t) selectedLayerIndex];
    if (layer.lengthSamples > 0)
        layer.muted = ! layer.muted;
}

void OramAudioCore::toggleSoloSelected()
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    auto& layer = layers[(size_t) selectedLayerIndex];
    if (layer.lengthSamples <= 0)
        return;

    const auto wasSolo = layer.solo;
    for (auto& other : layers)
        other.solo = false;
    layer.solo = ! wasSolo;
}

void OramAudioCore::setSelectedVolume (float volume)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    layers[(size_t) selectedLayerIndex].volume = juce::jlimit (0.0f, 2.0f, volume);
}

void OramAudioCore::setSelectedPan (float pan)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    layers[(size_t) selectedLayerIndex].pan = juce::jlimit (-1.0f, 1.0f, pan);
}

void OramAudioCore::setSelectedLoopRegion (float startPct, float endPct, bool enabled)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    auto& layer = layers[(size_t) selectedLayerIndex];
    if (layer.lengthSamples <= 0)
        return;

    auto start = juce::roundToInt (juce::jlimit (0.0f, 100.0f, startPct) * 0.01f * (float) layer.lengthSamples);
    auto end = juce::roundToInt (juce::jlimit (0.0f, 100.0f, endPct) * 0.01f * (float) layer.lengthSamples);
    start = juce::jlimit (0, juce::jmax (0, layer.lengthSamples - 1), start);
    end = juce::jlimit (start + 1, layer.lengthSamples, end);
    layer.loopStart = start;
    layer.loopEnd = end;
    layer.loopEnabled = enabled;
    clampLoopFades (layer);
    layer.playhead = regionStart (layer);
}

void OramAudioCore::setSelectedLoopFades (float fadeInPct, float fadeOutPct)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    auto& layer = layers[(size_t) selectedLayerIndex];
    if (layer.lengthSamples <= 0)
        return;

    layer.loopFadeIn = juce::roundToInt (juce::jlimit (0.0f, 100.0f, fadeInPct) * 0.01f * (float) layer.lengthSamples);
    layer.loopFadeOut = juce::roundToInt (juce::jlimit (0.0f, 100.0f, fadeOutPct) * 0.01f * (float) layer.lengthSamples);
    if (! layer.loopEnabled)
    {
        layer.loopStart = 0;
        layer.loopEnd = layer.lengthSamples;
        layer.loopEnabled = true;
    }
    clampLoopFades (layer);
}

void OramAudioCore::setSelectedPlaybackReverse (bool enabled)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    auto& layer = layers[(size_t) selectedLayerIndex];
    if (layer.lengthSamples > 0)
        layer.playbackReverse = enabled;
}

bool OramAudioCore::selectedPlaybackReverse() const
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    return layers[(size_t) selectedLayerIndex].playbackReverse;
}

void OramAudioCore::reverseSelected()
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    auto& layer = layers[(size_t) selectedLayerIndex];
    if (layer.lengthSamples <= 0)
        return;

    for (int channel = 0; channel < 2; ++channel)
    {
        auto* data = layer.audio.getWritePointer (channel);
        std::reverse (data, data + layer.lengthSamples);
    }
    layer.playhead = 0;
}

void OramAudioCore::changeSelectedSpeed (float speed)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    auto& layer = layers[(size_t) selectedLayerIndex];
    if (layer.lengthSamples <= 1)
        return;

    speed = juce::jlimit (0.25f, 4.0f, speed);
    const auto newLength = juce::jlimit (1, maxRecordSamples, juce::roundToInt ((float) layer.lengthSamples / speed));
    juce::AudioBuffer<float> resampled (2, newLength);
    for (int channel = 0; channel < 2; ++channel)
    {
        const auto* src = layer.audio.getReadPointer (channel);
        auto* dst = resampled.getWritePointer (channel);
        for (int i = 0; i < newLength; ++i)
        {
            const auto position = juce::jlimit (0.0f, (float) layer.lengthSamples - 1.0f, (float) i * speed);
            const auto index = (int) position;
            const auto frac = position - (float) index;
            const auto next = juce::jmin (index + 1, layer.lengthSamples - 1);
            dst[i] = src[index] + (src[next] - src[index]) * frac;
        }
    }

    layer.audio.clear();
    layer.audio.copyFrom (0, 0, resampled, 0, 0, newLength);
    layer.audio.copyFrom (1, 0, resampled, 1, 0, newLength);
    layer.lengthSamples = newLength;
    layer.playhead = 0;
    layer.loopEnabled = false;
    layer.loopFadeIn = 0;
    layer.loopFadeOut = 0;
}

void OramAudioCore::filterSelected (bool highpass, float cutoffHz)
{
    juce::AudioBuffer<float> work;
    int length = 0;
    const double sr = sampleRate;
    if (! snapshotSelectedAudio (work, length) || sr <= 0.0)
        return;

    // second-order Butterworth biquad (RBJ), zero-phase via forward+reverse
    // passes — a real 12→24 dB/oct filter instead of the old 6 dB/oct one-pole
    cutoffHz = juce::jlimit (20.0f, (float) sr * 0.45f, cutoffHz);
    const double w0 = juce::MathConstants<double>::twoPi * (double) cutoffHz / sr;
    const double cosw0 = std::cos (w0);
    const double sinw0 = std::sin (w0);
    const double q = juce::MathConstants<double>::sqrt2 * 0.5; // Butterworth Q
    const double alpha = sinw0 / (2.0 * q);

    double b0, b1, b2;
    if (highpass)
    {
        b0 = (1.0 + cosw0) * 0.5;
        b1 = -(1.0 + cosw0);
        b2 = (1.0 + cosw0) * 0.5;
    }
    else
    {
        b0 = (1.0 - cosw0) * 0.5;
        b1 = 1.0 - cosw0;
        b2 = (1.0 - cosw0) * 0.5;
    }
    const double a0 = 1.0 + alpha;
    const double a1 = -2.0 * cosw0;
    const double a2 = 1.0 - alpha;
    const float nb0 = (float) (b0 / a0), nb1 = (float) (b1 / a0), nb2 = (float) (b2 / a0);
    const float na1 = (float) (a1 / a0), na2 = (float) (a2 / a0);
    const float gainComp = highpass ? 0.85f : 1.0f;

    auto biquadPass = [&] (float* data, int n)
    {
        float x1 = 0, x2 = 0, y1 = 0, y2 = 0;
        for (int i = 0; i < n; ++i)
        {
            const float x0 = data[i];
            const float y0 = nb0 * x0 + nb1 * x1 + nb2 * x2 - na1 * y1 - na2 * y2;
            x2 = x1; x1 = x0; y2 = y1; y1 = y0;
            data[i] = y0;
        }
    };

    for (int channel = 0; channel < 2; ++channel)
    {
        auto* data = work.getWritePointer (channel);
        biquadPass (data, length);
        std::reverse (data, data + length);
        biquadPass (data, length);           // second pass → zero-phase, 24 dB/oct
        std::reverse (data, data + length);
        if (gainComp != 1.0f)
            juce::FloatVectorOperations::multiply (data, gainComp, length);
    }

    writeBackSelectedAudio (work, length);
}

void OramAudioCore::reverbSelected (float wet)
{
    juce::AudioBuffer<float> work;
    int length = 0;
    const double sr = sampleRate;
    if (! snapshotSelectedAudio (work, length) || sr <= 0.0)
        return;

    wet = juce::jlimit (0.0f, 1.0f, wet);

    // Schroeder network: 4 parallel feedback combs + 2 series allpasses,
    // per channel, right channel detuned for stereo width. A real tail
    // instead of a single slapback echo.
    const double scale = sr / 44100.0;
    const int combBase[4] = { 1116, 1188, 1277, 1356 };
    const int apBase[2] = { 556, 441 };
    const float combFb = 0.78f;
    const float apGain = 0.5f;

    std::vector<float> line;
    for (int channel = 0; channel < 2; ++channel)
    {
        auto* data = work.getWritePointer (channel);
        const int spread = channel == 1 ? 23 : 0;

        std::vector<float> combSum ((size_t) length, 0.0f);
        for (int c = 0; c < 4; ++c)
        {
            const int d = juce::jmax (1, (int) std::lround ((combBase[c] + spread) * scale));
            line.assign ((size_t) d, 0.0f);
            int idx = 0;
            for (int i = 0; i < length; ++i)
            {
                const float y = data[i] + combFb * line[(size_t) idx];
                line[(size_t) idx] = y;
                combSum[(size_t) i] += y * 0.25f;
                if (++idx >= d) idx = 0;
            }
        }

        for (int a = 0; a < 2; ++a)
        {
            const int d = juce::jmax (1, (int) std::lround ((apBase[a] + spread) * scale));
            line.assign ((size_t) d, 0.0f);
            int idx = 0;
            for (int i = 0; i < length; ++i)
            {
                const float bufOut = line[(size_t) idx];
                const float in = combSum[(size_t) i];
                const float y = -apGain * in + bufOut;
                line[(size_t) idx] = in + apGain * y;
                combSum[(size_t) i] = y;
                if (++idx >= d) idx = 0;
            }
        }

        // equal-power dry/wet
        const float theta = wet * juce::MathConstants<float>::halfPi;
        const float dryGain = std::cos (theta);
        const float wetGain = std::sin (theta);
        for (int i = 0; i < length; ++i)
            data[i] = data[i] * dryGain + combSum[(size_t) i] * wetGain;
    }

    writeBackSelectedAudio (work, length);
}

void OramAudioCore::fadeSelected (bool fadeIn, double seconds)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    auto& layer = layers[(size_t) selectedLayerIndex];
    if (layer.lengthSamples <= 0)
        return;

    const auto fadeSamples = juce::jlimit (1, layer.lengthSamples, juce::roundToInt (seconds * sampleRate));
    for (int sample = 0; sample < fadeSamples; ++sample)
    {
        const auto gain = fadeIn
            ? (float) sample / (float) juce::jmax (1, fadeSamples - 1)
            : 1.0f - ((float) sample / (float) juce::jmax (1, fadeSamples - 1));
        const auto position = fadeIn ? sample : layer.lengthSamples - fadeSamples + sample;
        layer.audio.setSample (0, position, layer.audio.getSample (0, position) * gain);
        layer.audio.setSample (1, position, layer.audio.getSample (1, position) * gain);
    }
}

void OramAudioCore::trimSelected (bool trimStart, double seconds)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    auto& layer = layers[(size_t) selectedLayerIndex];
    if (layer.lengthSamples <= 0)
        return;

    const auto trimSamples = juce::jlimit (1, layer.lengthSamples, juce::roundToInt (seconds * sampleRate));
    const auto newLength = layer.lengthSamples - trimSamples;
    if (newLength <= 0)
    {
        layer.lengthSamples = 0;
        layer.playhead = 0;
        return;
    }

    if (trimStart)
    {
        for (int channel = 0; channel < 2; ++channel)
            layer.audio.copyFrom (channel, 0, layer.audio, channel, trimSamples, newLength);
    }

    layer.lengthSamples = newLength;
    layer.playhead = 0;
    layer.loopEnabled = false;
    layer.loopStart = 0;
    layer.loopEnd = 0;
    layer.loopFadeIn = 0;
    layer.loopFadeOut = 0;
}

void OramAudioCore::silenceAll()
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    recordingLayerIndex = -1;
    recordWritePosition = 0;
    overdub = false;

    for (auto& layer : layers)
    {
        layer.playhead = 0;
        layer.solo = false;
        if (layer.lengthSamples > 0)
            layer.muted = true;
    }
}

void OramAudioCore::resetAll()
{
    reset();
}

int OramAudioCore::loadAudioToFirstEmpty (const juce::AudioBuffer<float>& source, double sourceSampleRate)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);

    auto target = std::find_if (layers.begin(), layers.end(), [] (const Layer& layer)
    {
        return layer.lengthSamples <= 0;
    });

    if (target == layers.end())
        target = layers.begin() + selectedLayerIndex;

    const auto targetIndex = (int) std::distance (layers.begin(), target);
    const auto sourceSamples = source.getNumSamples();
    if (sourceSamples <= 0)
        return targetIndex + 1;

    const auto needsResample = sourceSampleRate > 0.0 && std::abs (sourceSampleRate - sampleRate) > 1.0;
    const auto targetSamples = needsResample
        ? juce::jmin (maxRecordSamples, juce::roundToInt ((double) sourceSamples * sampleRate / sourceSampleRate))
        : juce::jmin (maxRecordSamples, sourceSamples);

    target->audio.clear();

    if (needsResample)
    {
        juce::LagrangeInterpolator leftInterpolator;
        juce::LagrangeInterpolator rightInterpolator;
        const auto ratio = sourceSampleRate / sampleRate;
        leftInterpolator.process (ratio, source.getReadPointer (0), target->audio.getWritePointer (0), targetSamples);
        rightInterpolator.process (
            ratio,
            source.getReadPointer (source.getNumChannels() > 1 ? 1 : 0),
            target->audio.getWritePointer (1),
            targetSamples);
    }
    else
    {
        target->audio.copyFrom (0, 0, source, 0, 0, targetSamples);
        target->audio.copyFrom (1, 0, source, source.getNumChannels() > 1 ? 1 : 0, 0, targetSamples);
    }

    target->lengthSamples = targetSamples;
    target->playhead = 0;
    target->muted = false;
    target->loopEnabled = false;
    target->loopStart = 0;
    target->loopEnd = 0;
    target->loopFadeIn = 0;
    target->loopFadeOut = 0;
    target->playbackReverse = false;
    return targetIndex + 1;
}

std::array<OramAudioCore::LayerView, OramAudioCore::maxLayers> OramAudioCore::snapshot() const
{
    const juce::SpinLock::ScopedLockType lock (stateLock);

    std::array<LayerView, maxLayers> result;
    for (int i = 0; i < maxLayers; ++i)
    {
        const auto& layer = layers[(size_t) i];
        auto& view = result[(size_t) i];
        view.slot = i + 1;
        view.empty = layer.lengthSamples <= 0;
        view.muted = layer.muted;
        view.solo = layer.solo;
        view.recording = recordingLayerIndex == i;
        view.volume = layer.volume;
        view.pan = layer.pan;
        view.playbackReverse = layer.playbackReverse;
        view.durationSeconds = sampleRate > 0.0 ? (double) layer.lengthSamples / sampleRate : 0.0;
        view.loopEnabled = layer.loopEnabled;
        view.loopStartPct = layer.lengthSamples > 0 ? (double) regionStart (layer) / (double) layer.lengthSamples * 100.0 : 0.0;
        view.loopEndPct = layer.lengthSamples > 0 ? (double) regionEnd (layer) / (double) layer.lengthSamples * 100.0 : 100.0;
        view.loopFadeInPct = layer.lengthSamples > 0 ? (double) layer.loopFadeIn / (double) layer.lengthSamples * 100.0 : 0.0;
        view.loopFadeOutPct = layer.lengthSamples > 0 ? (double) layer.loopFadeOut / (double) layer.lengthSamples * 100.0 : 0.0;
    }
    return result;
}

void OramAudioCore::writeStateToStream (juce::OutputStream& stream) const
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    stream.writeInt ((int) stateMagic);
    stream.writeInt ((int) stateVersion);
    stream.writeDouble (sampleRate);
    stream.writeInt (selectedLayerIndex);
    stream.writeInt (recordingLayerIndex);

    for (const auto& layer : layers)
    {
        stream.writeInt (layer.lengthSamples);
        stream.writeInt (layer.playhead);
        stream.writeFloat (layer.volume);
        stream.writeFloat (layer.pan);
        stream.writeBool (layer.muted);
        stream.writeBool (layer.solo);
        stream.writeBool (layer.loopEnabled);
        stream.writeInt (layer.loopStart);
        stream.writeInt (layer.loopEnd);
        stream.writeInt (layer.loopFadeIn);
        stream.writeInt (layer.loopFadeOut);
        stream.writeBool (layer.playbackReverse);
        if (layer.lengthSamples > 0)
        {
            std::vector<float> silence ((size_t) layer.lengthSamples, 0.0f);
            for (int channel = 0; channel < 2; ++channel)
            {
                const auto hasChannel = channel < layer.audio.getNumChannels()
                    && layer.audio.getNumSamples() >= layer.lengthSamples;
                const auto* data = hasChannel ? layer.audio.getReadPointer (channel) : silence.data();
                stream.write (data, (size_t) layer.lengthSamples * sizeof (float));
            }
        }
    }
}

bool OramAudioCore::readStateFromStream (juce::InputStream& stream)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    if ((uint32_t) stream.readInt() != stateMagic)
        return false;
    const auto version = (uint32_t) stream.readInt();
    if (version < 1 || version > stateVersion)
        return false;

    sampleRate = stream.readDouble();
    // guard against a corrupt sample rate driving an absurd capacity below
    if (! (sampleRate > 0.0 && sampleRate <= 768000.0))
        sampleRate = 44100.0;
    selectedLayerIndex = juce::jlimit (0, maxLayers - 1, stream.readInt());
    recordingLayerIndex = -1;
    (void) stream.readInt();

    // absolute per-layer sample ceiling: never trust a length from the stream
    // that would force a multi-gigabyte allocation (corrupt / truncated state).
    const int recordCeiling = maxRecordSamples > 0
        ? maxRecordSamples
        : juce::roundToInt (sampleRate * maxRecordSeconds);
    const int hardCeiling = juce::jmax (recordCeiling, juce::roundToInt (768000.0 * maxRecordSeconds));

    for (auto& layer : layers)
    {
        int length = stream.readInt();
        if (length < 0)
            length = 0;
        if (length > hardCeiling)
            return false; // implausible payload — reject rather than allocate
        // there must be at least `length` floats × 2 channels left to read
        const int64_t remaining = stream.getTotalLength() - stream.getPosition();
        const int64_t audioBytesNeeded = (int64_t) length * 2 * (int64_t) sizeof (float);

        ensureLayerCapacity (layer, juce::jmax (1, length));
        layer.lengthSamples = length;
        const auto playhead = stream.readInt();
        layer.playhead = length > 0 ? playhead % length : 0;
        layer.volume = stream.readFloat();
        layer.pan = stream.readFloat();
        layer.muted = stream.readBool();
        layer.solo = stream.readBool();
        layer.loopEnabled = stream.readBool();
        layer.loopStart = stream.readInt();
        layer.loopEnd = stream.readInt();
        if (version >= 2)
        {
            layer.loopFadeIn = stream.readInt();
            layer.loopFadeOut = stream.readInt();
            layer.playbackReverse = stream.readBool();
        }
        else
        {
            layer.loopFadeIn = 0;
            layer.loopFadeOut = 0;
            layer.playbackReverse = false;
        }
        clampLoopFades (layer);

        if (remaining >= 0 && audioBytesNeeded > 0 && stream.getTotalLength() > 0
            && (stream.getTotalLength() - stream.getPosition()) < audioBytesNeeded)
        {
            // truncated payload: zero the layer instead of reading garbage
            layer.audio.clear();
            layer.lengthSamples = 0;
            return false;
        }
        for (int channel = 0; channel < 2; ++channel)
        {
            auto* dest = layer.audio.getWritePointer (channel);
            const auto got = stream.read (dest, (size_t) length * sizeof (float));
            if (got < (int) ((size_t) length * sizeof (float)))
            {
                layer.audio.clear();
                layer.lengthSamples = 0;
                return false;
            }
        }
    }
    return true;
}

OramAudioCore::Layer* OramAudioCore::currentRecordingLayer() noexcept
{
    if (recordingLayerIndex < 0 || recordingLayerIndex >= maxLayers)
        return nullptr;
    return &layers[(size_t) recordingLayerIndex];
}

const OramAudioCore::Layer* OramAudioCore::currentRecordingLayer() const noexcept
{
    if (recordingLayerIndex < 0 || recordingLayerIndex >= maxLayers)
        return nullptr;
    return &layers[(size_t) recordingLayerIndex];
}

void OramAudioCore::ensureLayerCapacity (Layer& layer, int requiredSamples)
{
    const auto capacity = juce::jmax (requiredSamples, maxRecordSamples > 0 ? maxRecordSamples : requiredSamples);
    if (layer.audio.getNumSamples() < capacity)
        layer.audio.setSize (2, capacity, true, true, true);
}

bool OramAudioCore::snapshotSelectedAudio (juce::AudioBuffer<float>& dest, int& lengthOut)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    auto& layer = layers[(size_t) selectedLayerIndex];
    if (layer.lengthSamples <= 0)
        return false;
    lengthOut = layer.lengthSamples;
    dest.setSize (2, lengthOut, false, false, true);
    for (int ch = 0; ch < 2; ++ch)
        dest.copyFrom (ch, 0, layer.audio, ch, 0, lengthOut);
    return true;
}

void OramAudioCore::writeBackSelectedAudio (const juce::AudioBuffer<float>& src, int length)
{
    const juce::SpinLock::ScopedLockType lock (stateLock);
    auto& layer = layers[(size_t) selectedLayerIndex];
    ensureLayerCapacity (layer, juce::jmax (1, length));
    const int n = juce::jmin (length, src.getNumSamples());
    for (int ch = 0; ch < 2; ++ch)
        layer.audio.copyFrom (ch, 0, src, ch, 0, n);
    layer.lengthSamples = n;
    if (layer.playhead >= n)
        layer.playhead = 0;
}

int OramAudioCore::regionStart (const Layer& layer) noexcept
{
    if (! layer.loopEnabled || layer.lengthSamples <= 0)
        return 0;
    return juce::jlimit (0, juce::jmax (0, layer.lengthSamples - 1), layer.loopStart);
}

int OramAudioCore::regionEnd (const Layer& layer) noexcept
{
    if (! layer.loopEnabled || layer.lengthSamples <= 0)
        return layer.lengthSamples;
    return juce::jlimit (regionStart (layer) + 1, layer.lengthSamples, layer.loopEnd > 0 ? layer.loopEnd : layer.lengthSamples);
}

int OramAudioCore::regionLength (const Layer& layer) noexcept
{
    return juce::jmax (1, regionEnd (layer) - regionStart (layer));
}

float OramAudioCore::loopFadeGain (const Layer& layer, int phase) noexcept
{
    const auto length = regionLength (layer);
    auto gain = 1.0f;
    if (layer.loopFadeIn > 0 && phase < layer.loopFadeIn)
        gain = juce::jmin (gain, (float) phase / (float) juce::jmax (1, layer.loopFadeIn));
    if (layer.loopFadeOut > 0)
    {
        const auto fadeOutStart = length - layer.loopFadeOut;
        if (phase >= fadeOutStart)
            gain = juce::jmin (gain, (float) juce::jmax (0, length - phase - 1) / (float) juce::jmax (1, layer.loopFadeOut));
    }
    return juce::jlimit (0.0f, 1.0f, gain);
}

void OramAudioCore::clampLoopFades (Layer& layer) noexcept
{
    const auto length = regionLength (layer);
    layer.loopFadeIn = juce::jlimit (0, length, layer.loopFadeIn);
    layer.loopFadeOut = juce::jlimit (0, length, layer.loopFadeOut);
    if (layer.loopFadeIn + layer.loopFadeOut > length)
    {
        const auto excess = layer.loopFadeIn + layer.loopFadeOut - length;
        if (layer.loopFadeOut >= excess)
            layer.loopFadeOut -= excess;
        else
        {
            layer.loopFadeIn = juce::jmax (0, layer.loopFadeIn - (excess - layer.loopFadeOut));
            layer.loopFadeOut = 0;
        }
    }
}

void OramAudioCore::resetLayerMetadata (Layer& layer) noexcept
{
    layer.muted = false;
    layer.solo = false;
    layer.loopEnabled = false;
    layer.loopStart = 0;
    layer.loopEnd = 0;
    layer.loopFadeIn = 0;
    layer.loopFadeOut = 0;
    layer.playbackReverse = false;
    layer.volume = 1.0f;
    layer.pan = 0.0f;
}

float OramAudioCore::panLeftGain (float pan) noexcept
{
    const auto theta = (juce::jlimit (-1.0f, 1.0f, pan) + 1.0f) * juce::MathConstants<float>::pi * 0.25f;
    return std::cos (theta);
}

float OramAudioCore::panRightGain (float pan) noexcept
{
    const auto theta = (juce::jlimit (-1.0f, 1.0f, pan) + 1.0f) * juce::MathConstants<float>::pi * 0.25f;
    return std::sin (theta);
}
