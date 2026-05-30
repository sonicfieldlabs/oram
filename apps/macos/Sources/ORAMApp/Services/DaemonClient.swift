import Foundation

@MainActor
final class DaemonClient {
    private let session: URLSession
    nonisolated(unsafe) private(set) var metadata: DaemonMetadata?

    init(session: URLSession = .shared) {
        self.session = session
    }

    var isConfigured: Bool {
        metadata != nil
    }

    nonisolated func loadMetadata() throws -> DaemonMetadata {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/ORAM/oram-daemon.json")
        let data = try Data(contentsOf: url)
        let decoded = try JSONDecoder().decode(DaemonMetadata.self, from: data)
        metadata = decoded
        return decoded
    }

    func health() async throws -> Health {
        try await get("/health")
    }

    func state() async throws -> EngineState {
        try await get("/state")
    }

    func providers() async throws -> ProvidersResponse {
        try await get("/providers")
    }

    func devices() async throws -> DevicesResponse {
        try await get("/devices")
    }

    func credentialStatus() async throws -> [String: CredentialStatus] {
        try await get("/credentials/status")
    }

    func testCredential(provider: String) async throws -> CredentialTestResponse {
        try await post("/credentials/test", payload: ["provider": provider])
    }

    func sounds() async throws -> SoundsResponse {
        try await get("/library/sounds")
    }

    func waveform(target: Int, points: Int = 512) async throws -> WaveformPeaks {
        try await get("/waveform/\(target)?points=\(points)")
    }

    func sendCommand(_ text: String) async throws {
        let payload = ["text": text]
        let _: EmptyResponse = try await post("/command", payload: payload)
    }

    func generate(_ payload: GeneratePayload) async throws -> GenerateResponse {
        try await post("/generate", payload: payload)
    }

    func stableAudioRender(_ payload: StableAudioRenderPayload) async throws -> GenerateResponse {
        try await post("/stable-audio/render", payload: payload)
    }

    func recordStart() async throws {
        let payload = ["target": "selected"]
        let _: EmptyResponse = try await post("/record/start", payload: payload)
    }

    func recordStop() async throws {
        let _: EmptyResponse = try await post("/record/stop", payload: EmptyPayload())
    }

    func clearLayer(_ target: Int) async throws {
        let _: EmptyResponse = try await post("/layer/clear", payload: TargetPayload(target: target))
    }

    func exportLayer(_ target: Int) async throws {
        let _: EmptyResponse = try await post("/layer/export", payload: TargetPayload(target: target))
    }

    func generateFromLayer(_ target: Int, engine: String = "auto") async throws {
        let payload = GenerateFromPayload(target: target, route: "hybrid", engine: engine, duration: nil)
        let _: EmptyResponse = try await post("/layer/generate", payload: payload)
    }

    func setLoopRegion(layer target: Int, startPct: Double, endPct: Double, enabled: Bool) async throws {
        let payload = LoopRegionPayload(
            target: target,
            startPct: startPct,
            endPct: endPct,
            enabled: enabled
        )
        let _: EmptyResponse = try await post("/layer/loop-region", payload: payload)
    }

    func setVolume(layer target: Int, volume: Double) async throws {
        let payload = VolumePayload(target: target, volume: volume)
        let _: EmptyResponse = try await post("/layer/volume", payload: payload)
    }

    func killAll() async throws {
        let _: EmptyResponse = try await post("/kill", payload: EmptyPayload())
    }

    func setInputMode(_ mode: String) async throws {
        let _: EmptyResponse = try await post("/input-mode", payload: InputModePayload(mode: mode))
    }

    func toggleAutoListen() async throws {
        let _: EmptyResponse = try await post("/auto-listen", payload: EmptyPayload())
    }

    func updateSettings(
        sampleRate: Int?,
        blockSize: Int?,
        inputDevice: Int? = nil,
        outputDevice: Int? = nil,
        bitDepth: Int? = nil,
        recFormat: String? = nil
    ) async throws {
        let payload = SettingsPayload(
            inputDevice: inputDevice,
            outputDevice: outputDevice,
            sampleRate: sampleRate,
            blockSize: blockSize,
            bitDepth: bitDepth,
            recFormat: recFormat
        )
        let _: EmptyResponse = try await post("/settings", payload: payload)
    }

    func reveal(soundID: String) async throws {
        let payload = ["sound_id": soundID]
        let _: EmptyResponse = try await post("/library/reveal", payload: payload)
    }

    func favorite(soundID: String, favorite: Bool) async throws -> SoundRecord {
        try await post("/library/sounds/\(soundID)/favorite", payload: ["favorite": favorite])
    }

    func setTags(soundID: String, tags: [String]) async throws -> SoundRecord {
        try await post("/library/sounds/\(soundID)/tags", payload: ["tags": tags])
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        var request = try request(path: path, method: "GET")
        request.httpBody = nil
        let (data, response) = try await session.data(for: request)
        try validate(response: response, data: data)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func post<T: Decodable, Payload: Encodable>(_ path: String, payload: Payload) async throws -> T {
        var request = try request(path: path, method: "POST")
        request.httpBody = try JSONEncoder().encode(payload)
        let (data, response) = try await session.data(for: request)
        try validate(response: response, data: data)
        if data.isEmpty {
            guard let result = EmptyResponse() as? T else {
                throw DaemonError.invalidResponse("Expected EmptyResponse but got \(T.self)")
            }
            return result
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func request(path: String, method: String) throws -> URLRequest {
        guard let metadata else {
            throw DaemonError.missingMetadata
        }
        guard let url = URL(string: path, relativeTo: metadata.baseURL) else {
            throw DaemonError.missingMetadata
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = metadata.auth?.token, metadata.auth?.enabled == true {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return request
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw DaemonError.http(status: http.statusCode, body: body)
        }
    }
}

struct EmptyPayload: Encodable {}

struct EmptyResponse: Decodable {}

private struct TargetPayload: Encodable {
    let target: Int
}

private struct GenerateFromPayload: Encodable {
    let target: Int
    let route: String
    let engine: String
    let duration: Double?
}

private struct LoopRegionPayload: Encodable {
    let target: Int
    let startPct: Double
    let endPct: Double
    let enabled: Bool

    enum CodingKeys: String, CodingKey {
        case target
        case startPct = "start_pct"
        case endPct = "end_pct"
        case enabled
    }
}

private struct VolumePayload: Encodable {
    let target: Int
    let volume: Double
}

private struct InputModePayload: Encodable {
    let mode: String
}

private struct SettingsPayload: Encodable {
    let inputDevice: Int?
    let outputDevice: Int?
    let sampleRate: Int?
    let blockSize: Int?
    let bitDepth: Int?
    let recFormat: String?

    enum CodingKeys: String, CodingKey {
        case inputDevice = "input_device"
        case outputDevice = "output_device"
        case sampleRate = "sample_rate"
        case blockSize = "block_size"
        case bitDepth = "bit_depth"
        case recFormat = "rec_format"
    }
}

enum DaemonError: Error, LocalizedError {
    case missingMetadata
    case http(status: Int, body: String)
    case invalidResponse(String)

    var errorDescription: String? {
        switch self {
        case .missingMetadata:
            return "Daemon metadata was not found."
        case let .http(status, body):
            return "HTTP \(status): \(body)"
        case let .invalidResponse(message):
            return "Invalid response: \(message)"
        }
    }
}
