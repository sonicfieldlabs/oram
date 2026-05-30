import Foundation

struct DaemonAuth: Codable {
    let enabled: Bool
    let token: String?
    let source: String?
}

struct DaemonMetadata: Codable {
    let pid: Int
    let host: String
    let port: Int
    let startedAt: String
    let version: String
    let authTokenConfigured: Bool
    let metadataPath: String
    let projectPath: String?
    let auth: DaemonAuth?

    enum CodingKeys: String, CodingKey {
        case pid
        case host
        case port
        case startedAt = "started_at"
        case version
        case authTokenConfigured = "auth_token_configured"
        case metadataPath = "metadata_path"
        case projectPath = "project_path"
        case auth
    }

    var baseURL: URL {
        guard let url = URL(string: "http://\(host):\(port)") else {
            // Fallback to localhost when host string produces an invalid URL
            return URL(string: "http://127.0.0.1:\(port)")!
        }
        return url
    }
}

struct Health: Decodable {
    let status: String
    let version: String
    let audioRunning: Bool

    enum CodingKeys: String, CodingKey {
        case status
        case version
        case audioRunning = "audio_running"
    }
}

struct EngineState: Decodable {
    let version: String
    let mode: String
    let scene: String
    let sampleRate: Int
    let blockSize: Int
    let sessionDir: String
    let libraryDir: String
    let selectedLayer: Int
    let audioRunning: Bool
    let recording: Bool
    let inputLevel: Double?
    let outputLevel: Double?
    let inputMode: String?
    let autoListen: Bool?
    let gateway: String
    let engineCount: Int
    let layers: [LayerState]
    let log: [String]

    enum CodingKeys: String, CodingKey {
        case version
        case mode
        case scene
        case sampleRate = "sample_rate"
        case blockSize = "block_size"
        case sessionDir = "session_dir"
        case libraryDir = "library_dir"
        case selectedLayer = "selected_layer"
        case audioRunning = "audio_running"
        case recording
        case inputLevel = "input_level"
        case outputLevel = "output_level"
        case inputMode = "input_mode"
        case autoListen = "auto_listen"
        case gateway
        case engineCount = "engine_count"
        case layers
        case log
    }
}

struct LayerState: Decodable, Identifiable {
    let id: String
    let slot: Int
    let name: String
    let state: String
    let sourceType: String
    let layerMode: String?
    let duration: Double
    let muted: Bool
    let solo: Bool
    let volume: Double
    let pan: Double
    let effects: [String]?
    let isGenerated: Bool
    let generationPrompt: String?
    let parentLayerID: String?
    let generationDepth: Int?
    let listeningRoute: String?
    let generationEngine: String?
    let waveform: [Double]
    let waveformRevision: Int?
    let playheadPct: Double?
    let loopEnabled: Bool?
    let loopStartPct: Double?
    let loopEndPct: Double?
    let loopStartSeconds: Double?
    let loopEndSeconds: Double?

    enum CodingKeys: String, CodingKey {
        case id
        case slot
        case name
        case state
        case sourceType = "source_type"
        case layerMode = "layer_mode"
        case duration
        case muted
        case solo
        case volume
        case pan
        case effects
        case isGenerated = "is_generated"
        case generationPrompt = "generation_prompt"
        case parentLayerID = "parent_layer_id"
        case generationDepth = "generation_depth"
        case listeningRoute = "listening_route"
        case generationEngine = "generation_engine"
        case waveform
        case waveformRevision = "waveform_revision"
        case playheadPct = "playhead_pct"
        case loopEnabled = "loop_enabled"
        case loopStartPct = "loop_start_pct"
        case loopEndPct = "loop_end_pct"
        case loopStartSeconds = "loop_start_seconds"
        case loopEndSeconds = "loop_end_seconds"
    }
}

extension LayerState {
    static let placeholderLayers: [LayerState] = (1...4).map { placeholder(slot: $0) }

    static func placeholder(slot: Int) -> LayerState {
        LayerState(
            id: "placeholder-\(slot)",
            slot: slot,
            name: "Layer \(slot)",
            state: "empty",
            sourceType: "empty",
            layerMode: nil,
            duration: 0,
            muted: false,
            solo: false,
            volume: 1,
            pan: 0,
            effects: [],
            isGenerated: false,
            generationPrompt: nil,
            parentLayerID: nil,
            generationDepth: nil,
            listeningRoute: nil,
            generationEngine: nil,
            waveform: Array(repeating: 0, count: 64),
            waveformRevision: 0,
            playheadPct: 0,
            loopEnabled: false,
            loopStartPct: 0,
            loopEndPct: 100,
            loopStartSeconds: 0,
            loopEndSeconds: 0
        )
    }
}

struct WaveformPeaks: Decodable {
    let target: Int
    let points: Int
    let revision: Int
    let duration: Double
    let peaks: [[Double]]
    let rms: [Double]
}

struct CredentialStatus: Decodable {
    let configured: Bool
    let source: String
    let lastTestStatus: String

    enum CodingKeys: String, CodingKey {
        case configured
        case source
        case lastTestStatus = "last_test_status"
    }
}

struct CredentialTestResponse: Decodable {
    let provider: String
    let configured: Bool
    let status: String
    let message: String?
}

struct ProviderEngine: Decodable, Identifiable {
    let id: String
    let provider: String
    let label: String
    let mode: String
    let requiresAPIKey: Bool
    let available: Bool
    let capabilities: [String]
    let maxDuration: Double

    enum CodingKeys: String, CodingKey {
        case id
        case provider
        case label
        case mode
        case requiresAPIKey = "requires_api_key"
        case available
        case capabilities
        case maxDuration = "max_duration"
    }
}

struct ProvidersResponse: Decodable {
    let engines: [ProviderEngine]
    let available: Int
}

struct AudioDevice: Decodable, Identifiable {
    let id: Int
    let name: String
    let maxInputChannels: Int
    let maxOutputChannels: Int
    let defaultSamplerate: Double
    let isInput: Bool
    let isOutput: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case maxInputChannels = "max_input_channels"
        case maxOutputChannels = "max_output_channels"
        case defaultSamplerate = "default_samplerate"
        case isInput = "is_input"
        case isOutput = "is_output"
    }
}

struct DevicesResponse: Decodable {
    let devices: [AudioDevice]
    let defaultInput: Int
    let defaultOutput: Int
    let currentInput: Int?
    let currentOutput: Int?
    let currentSampleRate: Int
    let currentFormat: String
    let currentBitDepth: Int

    enum CodingKeys: String, CodingKey {
        case devices
        case defaultInput = "default_input"
        case defaultOutput = "default_output"
        case currentInput = "current_input"
        case currentOutput = "current_output"
        case currentSampleRate = "current_sample_rate"
        case currentFormat = "current_format"
        case currentBitDepth = "current_bit_depth"
    }
}

struct SoundRecord: Decodable, Identifiable {
    let id: String
    let createdAt: String
    let provider: String
    let model: String
    let prompt: String
    let durationSeconds: Double
    let sampleRate: Int
    let format: String
    let source: String
    let tags: [String]
    let sessionID: String?
    let favorite: Bool
    let path: String

    enum CodingKeys: String, CodingKey {
        case id
        case createdAt = "created_at"
        case provider
        case model
        case prompt
        case durationSeconds = "duration_seconds"
        case sampleRate = "sample_rate"
        case format
        case source
        case tags
        case sessionID = "session_id"
        case favorite
        case path
    }
}

struct SoundsResponse: Decodable {
    let sounds: [SoundRecord]
}

struct GeneratePayload: Encodable {
    let prompt: String
    let duration: Double
    let provider: String
    let model: String
    let targetLayer: String
    let tags: [String]

    enum CodingKeys: String, CodingKey {
        case prompt
        case duration
        case provider
        case model
        case targetLayer = "target_layer"
        case tags
    }
}

struct StableAudioLoraPayload: Encodable {
    let name: String
    let path: String
    let strength: Double
    let interval: [Double]?
}

struct StableAudioRenderPayload: Encodable {
    let prompt: String
    let mode: String
    let duration: Double
    let provider: String
    let model: String
    let decoder: String
    let localProvider: String
    let localModel: String
    let serviceURL: String
    let chunkedDecode: Bool
    let sourceLayer: Int?
    let targetLayer: String?
    let assignLayer: Bool
    let tags: [String]
    let negativePrompt: String
    let seed: Int?
    let steps: Int
    let cfgScale: Double
    let noiseDepth: Double?
    let inpaintStart: Double?
    let inpaintEnd: Double?
    let variationCount: Int
    let loraStack: [StableAudioLoraPayload]
    let loraAPath: String
    let loraAStrength: Double
    let loraBPath: String
    let loraBStrength: Double
    let loraIntervalMin: Double
    let loraIntervalMax: Double

    enum CodingKeys: String, CodingKey {
        case prompt
        case mode
        case duration
        case provider
        case model
        case decoder
        case localProvider = "local_provider"
        case localModel = "local_model"
        case serviceURL = "service_url"
        case chunkedDecode = "chunked_decode"
        case sourceLayer = "source_layer"
        case targetLayer = "target_layer"
        case assignLayer = "assign_layer"
        case tags
        case negativePrompt = "negative_prompt"
        case seed
        case steps
        case cfgScale = "cfg_scale"
        case noiseDepth = "noise_depth"
        case inpaintStart = "inpaint_start"
        case inpaintEnd = "inpaint_end"
        case variationCount = "variation_count"
        case loraStack = "lora_stack"
        case loraAPath = "lora_a_path"
        case loraAStrength = "lora_a_strength"
        case loraBPath = "lora_b_path"
        case loraBStrength = "lora_b_strength"
        case loraIntervalMin = "lora_interval_min"
        case loraIntervalMax = "lora_interval_max"
    }
}

struct GenerateResponse: Decodable {
    let status: String
    let sound: SoundRecord?
    let layer: Int?
    let message: String?
}
