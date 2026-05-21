import Foundation

@MainActor
final class AppStore: ObservableObject {
    @Published var connectionStatus = "starting"
    @Published var health: Health?
    @Published var state: EngineState?
    @Published var providers: [ProviderEngine] = []
    @Published var credentials: [String: CredentialStatus] = [:]
    @Published var sounds: [SoundRecord] = []
    @Published var selectedSoundID: SoundRecord.ID?
    @Published var errorMessage: String?
    @Published var isGenerating = false

    let client = DaemonClient()
    private let daemonManager = DaemonManager()

    var selectedSound: SoundRecord? {
        sounds.first { $0.id == selectedSoundID }
    }

    func bootstrap() async {
        connectionStatus = await daemonManager.launchIfNeeded(client: client)
        await refreshAll()
    }

    func refreshAll() async {
        do {
            _ = try client.loadMetadata()
            health = try await client.health()
            state = try await client.state()
            providers = try await client.providers().engines
            credentials = try await client.credentialStatus()
            sounds = try await client.sounds().sounds
            connectionStatus = "connected"
            errorMessage = nil
        } catch {
            connectionStatus = "offline"
            errorMessage = error.localizedDescription
        }
    }

    func generate(prompt: String, duration: Double, provider: String, model: String, tags: [String]) async {
        guard !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        isGenerating = true
        defer { isGenerating = false }
        do {
            let payload = GeneratePayload(
                prompt: prompt,
                duration: duration,
                provider: provider,
                model: model,
                targetLayer: "first_empty",
                tags: tags
            )
            let response = try await client.generate(payload)
            if let sound = response.sound {
                selectedSoundID = sound.id
            }
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func startRecording() async {
        do {
            try await client.recordStart()
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func stopRecording() async {
        do {
            try await client.recordStop()
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func sendCommand(_ text: String) async {
        do {
            try await client.sendCommand(text)
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func clearLayer(_ target: Int) async {
        do {
            try await client.clearLayer(target)
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func exportLayer(_ target: Int) async {
        do {
            try await client.exportLayer(target)
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func generateFromLayer(_ target: Int) async {
        do {
            try await client.generateFromLayer(target)
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func setVolume(layer target: Int, volume: Double) async {
        do {
            try await client.setVolume(layer: target, volume: volume)
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func killAll() async {
        do {
            try await client.killAll()
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func cycleInputMode() async {
        let current = modeKey
        let next = current == "prompt" ? "audio" : (current == "audio" ? "listen" : "prompt")
        do {
            try await client.setInputMode(next)
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func updateAudioSettings(sampleRate: Int?, blockSize: Int?) async {
        do {
            try await client.updateSettings(sampleRate: sampleRate, blockSize: blockSize)
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func testCredential(provider: String) async -> String {
        do {
            let response = try await client.testCredential(provider: provider)
            await refreshAll()
            return response.status
        } catch {
            errorMessage = error.localizedDescription
            return "failed"
        }
    }

    var modeKey: String {
        if state?.autoListen == true {
            return "listen"
        }
        return state?.inputMode == "audio" ? "audio" : "prompt"
    }

    func revealSelectedSound() async {
        guard let selectedSoundID else { return }
        do {
            try await client.reveal(soundID: selectedSoundID)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
