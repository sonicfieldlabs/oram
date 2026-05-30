#include "PluginEditor.h"

namespace
{
constexpr auto margin = 18;
constexpr auto rowHeight = 30;

juce::Colour backgroundColour() { return juce::Colour::fromRGB (20, 21, 22); }
juce::Colour panelColour() { return juce::Colour::fromRGB (31, 33, 35); }
juce::Colour textColour() { return juce::Colour::fromRGB (232, 229, 220); }
juce::Colour mutedTextColour() { return juce::Colour::fromRGB (150, 148, 140); }
}

OramAudioProcessorEditor::OramAudioProcessorEditor (OramAudioProcessor& p)
    : AudioProcessorEditor (&p), audioProcessor (p)
{
    setSize (760, 560);

    titleLabel.setText ("ORAM", juce::dontSendNotification);
    titleLabel.setJustificationType (juce::Justification::centredLeft);
    titleLabel.setColour (juce::Label::textColourId, textColour());
    titleLabel.setFont (juce::FontOptions (26.0f, juce::Font::bold));
    addAndMakeVisible (titleLabel);

    statusLabel.setText (audioProcessor.status(), juce::dontSendNotification);
    statusLabel.setJustificationType (juce::Justification::centredRight);
    statusLabel.setColour (juce::Label::textColourId, mutedTextColour());
    addAndMakeVisible (statusLabel);

    for (int i = 1; i <= OramAudioCore::maxLayers; ++i)
        layerSelector.addItem ("Layer " + juce::String (i), i);
    layerSelector.setSelectedId (1);
    layerSelector.onChange = [this]
    {
        audioProcessor.selectLayer (layerSelector.getSelectedId());
    };
    addAndMakeVisible (layerSelector);

    recordButton.onClick = [this] { audioProcessor.startRecordingSelected (false); };
    overdubButton.onClick = [this] { audioProcessor.startRecordingSelected (true); };
    stopButton.onClick = [this] { audioProcessor.stopRecording(); };
    clearButton.onClick = [this] { audioProcessor.clearSelectedLayer(); };
    generateButton.onClick = [this]
    {
        audioProcessor.requestGenerate (
            promptEditor.getText(),
            providerSelector.getText(),
            modelSelector.getText(),
            durationSlider.getValue());
    };
    commandButton.onClick = [this] { audioProcessor.requestCommand (commandEditor.getText()); };

    for (auto* button : { &recordButton, &overdubButton, &stopButton, &clearButton, &generateButton, &commandButton })
        addAndMakeVisible (*button);

    promptEditor.setMultiLine (false);
    promptEditor.setReturnKeyStartsNewLine (false);
    promptEditor.setTextToShowWhenEmpty ("describe a sound to generate", mutedTextColour());
    promptEditor.setColour (juce::TextEditor::backgroundColourId, panelColour());
    promptEditor.setColour (juce::TextEditor::textColourId, textColour());
    promptEditor.setColour (juce::TextEditor::outlineColourId, juce::Colours::transparentBlack);
    addAndMakeVisible (promptEditor);

    providerSelector.addItem ("auto", 1);
    providerSelector.addItem ("local", 2);
    providerSelector.addItem ("elevenlabs", 3);
    providerSelector.addItem ("stability", 4);
    providerSelector.setSelectedId (1);
    providerSelector.onChange = [this]
    {
        if (providerSelector.getText() == "elevenlabs")
            modelSelector.setText ("elevenlabs-sfx", juce::dontSendNotification);
        else if (providerSelector.getText() == "stability")
            modelSelector.setText ("stability-stable-audio-3", juce::dontSendNotification);
        else if (providerSelector.getText() == "local")
            modelSelector.setText ("stable-audio-3-local", juce::dontSendNotification);
        else
            modelSelector.setText ("local-mock", juce::dontSendNotification);
    };
    addAndMakeVisible (providerSelector);

    modelSelector.addItem ("local-mock", 1);
    modelSelector.addItem ("elevenlabs-sfx", 2);
    modelSelector.addItem ("stable-audio-3-local", 3);
    modelSelector.addItem ("stability-stable-audio-3", 4);
    modelSelector.addItem ("stability-stable-audio-25", 5);
    modelSelector.setText ("local-mock", juce::dontSendNotification);
    addAndMakeVisible (modelSelector);

    durationSlider.setSliderStyle (juce::Slider::LinearHorizontal);
    durationSlider.setTextBoxStyle (juce::Slider::TextBoxRight, false, 64, 22);
    durationSlider.setRange (0.5, 60.0, 0.5);
    durationSlider.setValue (8.0, juce::dontSendNotification);
    durationSlider.setColour (juce::Slider::trackColourId, juce::Colour::fromRGB (106, 176, 147));
    durationSlider.setColour (juce::Slider::backgroundColourId, panelColour());
    durationLabel.setText ("Duration", juce::dontSendNotification);
    durationLabel.setColour (juce::Label::textColourId, mutedTextColour());
    addAndMakeVisible (durationLabel);
    addAndMakeVisible (durationSlider);

    commandEditor.setMultiLine (false);
    commandEditor.setReturnKeyStartsNewLine (false);
    commandEditor.setTextToShowWhenEmpty ("type an ORAM command", mutedTextColour());
    commandEditor.setColour (juce::TextEditor::backgroundColourId, panelColour());
    commandEditor.setColour (juce::TextEditor::textColourId, textColour());
    commandEditor.setColour (juce::TextEditor::outlineColourId, juce::Colours::transparentBlack);
    addAndMakeVisible (commandEditor);

    configureSlider (inputMonitorSlider);
    configureSlider (loopLevelSlider);
    inputMonitorLabel.setText ("Input", juce::dontSendNotification);
    loopLevelLabel.setText ("Loops", juce::dontSendNotification);
    for (auto* label : { &inputMonitorLabel, &loopLevelLabel })
    {
        label->setColour (juce::Label::textColourId, mutedTextColour());
        addAndMakeVisible (*label);
    }
    addAndMakeVisible (inputMonitorSlider);
    addAndMakeVisible (loopLevelSlider);
    inputMonitorAttachment = std::make_unique<SliderAttachment> (audioProcessor.parameters(), "input_monitor", inputMonitorSlider);
    loopLevelAttachment = std::make_unique<SliderAttachment> (audioProcessor.parameters(), "loop_level", loopLevelSlider);

    for (auto& label : layerLabels)
    {
        label.setColour (juce::Label::backgroundColourId, panelColour());
        label.setColour (juce::Label::textColourId, textColour());
        label.setJustificationType (juce::Justification::centredLeft);
        addAndMakeVisible (label);
    }

    refreshLayerLabels();
    startTimerHz (12);
}

OramAudioProcessorEditor::~OramAudioProcessorEditor()
{
    stopTimer();
}

void OramAudioProcessorEditor::paint (juce::Graphics& g)
{
    g.fillAll (backgroundColour());
    g.setColour (juce::Colour::fromRGB (58, 61, 62));
    g.drawRect (getLocalBounds(), 1);
}

void OramAudioProcessorEditor::resized()
{
    auto bounds = getLocalBounds().reduced (margin);
    auto header = bounds.removeFromTop (44);
    titleLabel.setBounds (header.removeFromLeft (220));
    statusLabel.setBounds (header);

    bounds.removeFromTop (10);
    auto controls = bounds.removeFromTop (rowHeight);
    layerSelector.setBounds (controls.removeFromLeft (120));
    controls.removeFromLeft (8);
    recordButton.setBounds (controls.removeFromLeft (86));
    controls.removeFromLeft (6);
    overdubButton.setBounds (controls.removeFromLeft (86));
    controls.removeFromLeft (6);
    stopButton.setBounds (controls.removeFromLeft (72));
    controls.removeFromLeft (6);
    clearButton.setBounds (controls.removeFromLeft (72));

    bounds.removeFromTop (18);
    auto sliderRow = bounds.removeFromTop (56);
    auto inputArea = sliderRow.removeFromLeft ((sliderRow.getWidth() - 16) / 2);
    sliderRow.removeFromLeft (16);
    inputMonitorLabel.setBounds (inputArea.removeFromTop (18));
    inputMonitorSlider.setBounds (inputArea);
    loopLevelLabel.setBounds (sliderRow.removeFromTop (18));
    loopLevelSlider.setBounds (sliderRow);

    bounds.removeFromTop (18);
    const auto labelHeight = 46;
    for (auto& label : layerLabels)
    {
        label.setBounds (bounds.removeFromTop (labelHeight));
        bounds.removeFromTop (8);
    }

    bounds.removeFromTop (6);
    auto promptRow = bounds.removeFromTop (rowHeight);
    generateButton.setBounds (promptRow.removeFromRight (110));
    promptRow.removeFromRight (8);
    promptEditor.setBounds (promptRow);

    bounds.removeFromTop (10);
    auto generationOptions = bounds.removeFromTop (rowHeight);
    providerSelector.setBounds (generationOptions.removeFromLeft (140));
    generationOptions.removeFromLeft (8);
    modelSelector.setBounds (generationOptions.removeFromLeft (220));
    generationOptions.removeFromLeft (14);
    durationLabel.setBounds (generationOptions.removeFromLeft (70));
    durationSlider.setBounds (generationOptions);

    bounds.removeFromTop (10);
    auto commandRow = bounds.removeFromTop (rowHeight);
    commandButton.setBounds (commandRow.removeFromRight (110));
    commandRow.removeFromRight (8);
    commandEditor.setBounds (commandRow);
}

void OramAudioProcessorEditor::timerCallback()
{
    statusLabel.setText (audioProcessor.status(), juce::dontSendNotification);
    refreshLayerLabels();
}

void OramAudioProcessorEditor::configureSlider (juce::Slider& slider)
{
    slider.setSliderStyle (juce::Slider::LinearHorizontal);
    slider.setTextBoxStyle (juce::Slider::TextBoxRight, false, 70, 22);
    slider.setColour (juce::Slider::trackColourId, juce::Colour::fromRGB (106, 176, 147));
    slider.setColour (juce::Slider::backgroundColourId, panelColour());
    slider.setColour (juce::Slider::textBoxTextColourId, textColour());
    slider.setColour (juce::Slider::textBoxBackgroundColourId, panelColour());
}

void OramAudioProcessorEditor::refreshLayerLabels()
{
    const auto snapshot = audioProcessor.audioCore().snapshot();
    for (size_t i = 0; i < snapshot.size(); ++i)
    {
        const auto& layer = snapshot[i];
        auto text = "Layer " + juce::String (layer.slot) + "  ";
        if (layer.recording)
            text << "recording";
        else if (layer.empty)
            text << "empty";
        else
            text << juce::String (layer.durationSeconds, 2) << "s";
        if (layer.loopEnabled)
            text << "  loop " << juce::String (layer.loopStartPct, 0) << "-" << juce::String (layer.loopEndPct, 0) << "%";
        layerLabels[i].setText (text, juce::dontSendNotification);
    }
}
