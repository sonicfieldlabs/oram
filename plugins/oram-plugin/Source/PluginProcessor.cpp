#include "PluginProcessor.h"

#include "PluginEditor.h"

#include <cmath>
#include <utility>
#include <vector>

namespace
{
constexpr uint32_t processorStateMagic = 0x4f525053; // ORPS
constexpr uint32_t processorStateVersion = 1;
}

class OramAudioProcessor::GenerateJob final : public juce::ThreadPoolJob
{
public:
    GenerateJob (
        OramAudioProcessor& owner,
        juce::String promptText,
        juce::String providerName,
        juce::String modelName,
        double duration)
        : juce::ThreadPoolJob ("ORAM generate"),
          processor (owner),
          prompt (std::move (promptText)),
          provider (std::move (providerName)),
          model (std::move (modelName)),
          durationSeconds (duration)
    {
    }

    JobStatus runJob() override
    {
        processor.setStatus ("generating...");
        auto result = processor.daemonClient.pluginGenerate (prompt, durationSeconds, provider, model);
        if (shouldExit())
            return jobHasFinished;

        if (! result.ok)
        {
            processor.setStatus (result.message.isNotEmpty() ? result.message : "generation failed");
            return jobHasFinished;
        }

        auto assignedLayer = 0;
        if (processor.importAudioFile (result.path, assignedLayer))
            processor.setStatus ("generated " + result.soundId + " -> layer " + juce::String (assignedLayer));
        else
            processor.setStatus ("generated but import failed: " + result.path);

        return jobHasFinished;
    }

private:
    OramAudioProcessor& processor;
    juce::String prompt;
    juce::String provider;
    juce::String model;
    double durationSeconds = 8.0;
};

class OramAudioProcessor::CommandJob final : public juce::ThreadPoolJob
{
public:
    CommandJob (OramAudioProcessor& owner, juce::String commandText)
        : juce::ThreadPoolJob ("ORAM command"), processor (owner), command (std::move (commandText))
    {
    }

    JobStatus runJob() override
    {
        processor.setStatus ("parsing command...");
        auto response = processor.daemonClient.parseCommand (command);
        if (shouldExit())
            return jobHasFinished;

        auto action = response.getProperty ("action", juce::var());
        if (! action.isObject())
        {
            processor.setStatus ("command parse failed");
            return jobHasFinished;
        }

        if (! processor.applyAction (action))
            processor.setStatus ("unsupported command for plugin");

        return jobHasFinished;
    }

private:
    OramAudioProcessor& processor;
    juce::String command;
};

OramAudioProcessor::OramAudioProcessor()
    : AudioProcessor (BusesProperties()
        .withInput ("Input", juce::AudioChannelSet::stereo(), true)
        .withOutput ("Output", juce::AudioChannelSet::stereo(), true)),
      parameterState (*this, nullptr, "ORAM", createParameterLayout())
{
    formatManager.registerBasicFormats();
    if (daemonClient.loadMetadata())
        setStatus ("daemon connected");
}

OramAudioProcessor::~OramAudioProcessor()
{
    backgroundPool.removeAllJobs (true, 5000);
}

juce::AudioProcessorValueTreeState::ParameterLayout OramAudioProcessor::createParameterLayout()
{
    std::vector<std::unique_ptr<juce::RangedAudioParameter>> params;
    params.push_back (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { "input_monitor", 1 },
        "Input Monitor",
        juce::NormalisableRange<float> (0.0f, 1.0f, 0.01f),
        1.0f));
    params.push_back (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { "loop_level", 1 },
        "Loop Level",
        juce::NormalisableRange<float> (0.0f, 2.0f, 0.01f),
        1.0f));
    return { params.begin(), params.end() };
}

juce::String OramAudioProcessor::generationModelForProvider (const juce::String& provider)
{
    if (provider == "elevenlabs")
        return "elevenlabs-sfx";
    if (provider == "stability")
        return "stability-stable-audio-3";
    if (provider == "local")
        return "stable-audio-3-local";
    return "auto";
}

void OramAudioProcessor::prepareToPlay (double sampleRate, int samplesPerBlock)
{
    core.prepare (sampleRate, samplesPerBlock, getTotalNumOutputChannels());
}

void OramAudioProcessor::releaseResources() {}

bool OramAudioProcessor::isBusesLayoutSupported (const BusesLayout& layouts) const
{
    const auto mainOut = layouts.getMainOutputChannelSet();
    const auto mainIn = layouts.getMainInputChannelSet();
    if (mainIn.isDisabled() || mainIn != mainOut)
        return false;

    return mainOut == juce::AudioChannelSet::mono()
        || mainOut == juce::AudioChannelSet::stereo();
}

void OramAudioProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer&)
{
    juce::ScopedNoDenormals noDenormals;

    const auto totalInputChannels = getTotalNumInputChannels();
    const auto totalOutputChannels = getTotalNumOutputChannels();
    for (auto channel = totalInputChannels; channel < totalOutputChannels; ++channel)
        buffer.clear (channel, 0, buffer.getNumSamples());

    const auto inputMonitor = parameterState.getRawParameterValue ("input_monitor")->load();
    const auto loopLevel = parameterState.getRawParameterValue ("loop_level")->load();
    core.process (buffer, inputMonitor, loopLevel);
}

juce::AudioProcessorEditor* OramAudioProcessor::createEditor()
{
    return new OramAudioProcessorEditor (*this);
}

void OramAudioProcessor::getStateInformation (juce::MemoryBlock& destData)
{
    juce::MemoryOutputStream stream (destData, false);
    stream.writeInt ((int) processorStateMagic);
    stream.writeInt ((int) processorStateVersion);
    auto xmlText = juce::String();
    if (auto xml = parameterState.copyState().createXml())
        xmlText = xml->toString();
    stream.writeString (xmlText);
    core.writeStateToStream (stream);
}

void OramAudioProcessor::setStateInformation (const void* data, int sizeInBytes)
{
    juce::MemoryInputStream stream (data, (size_t) sizeInBytes, false);
    if ((uint32_t) stream.readInt() != processorStateMagic)
    {
        if (auto xml = getXmlFromBinary (data, sizeInBytes))
            if (xml->hasTagName (parameterState.state.getType()))
                parameterState.replaceState (juce::ValueTree::fromXml (*xml));
        return;
    }

    if ((uint32_t) stream.readInt() != processorStateVersion)
        return;

    const auto xmlText = stream.readString();
    if (auto xml = juce::parseXML (xmlText))
        if (xml->hasTagName (parameterState.state.getType()))
            parameterState.replaceState (juce::ValueTree::fromXml (*xml));

    core.readStateFromStream (stream);
}

void OramAudioProcessor::selectLayer (int oneBasedLayer)
{
    core.selectLayer (oneBasedLayer);
}

void OramAudioProcessor::startRecordingSelected (bool shouldOverdub)
{
    core.startRecordingSelected (shouldOverdub);
    setStatus (shouldOverdub ? "overdubbing layer " + juce::String (core.selectedLayer())
                             : "recording layer " + juce::String (core.selectedLayer()));
}

void OramAudioProcessor::stopRecording()
{
    core.stopRecording();
    setStatus ("recording stopped");
}

void OramAudioProcessor::clearSelectedLayer()
{
    core.clearSelectedLayer();
    setStatus ("cleared layer " + juce::String (core.selectedLayer()));
}

void OramAudioProcessor::requestGenerate (
    const juce::String& prompt,
    const juce::String& provider,
    const juce::String& model,
    double durationSeconds)
{
    auto trimmed = prompt.trim();
    if (trimmed.isEmpty())
        return;

    const auto providerName = provider.isNotEmpty() ? provider : "auto";
    const auto modelName = model.isNotEmpty() ? model : generationModelForProvider (providerName);
    backgroundPool.addJob (
        new GenerateJob (*this, trimmed, providerName, modelName, juce::jlimit (0.5, 120.0, durationSeconds)),
        true);
}

void OramAudioProcessor::requestCommand (const juce::String& text)
{
    auto trimmed = text.trim();
    if (trimmed.isEmpty())
        return;

    backgroundPool.addJob (new CommandJob (*this, trimmed), true);
}

juce::String OramAudioProcessor::status() const
{
    const juce::ScopedLock lock (statusLock);
    return statusText;
}

void OramAudioProcessor::setStatus (const juce::String& message)
{
    const juce::ScopedLock lock (statusLock);
    statusText = message;
}

bool OramAudioProcessor::applyAction (const juce::var& action)
{
    const auto actionName = action.getProperty ("action", juce::var()).toString();
    const auto target = targetLayerFromAction (action);
    if (target > 0)
        core.selectLayer (target);

    if (actionName == "select_layer")
    {
        setStatus ("selected layer " + juce::String (core.selectedLayer()));
        return true;
    }

    if (actionName == "record")
    {
        startRecordingSelected (false);
        return true;
    }

    if (actionName == "overdub")
    {
        startRecordingSelected (true);
        return true;
    }

    if (actionName == "stop_recording")
    {
        stopRecording();
        return true;
    }

    if (actionName == "clear_layer")
    {
        clearSelectedLayer();
        return true;
    }

    if (actionName == "mute_layer")
    {
        core.toggleMuteSelected();
        setStatus ("toggled mute on layer " + juce::String (core.selectedLayer()));
        return true;
    }

    if (actionName == "solo_layer")
    {
        core.toggleSoloSelected();
        setStatus ("toggled solo on layer " + juce::String (core.selectedLayer()));
        return true;
    }

    if (actionName == "set_volume")
    {
        core.setSelectedVolume ((float) (double) action.getProperty ("volume", juce::var (1.0)));
        setStatus ("set volume on layer " + juce::String (core.selectedLayer()));
        return true;
    }

    if (actionName == "set_pan")
    {
        core.setSelectedPan ((float) (double) action.getProperty ("pan", juce::var (0.0)));
        setStatus ("set pan on layer " + juce::String (core.selectedLayer()));
        return true;
    }

    if (actionName == "kill_audio")
    {
        core.silenceAll();
        setStatus ("killed plugin audio");
        return true;
    }

    if (actionName == "generate_layer")
    {
        const auto provider = action.getProperty ("provider", juce::var()).toString();
        const auto engine = action.getProperty ("engine", juce::var()).toString();
        const auto duration = (double) action.getProperty ("duration", juce::var (8.0));
        requestGenerate (action.getProperty ("prompt", juce::var()).toString(), provider, engine, duration);
        return true;
    }

    if (actionName == "set_loop_region")
    {
        const auto startPct = (float) (double) action.getProperty ("start_pct", juce::var (0.0));
        const auto endPct = (float) (double) action.getProperty ("end_pct", juce::var (100.0));
        const auto enabled = (bool) action.getProperty ("enabled", juce::var (true));
        core.setSelectedLoopRegion (startPct, endPct, enabled);
        setStatus (enabled ? "set loop region" : "cleared loop region");
        return true;
    }

    if (actionName == "apply_effect")
    {
        const auto effect = action.getProperty ("effect", juce::var()).toString();
        const auto parameters = action.getProperty ("parameters", juce::var());

        if (effect == "reverse")
        {
            core.reverseSelected();
            setStatus ("reversed layer " + juce::String (core.selectedLayer()));
            return true;
        }
        if (effect == "speed" || effect == "pitch")
        {
            auto speed = (float) (double) parameters.getProperty ("speed", juce::var (1.0));
            if (effect == "pitch")
            {
                const auto semitones = (double) parameters.getProperty ("semitones", juce::var (0.0));
                speed = (float) std::pow (2.0, semitones / 12.0);
            }
            core.changeSelectedSpeed (speed);
            setStatus (effect + " layer " + juce::String (core.selectedLayer()));
            return true;
        }
        if (effect == "lowpass" || effect == "highpass")
        {
            const auto cutoff = (float) (double) parameters.getProperty ("cutoff_hz", juce::var (effect == "lowpass" ? 2000.0 : 4000.0));
            core.filterSelected (effect == "highpass", cutoff);
            setStatus (effect + " layer " + juce::String (core.selectedLayer()));
            return true;
        }
        if (effect == "reverb" || effect == "spatial_far")
        {
            const auto wet = (float) (double) parameters.getProperty ("wet", juce::var (effect == "spatial_far" ? 0.65 : 0.4));
            core.reverbSelected (wet);
            setStatus (effect + " layer " + juce::String (core.selectedLayer()));
            return true;
        }
        if (effect == "fade_in" || effect == "fade_out")
        {
            const auto seconds = (double) parameters.getProperty ("fade_seconds", juce::var (1.0));
            core.fadeSelected (effect == "fade_in", seconds);
            setStatus (effect + " layer " + juce::String (core.selectedLayer()));
            return true;
        }
        if (effect == "trim_start" || effect == "trim_end")
        {
            core.trimSelected (effect == "trim_start", 0.25);
            setStatus (effect + " layer " + juce::String (core.selectedLayer()));
            return true;
        }

        return false;
    }

    return false;
}

int OramAudioProcessor::targetLayerFromAction (const juce::var& action) const
{
    auto target = action.getProperty ("target", juce::var());
    if (target.isInt() || target.isInt64() || target.isDouble())
        return juce::jlimit (1, OramAudioCore::maxLayers, (int) target);

    if (target.isString())
    {
        auto text = target.toString();
        if (text.containsOnly ("0123456789"))
            return juce::jlimit (1, OramAudioCore::maxLayers, text.getIntValue());
    }

    return 0;
}

bool OramAudioProcessor::importAudioFile (const juce::String& path, int& assignedLayer)
{
    auto file = juce::File (path);
    if (! file.existsAsFile())
        return false;

    std::unique_ptr<juce::AudioFormatReader> reader (formatManager.createReaderFor (file));
    if (reader == nullptr)
        return false;

    juce::AudioBuffer<float> imported ((int) reader->numChannels, (int) reader->lengthInSamples);
    reader->read (&imported, 0, (int) reader->lengthInSamples, 0, true, true);
    assignedLayer = core.loadAudioToFirstEmpty (imported, reader->sampleRate);
    return assignedLayer > 0;
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new OramAudioProcessor();
}
