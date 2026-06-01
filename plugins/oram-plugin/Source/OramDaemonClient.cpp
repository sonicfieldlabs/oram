#include "OramDaemonClient.h"

namespace
{
juce::File metadataFile()
{
    return juce::File::getSpecialLocation (juce::File::userHomeDirectory)
        .getChildFile ("Library")
        .getChildFile ("Application Support")
        .getChildFile ("ORAM")
        .getChildFile ("oram-daemon.json");
}

juce::String stringFromObject (const juce::var& object, const juce::Identifier& key)
{
    if (auto* dynamicObject = object.getDynamicObject())
        if (dynamicObject->hasProperty (key))
            return dynamicObject->getProperty (key).toString();
    return {};
}

int intFromObject (const juce::var& object, const juce::Identifier& key)
{
    if (auto* dynamicObject = object.getDynamicObject())
        if (dynamicObject->hasProperty (key))
            return (int) dynamicObject->getProperty (key);
    return 0;
}

juce::var objectProperty (const juce::var& object, const juce::Identifier& key)
{
    if (auto* dynamicObject = object.getDynamicObject())
        if (dynamicObject->hasProperty (key))
            return dynamicObject->getProperty (key);
    return {};
}
}

bool OramDaemonClient::loadMetadata()
{
    auto file = metadataFile();
    if (! file.existsAsFile())
        return false;

    auto parsed = juce::JSON::parse (file);
    if (! parsed.isObject())
        return false;

    host = stringFromObject (parsed, "host");
    port = intFromObject (parsed, "port");

    const auto auth = objectProperty (parsed, "auth");
    if (auth.isObject())
        authToken = stringFromObject (auth, "token");
    else
        authToken.clear();

    if (host.isEmpty())
        host = "127.0.0.1";

    return port > 0;
}

OramDaemonClient::GenerateResult OramDaemonClient::pluginGenerate (
    const juce::String& prompt,
    double durationSeconds,
    const juce::String& provider,
    const juce::String& model)
{
    if (! isConfigured() && ! loadMetadata())
        return { false, "offline", {}, {}, "ORAM daemon metadata not found" };

    auto* body = new juce::DynamicObject();
    body->setProperty ("prompt", prompt);
    body->setProperty ("duration", durationSeconds);
    body->setProperty ("model", model.isNotEmpty() ? model : "auto");
    body->setProperty ("provider", provider.isNotEmpty() ? provider : "auto");
    body->setProperty ("tags", juce::Array<juce::var>());

    auto response = postJson ("/plugin/generate", juce::var (body));
    GenerateResult result;
    result.status = stringFromObject (response, "status");
    result.message = stringFromObject (response, "message");
    result.ok = result.status == "ok";

    const auto sound = objectProperty (response, "sound");
    if (sound.isObject())
    {
        result.soundId = stringFromObject (sound, "id");
        result.path = stringFromObject (sound, "path");
    }

    if (! result.ok && result.message.isEmpty())
        result.message = stringFromObject (response, "error");

    return result;
}

juce::var OramDaemonClient::parseCommand (const juce::String& text)
{
    if (! isConfigured() && ! loadMetadata())
        return {};

    auto* body = new juce::DynamicObject();
    body->setProperty ("text", text);
    return postJson ("/actions/parse", juce::var (body), 10000);
}

juce::URL OramDaemonClient::endpoint (const juce::String& path) const
{
    return juce::URL ("http://" + host + ":" + juce::String (port) + path);
}

juce::var OramDaemonClient::postJson (const juce::String& path, const juce::var& body, int timeoutMs) const
{
    auto statusCode = 0;
    auto url = endpoint (path).withPOSTData (juce::JSON::toString (body, true));
    auto stream = url.createInputStream (
        juce::URL::InputStreamOptions (juce::URL::ParameterHandling::inPostData)
            .withConnectionTimeoutMs (timeoutMs)
            .withExtraHeaders (authHeaders())
            .withStatusCode (&statusCode));

    if (stream == nullptr || statusCode < 200 || statusCode >= 300)
        return {};

    return juce::JSON::parse (stream->readEntireStreamAsString());
}

juce::String OramDaemonClient::authHeaders() const
{
    auto headers = juce::String ("Content-Type: application/json\r\n");
    if (authToken.isNotEmpty())
        headers << "Authorization: Bearer " << authToken << "\r\n";
    return headers;
}
