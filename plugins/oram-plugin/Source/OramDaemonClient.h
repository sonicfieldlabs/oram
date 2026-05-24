#pragma once

#include <juce_core/juce_core.h>

class OramDaemonClient
{
public:
    struct GenerateResult
    {
        bool ok = false;
        juce::String status;
        juce::String soundId;
        juce::String path;
        juce::String message;
    };

    bool loadMetadata();
    bool isConfigured() const noexcept { return port > 0; }

    GenerateResult pluginGenerate (
        const juce::String& prompt,
        double durationSeconds,
        const juce::String& provider,
        const juce::String& model);
    juce::var parseCommand (const juce::String& text);

private:
    juce::URL endpoint (const juce::String& path) const;
    juce::var postJson (const juce::String& path, const juce::var& body, int timeoutMs = 30000) const;
    juce::String authHeaders() const;

    juce::String host = "127.0.0.1";
    int port = 0;
    juce::String authToken;
};
