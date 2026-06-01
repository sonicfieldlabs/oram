import SwiftUI

struct GeneratorView: View {
    @EnvironmentObject private var store: AppStore
    @State private var prompt = "distant metallic rain inside a concrete tunnel"
    @State private var duration = 8.0
    @State private var runtimeMode = "local"
    @State private var provider = "auto"
    @State private var model = "stable-audio-3-local"
    @State private var localProvider = "stable_audio_mlx"
    @State private var localModel = "sm-music"
    @State private var serviceURL = "http://127.0.0.1:8765"
    @State private var chunkedDecode = true
    @State private var tags = "texture, metallic"
    @State private var stableMode = "generate"
    @State private var decoder = "same-s"
    @State private var sourceLayer = 1
    @State private var seedText = ""
    @State private var negativePrompt = "voice, speech, vocals"
    @State private var steps = 8
    @State private var cfgScale = 1.0
    @State private var noiseDepth = 0.55
    @State private var inpaintStart = ""
    @State private var inpaintEnd = ""
    @State private var variationCount = 1
    @State private var loraAPath = ""
    @State private var loraAStrength = 0.0
    @State private var loraBPath = ""
    @State private var loraBStrength = 0.0
    @State private var loraIntervalMin = 0.0
    @State private var loraIntervalMax = 1.0

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            TextEditor(text: $prompt)
                .font(.title3)
                .frame(minHeight: 110)
                .scrollContentBackground(.hidden)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))

            VStack(alignment: .leading, spacing: 10) {
                Picker("Runtime", selection: $runtimeMode) {
                    Text("API").tag("api")
                    Text("Local").tag("local")
                }
                .pickerStyle(.segmented)

                if runtimeMode == "api" {
                    HStack {
                        Picker("Provider", selection: $provider) {
                            Text("Auto").tag("auto")
                            Text("Local").tag("local")
                            Text("ElevenLabs").tag("elevenlabs")
                            Text("Stability").tag("stability")
                        }
                        .pickerStyle(.segmented)

                        Picker("Model", selection: $model) {
                            Text("Auto").tag("auto")
                            Text("Local").tag("stable-audio-3-local")
                            Text("ElevenLabs SFX").tag("elevenlabs-sfx")
                            Text("ElevenLabs Music").tag("elevenlabs-music")
                            Text("Stability Large").tag("stability-stable-audio-3")
                            Text("Stability 2.5").tag("stability-stable-audio-25")
                            Text("Local Mock").tag("local-mock")
                        }
                        .frame(width: 260)
                    }
                } else {
                    HStack {
                        Picker("Runtime", selection: $localProvider) {
                            Text("MLX").tag("stable_audio_mlx")
                            Text("Python").tag("stable_audio_python")
                            Text("Mock").tag("mock")
                        }
                        .frame(width: 140)

                        Picker("Model", selection: $localModel) {
                            Text("Small Music").tag("sm-music")
                            Text("Small SFX").tag("sm-sfx")
                            Text("Medium").tag("medium")
                            Text("Medium MLX").tag("medium-mlx")
                        }
                        .frame(width: 160)

                        TextField("service URL", text: $serviceURL)
                            .textFieldStyle(.roundedBorder)
                            .frame(minWidth: 220)

                        Toggle("chunked", isOn: $chunkedDecode)
                            .toggleStyle(.checkbox)
                            .frame(width: 92)
                    }
                }
            }

            HStack {
                Slider(value: $duration, in: 0.5...30, step: 0.5)
                Text(String(format: "%.1fs", duration))
                    .frame(width: 54, alignment: .trailing)
            }

            if isStableAudio3 {
                stableAudioControls
            }

            TextField("tags", text: $tags)
                .textFieldStyle(.roundedBorder)

            HStack {
                Button {
                    Task {
                        if isStableAudio3 {
                            await store.stableAudioRender(stableAudioPayload)
                        } else {
                            await store.generate(
                                prompt: prompt,
                                duration: duration,
                                provider: effectiveProvider,
                                model: effectiveModel,
                                tags: parsedTags
                            )
                        }
                    }
                } label: {
                    Label(store.isGenerating ? "Generating" : "Generate", systemImage: "wand.and.stars")
                }
                .keyboardShortcut(.return, modifiers: [.command])
                .disabled(store.isGenerating)

                Button {
                    Task { await store.refreshAll() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }

                Spacer()
            }

            if let selected = store.selectedSound {
                Divider()
                SoundDetail(sound: selected)
            }

            Spacer()
        }
        .padding(24)
    }

    private var isStableAudio3: Bool {
        runtimeMode == "local" || model == "stability-stable-audio-3"
    }

    private var effectiveProvider: String {
        runtimeMode == "local" ? "local" : provider
    }

    private var effectiveModel: String {
        runtimeMode == "local" ? "stable-audio-3-local" : model
    }

    private var modeRequiresSource: Bool {
        stableMode == "morph" || stableMode == "continue" || stableMode == "inpaint" || stableMode == "latent"
    }

    private var parsedTags: [String] {
        tags.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }

    private var stableAudioPayload: StableAudioRenderPayload {
        StableAudioRenderPayload(
            prompt: prompt,
            mode: stableMode,
            duration: duration,
            provider: effectiveProvider,
            model: effectiveModel,
            decoder: decoder,
            localProvider: localProvider,
            localModel: localModel,
            serviceURL: runtimeMode == "local" ? serviceURL : "",
            chunkedDecode: chunkedDecode,
            sourceLayer: modeRequiresSource ? sourceLayer : nil,
            targetLayer: "first_empty",
            assignLayer: true,
            tags: parsedTags,
            negativePrompt: negativePrompt,
            seed: Int(seedText.trimmingCharacters(in: .whitespacesAndNewlines)),
            steps: steps,
            cfgScale: cfgScale,
            noiseDepth: stableMode == "generate" ? nil : noiseDepth,
            inpaintStart: Double(inpaintStart.trimmingCharacters(in: .whitespacesAndNewlines)),
            inpaintEnd: Double(inpaintEnd.trimmingCharacters(in: .whitespacesAndNewlines)),
            variationCount: variationCount,
            loraStack: [],
            loraAPath: loraAPath,
            loraAStrength: loraAStrength,
            loraBPath: loraBPath,
            loraBStrength: loraBStrength,
            loraIntervalMin: loraIntervalMin,
            loraIntervalMax: loraIntervalMax
        )
    }

    private var stableAudioControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Picker("Mode", selection: $stableMode) {
                    Text("Generate").tag("generate")
                    Text("Morph").tag("morph")
                    Text("Continue").tag("continue")
                    Text("Inpaint").tag("inpaint")
                    Text("Latent").tag("latent")
                    Text("LoRA").tag("lora_mixer")
                }
                .pickerStyle(.segmented)

                Picker("Source", selection: $sourceLayer) {
                    ForEach(1...4, id: \.self) { slot in
                        Text("L\(slot)").tag(slot)
                    }
                }
                .frame(width: 96)
                .disabled(!modeRequiresSource)

                Picker("Decoder", selection: $decoder) {
                    Text("same-s").tag("same-s")
                    Text("same-l").tag("same-l")
                }
                .frame(width: 112)
            }

            HStack(spacing: 14) {
                Stepper("Steps \(steps)", value: $steps, in: 1...32)
                    .frame(width: 130)
                Stepper("Var \(variationCount)", value: $variationCount, in: 1...8)
                    .frame(width: 116)
                TextField("seed", text: $seedText)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 88)
                TextField("negative prompt", text: $negativePrompt)
                    .textFieldStyle(.roundedBorder)
            }

            HStack(spacing: 14) {
                Slider(value: $cfgScale, in: 0.0...8.0, step: 0.1)
                Text(String(format: "cfg %.1f", cfgScale))
                    .frame(width: 64, alignment: .trailing)
                Slider(value: $noiseDepth, in: 0.0...1.0, step: 0.01)
                    .disabled(stableMode == "generate")
                Text(String(format: "noise %.2f", noiseDepth))
                    .frame(width: 82, alignment: .trailing)
            }

            if stableMode == "inpaint" || stableMode == "continue" {
                HStack {
                    TextField("start", text: $inpaintStart)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 90)
                    TextField("end", text: $inpaintEnd)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 90)
                }
            }

            if stableMode == "lora_mixer" {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        TextField("LoRA A path", text: $loraAPath)
                            .textFieldStyle(.roundedBorder)
                        Slider(value: $loraAStrength, in: 0.0...2.0, step: 0.05)
                        Text(String(format: "%.2f", loraAStrength))
                            .frame(width: 44, alignment: .trailing)
                    }
                    HStack {
                        TextField("LoRA B path", text: $loraBPath)
                            .textFieldStyle(.roundedBorder)
                        Slider(value: $loraBStrength, in: 0.0...2.0, step: 0.05)
                        Text(String(format: "%.2f", loraBStrength))
                            .frame(width: 44, alignment: .trailing)
                    }
                    HStack {
                        Slider(value: $loraIntervalMin, in: 0.0...1.0, step: 0.01)
                        Slider(value: $loraIntervalMax, in: 0.0...1.0, step: 0.01)
                        Text(String(format: "%.2f-%.2f", loraIntervalMin, loraIntervalMax))
                            .frame(width: 92, alignment: .trailing)
                    }
                }
            }
        }
        .padding(12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }
}

struct SoundDetail: View {
    let sound: SoundRecord

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(sound.prompt)
                .font(.headline)
                .lineLimit(2)
            HStack {
                Text(sound.provider)
                Text(sound.model)
                Text(String(format: "%.2fs", sound.durationSeconds))
                Text("\(sound.sampleRate) Hz")
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            Text(sound.path)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
                .textSelection(.enabled)
        }
        .padding(12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }
}
