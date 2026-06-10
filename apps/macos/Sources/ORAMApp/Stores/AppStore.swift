import Foundation

@MainActor
final class AppStore: ObservableObject {
    @Published var connectionStatus = "starting"
    @Published var health: Health?
    @Published var state: EngineState?
    @Published var providers: [ProviderEngine] = []
    @Published var devices: DevicesResponse?
    @Published var credentials: [String: CredentialStatus] = [:]
    @Published var sounds: [SoundRecord] = []
    @Published var waveforms: [Int: WaveformPeaks] = [:]
    @Published var selectedSoundID: SoundRecord.ID?
    @Published var errorMessage: String?
    @Published var isGenerating = false

    let client = DaemonClient()
    private let daemonManager = DaemonManager()
    private var retryTask: Task<Void, Never>?
    private var wsTask: URLSessionWebSocketTask?
    private var wsRetryTask: Task<Void, Never>?
    private var waveformCacheKeys: [Int: String] = [:]
    private var waveformFetches: Set<String> = []
    private var isShuttingDown = false

    var selectedSound: SoundRecord? {
        sounds.first { $0.id == selectedSoundID }
    }

    func bootstrap() async {
        isShuttingDown = false
        connectionStatus = await daemonManager.launchIfNeeded(client: client)
        await refreshAll()
        connectWebSocket()
    }

    func refreshAll() async {
        do {
            _ = try client.loadMetadata()
            health = try await client.health()
            let nextState = try await client.state()
            state = nextState
            providers = try await client.providers().engines
            devices = try await client.devices()
            credentials = try await client.credentialStatus()
            sounds = try await client.sounds().sounds
            await refreshWaveforms(for: nextState)
            connectionStatus = "connected"
            errorMessage = nil
            retryTask?.cancel()
            retryTask = nil
        } catch {
            connectionStatus = "offline"
            errorMessage = error.localizedDescription
            // L12: Auto-reconnect after 5 seconds on failure
            retryTask?.cancel()
            retryTask = Task {
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                guard !Task.isCancelled else { return }
                await self.refreshAll()
            }
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

    func stableAudioRender(_ payload: StableAudioRenderPayload) async {
        guard !payload.prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        isGenerating = true
        defer { isGenerating = false }
        do {
            let response = try await client.stableAudioRender(payload)
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

    func toggleMasterRecording() async {
        do {
            let action = state?.masterRecording == true ? "stop" : "start"
            try await client.masterRecord(action: action)
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

    func undo() async {
        do {
            try await client.undo()
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func redo() async {
        do {
            try await client.redo()
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

    func generateFromLayer(_ target: Int, engine: String = "auto") async {
        do {
            try await client.generateFromLayer(target, engine: engine)
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func setLoopRegion(layer target: Int, startPct: Double, endPct: Double, enabled: Bool) async {
        do {
            try await client.setLoopRegion(layer: target, startPct: startPct, endPct: endPct, enabled: enabled)
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func setLoopFades(layer target: Int, fadeInPct: Double, fadeOutPct: Double) async {
        do {
            try await client.setLoopFades(layer: target, fadeInPct: fadeInPct, fadeOutPct: fadeOutPct)
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func setInpaintRegions(layer target: Int, regions: [InpaintRegionPayload]) async {
        do {
            try await client.setInpaintRegions(layer: target, regions: regions)
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func togglePlaybackReverse(layer target: Int, enabled: Bool? = nil) async {
        do {
            try await client.setPlaybackReverse(layer: target, enabled: enabled)
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

    func shutdown() async {
        guard !isShuttingDown else { return }
        isShuttingDown = true
        retryTask?.cancel()
        retryTask = nil
        wsRetryTask?.cancel()
        wsRetryTask = nil
        wsTask?.cancel(with: .goingAway, reason: nil)
        wsTask = nil
        do {
            if client.isConfigured {
                try await client.killAll()
            }
        } catch {
            errorMessage = error.localizedDescription
        }
        daemonManager.stop()
        connectionStatus = "stopped"
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

    func toggleAutoListen() async {
        do {
            try await client.toggleAutoListen()
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func updateAudioSettings(sampleRate: Int?, blockSize: Int?, inputDevice: Int? = nil, outputDevice: Int? = nil) async {
        do {
            try await client.updateSettings(
                sampleRate: sampleRate,
                blockSize: blockSize,
                inputDevice: inputDevice,
                outputDevice: outputDevice
            )
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

    func toggleFavorite(soundID: String) async {
        guard let sound = sounds.first(where: { $0.id == soundID }) else { return }
        do {
            _ = try await client.favorite(soundID: soundID, favorite: !sound.favorite)
            await refreshAll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func connectWebSocket() {
        guard let metadata = client.metadata else { return }
        wsTask?.cancel(with: .goingAway, reason: nil)
        var components = URLComponents()
        components.scheme = "ws"
        components.host = metadata.host
        components.port = metadata.port
        components.path = "/ws"
        if let token = metadata.auth?.token, metadata.auth?.enabled == true {
            components.queryItems = [URLQueryItem(name: "token", value: token)]
        }
        guard let url = components.url else { return }
        let session = URLSession(configuration: .default)
        let task = session.webSocketTask(with: url)
        wsTask = task
        task.resume()
        receiveWSMessage(task)
    }

    private func receiveWSMessage(_ task: URLSessionWebSocketTask) {
        task.receive { [weak self] result in
            Task { @MainActor in
                guard let self else { return }
                switch result {
                case .success(let message):
                    switch message {
                    case .string(let text):
                        if let data = text.data(using: .utf8),
                           let newState = try? JSONDecoder().decode(EngineState.self, from: data) {
                            self.state = newState
                            await self.refreshWaveforms(for: newState)
                        }
                    case .data(let data):
                        if let newState = try? JSONDecoder().decode(EngineState.self, from: data) {
                            self.state = newState
                            await self.refreshWaveforms(for: newState)
                        }
                    @unknown default:
                        break
                    }
                    self.receiveWSMessage(task)
                case .failure:
                    // Reconnect after delay
                    self.wsRetryTask?.cancel()
                    self.wsRetryTask = Task {
                        try? await Task.sleep(nanoseconds: 2_000_000_000)
                        guard !Task.isCancelled else { return }
                        self.connectWebSocket()
                    }
                }
            }
        }
    }

    private func refreshWaveforms(for state: EngineState) async {
        let activeSlots = Set(state.layers.filter { $0.state != "empty" }.map(\.slot))
        for slot in Array(waveforms.keys) where !activeSlots.contains(slot) {
            waveforms.removeValue(forKey: slot)
            waveformCacheKeys.removeValue(forKey: slot)
        }

        for layer in state.layers where layer.state != "empty" {
            let revision = layer.waveformRevision ?? 0
            let key = "\(layer.id):\(revision):512"
            if waveformCacheKeys[layer.slot] == key || waveformFetches.contains(key) {
                continue
            }
            waveformFetches.insert(key)
            do {
                let waveform = try await client.waveform(target: layer.slot, points: 512)
                if waveform.revision == revision {
                    waveforms[layer.slot] = waveform
                    waveformCacheKeys[layer.slot] = key
                }
            } catch {
                errorMessage = error.localizedDescription
            }
            waveformFetches.remove(key)
        }
    }
}
