import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: AppStore
    @State private var prompt = ""
    @State private var hint = "hover over a button for details"
    @State private var showSettings = false
    @State private var showFX = false
    @State private var showLog = false
    @State private var showLibrary = false
    @State private var showProvider = false
    @State private var showPrivacy = false
    @State private var showAbout = false
    @State private var showCommandPalette = false
    @State private var showFourthLayer = false
    @State private var lightTheme = false
    @State private var commandSearch = ""
    @State private var sampleRateDraft = 48000
    @State private var blockSizeDraft = 512
    @State private var inputDeviceDraft = -1
    @State private var outputDeviceDraft = -1
    @State private var selectedModel = "stable-audio-3-local"
    @State private var runtimeMode = "local"
    @State private var stableMode = "generate"
    @State private var stableDuration = 8.0
    @State private var stableLocalProvider = "stable_audio_mlx"
    @State private var stableLocalModel = "sm-music"
    @State private var stableServiceURL = "http://127.0.0.1:8765"
    @State private var stableDecoder = "same-s"
    @State private var stableChunkedDecode = true
    @State private var stableSeed = ""
    @State private var stableNegativePrompt = "voice, speech, vocals"
    @State private var stableSteps = 8
    @State private var stableCfgScale = 1.0
    @State private var stableNoiseDepth = 0.55

    private let fxCommands: [(glyph: String, name: String, command: String, hint: String)] = [
        ("↺", "reverse", "reverse", "reverse the selected layer's audio"),
        ("½", "slower", "make it slower", "half speed — pitch drops, time stretches"),
        ("2×", "faster", "make it faster", "double speed — time compresses, pitch rises"),
        ("◐", "darker", "make it darker", "low-pass filter — darken the texture"),
        ("░", "granulate", "granulate softly", "granular synthesis — fragment into particles"),
        ("≋", "reverb", "wash it in reverb", "reverb wash — push into deep space"),
        ("↠", "far", "make it far away", "distance — make it sound far away"),
        ("≈", "stretch", "stretch until it breathes", "time stretch — slow and breathe")
    ]

    var body: some View {
        ZStack {
            DashboardTheme.background(lightTheme)
                .ignoresSafeArea()

            ScrollView {
                VStack(spacing: 8) {
                    header
                    hintBar

                    if showSettings {
                        settingsPanel
                    }

                    if showFX {
                        fxPalette
                    }

                    layersPanel

                    promptModule

                    logPanel
                }
                .frame(maxWidth: 880)
                .padding(.horizontal, 20)
                .padding(.vertical, 18)
                .frame(maxWidth: .infinity, alignment: .top)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)

            if showAbout {
                AboutOverlay(theme: lightTheme, close: { showAbout = false }, openPrivacy: {
                    showAbout = false
                    showPrivacy = true
                })
            }

            if showCommandPalette {
                CommandPaletteOverlay(
                    theme: lightTheme,
                    search: $commandSearch,
                    close: { showCommandPalette = false },
                    run: { text in
                        prompt = text
                        showCommandPalette = false
                        Task { await store.sendCommand(text) }
                    }
                )
            }

            Button("") {
                showCommandPalette = true
            }
            .keyboardShortcut("k", modifiers: [.command])
            .opacity(0)
            .frame(width: 0, height: 0)
        }
        .foregroundStyle(DashboardTheme.text(lightTheme))
        .font(.system(size: 13, design: .monospaced))
        .sheet(isPresented: $showLibrary) {
            LibraryView()
                .environmentObject(store)
                .frame(minWidth: 760, minHeight: 520)
        }
        .sheet(isPresented: $showProvider) {
            ProviderSetupView()
                .environmentObject(store)
                .frame(minWidth: 640, minHeight: 520)
        }
        .sheet(isPresented: $showPrivacy) {
            PrivacyView()
                .environmentObject(store)
                .frame(minWidth: 640, minHeight: 480)
        }
        .task {
            sampleRateDraft = store.state?.sampleRate ?? sampleRateDraft
            blockSizeDraft = store.state?.blockSize ?? blockSizeDraft
            syncDeviceDrafts()
            ensureSelectedModel()
        }
        .onChange(of: store.state?.sampleRate) { newValue in
            if let newValue {
                sampleRateDraft = newValue
            }
        }
        .onChange(of: store.state?.blockSize) { newValue in
            if let newValue {
                blockSizeDraft = newValue
            }
        }
        .onChange(of: store.devices?.currentInput) { _ in
            syncDeviceDrafts()
        }
        .onChange(of: store.devices?.currentOutput) { _ in
            syncDeviceDrafts()
        }
        .onChange(of: store.providers.map(\.id)) { _ in
            ensureSelectedModel()
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Button {
                showAbout = true
            } label: {
                HStack(spacing: 8) {
                    OramLogoView(size: 26)
                    Text("oram")
                        .font(.system(size: 18, weight: .semibold, design: .monospaced))
                        .foregroundStyle(DashboardTheme.secondary(lightTheme))
                }
            }
            .buttonStyle(.plain)
            .onHoverHint("about · quick guide", $hint)

            Spacer(minLength: 8)

            HStack(spacing: 7) {
                Button {
                    Task { await cycleSelectedLayer() }
                } label: {
                    Text("\(store.state?.selectedLayer ?? 1)")
                        .font(.system(size: 12, weight: .bold, design: .monospaced))
                        .frame(width: 30, height: 30)
                }
                .buttonStyle(HeaderButtonStyle(theme: lightTheme, active: true))
                .onHoverHint("selected layer — click to cycle", $hint)

                HeaderGlyph("↶", active: store.state?.canUndo == true, theme: lightTheme) {
                    Task { await store.undo() }
                }
                .onHoverHint("undo last audio edit", $hint)

                HeaderGlyph("↷", active: store.state?.canRedo == true, theme: lightTheme) {
                    Task { await store.redo() }
                }
                .onHoverHint("redo last undone edit", $hint)

                HeaderSeparator()

                HeaderGlyph(store.state?.masterRecording == true ? "■" : "●", active: store.state?.masterRecording == true, role: .record, theme: lightTheme) {
                    Task { await store.toggleMasterRecording() }
                }
                .onHoverHint(masterRecordHint, $hint)

                HeaderGlyph("⊕", theme: lightTheme) {
                    Task { await store.sendCommand("overdub") }
                }
                .onHoverHint("overdub: layer new audio over existing (o)", $hint)

                HeaderSeparator()

                HeaderGlyph("fx", active: showFX, role: .fx, theme: lightTheme) {
                    showFX.toggle()
                }
                .onHoverHint("dsp effects — open the texture transforms", $hint)

                HeaderSeparator()

                HeaderGlyph("✦", role: .summon, theme: lightTheme) {
                    Task {
                        let sel = store.state?.selectedLayer ?? 1
                        if runtimeMode == "local" {
                            await store.stableAudioRender(stableAudioLayerPayload(sourceLayer: sel))
                        } else {
                            await store.generateFromLayer(sel, engine: selectedModel)
                        }
                    }
                }
                .onHoverHint("summon — listen to what's sounding and generate a new layer", $hint)

                HeaderSeparator()

                HeaderGlyph("⊘", role: .danger, theme: lightTheme) {
                    Task { await store.killAll() }
                }
                .onHoverHint("nuke reset — stop audio and return to the initial state", $hint)

                HeaderSeparator()

                HeaderGlyph("+", active: showFourthLayer, theme: lightTheme) {
                    showFourthLayer = true
                }
                .onHoverHint("add another layer slot", $hint)
            }

            Spacer(minLength: 8)

            HStack(spacing: 10) {
                MeterDots(
                    theme: lightTheme,
                    running: store.state?.audioRunning == true,
                    inputLevel: store.state?.inputLevel ?? 0,
                    outputLevel: store.state?.outputLevel ?? 0
                )
                    .onHoverHint("in / out level", $hint)

                Button {
                    Task { await store.toggleAutoListen() }
                } label: {
                    Text("auto")
                        .font(.system(size: 10, weight: .semibold, design: .monospaced))
                        .foregroundStyle(store.state?.autoListen == true ? DashboardTheme.accent(lightTheme) : DashboardTheme.ghost(lightTheme))
                }
                .buttonStyle(.plain)
                .onHoverHint("auto mode — when on, recording automatically generates a second layer", $hint)

                HeaderGlyph("⚙", active: showSettings, theme: lightTheme) {
                    showSettings.toggle()
                }
                .onHoverHint("audio settings", $hint)

                HeaderGlyph(lightTheme ? "☀" : "☾", theme: lightTheme) {
                    lightTheme.toggle()
                }
                .onHoverHint("toggle theme", $hint)

                HeaderGlyph("▤", active: showLibrary, theme: lightTheme) {
                    showLibrary = true
                }
                .onHoverHint("open ORAM Library", $hint)

                HeaderGlyph("⌘", active: showProvider, theme: lightTheme) {
                    showProvider = true
                }
                .onHoverHint("provider setup / Keychain", $hint)

                HeaderGlyph("⌕", active: showCommandPalette, theme: lightTheme) {
                    showCommandPalette = true
                }
                .onHoverHint("command palette (⌘K)", $hint)
            }
        }
        .padding(.vertical, 4)
    }

    // modeLetter removed — replaced by auto-mode toggle

    private var hintBar: some View {
        HStack {
            Text(hint)
                .font(.system(size: 10, weight: .medium, design: .monospaced))
                .foregroundStyle(hint == "hover over a button for details" ? DashboardTheme.dim(lightTheme) : DashboardTheme.accent(lightTheme))
                .lineLimit(1)
                .truncationMode(.tail)
            Spacer()
        }
        .frame(height: 18)
        .padding(.horizontal, 2)
    }

    private var masterRecordHint: String {
        if store.state?.masterRecording == true {
            return String(format: "stop master recording — %.1fs captured", store.state?.masterRecordingSeconds ?? 0)
        }
        return "record master output — click to start a custom-length capture"
    }

    private var settingsPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("audio · engine settings")
                    .font(.system(size: 10, weight: .medium, design: .monospaced))
                    .tracking(1.5)
                    .textCase(.uppercase)
                    .foregroundStyle(DashboardTheme.dim(lightTheme))
                Spacer()
                Button("×") {
                    showSettings = false
                }
                .buttonStyle(.plain)
                .foregroundStyle(DashboardTheme.secondary(lightTheme))
            }

            HStack(alignment: .bottom, spacing: 10) {
                DeviceMenu(
                    title: "input device",
                    selection: $inputDeviceDraft,
                    devices: inputDevices,
                    defaultDeviceID: store.devices?.defaultInput,
                    systemLabel: "system input",
                    theme: lightTheme
                )
                DeviceMenu(
                    title: "output device",
                    selection: $outputDeviceDraft,
                    devices: outputDevices,
                    defaultDeviceID: store.devices?.defaultOutput,
                    systemLabel: "system output",
                    theme: lightTheme
                )
                SettingMenu(
                    title: "sample rate",
                    selection: $sampleRateDraft,
                    values: [44100, 48000, 96000],
                    label: { "\($0) Hz" },
                    theme: lightTheme
                )
                SettingMenu(
                    title: "block size",
                    selection: $blockSizeDraft,
                    values: [128, 256, 512, 1024],
                    label: { "\($0)" },
                    theme: lightTheme
                )
                Button("apply") {
                    Task {
                        await store.updateAudioSettings(
                            sampleRate: sampleRateDraft,
                            blockSize: blockSizeDraft,
                            inputDevice: inputDeviceDraft,
                            outputDevice: outputDeviceDraft
                        )
                    }
                }
                .buttonStyle(ApplyButtonStyle(theme: lightTheme))
                Spacer(minLength: 0)
            }

            HStack(alignment: .bottom, spacing: 10) {
                StringSettingMenu(
                    title: "runtime",
                    selection: $runtimeMode,
                    values: [
                        ("api", "API / auto"),
                        ("local", "Local")
                    ],
                    theme: lightTheme
                )
                EngineMenu(
                    title: "generator",
                    selection: $selectedModel,
                    engines: generationEngines,
                    theme: lightTheme
                )
                .disabled(runtimeMode == "local")
                .opacity(runtimeMode == "local" ? 0.58 : 1)
                SettingBlock(title: "library", value: store.state?.libraryDir ?? "ORAM Library", theme: lightTheme)
                Spacer(minLength: 0)
            }

            if runtimeMode == "local" {
                stableAudioInlinePanel
            }
        }
        .padding(12)
        .background(DashboardTheme.settings(lightTheme), in: RoundedRectangle(cornerRadius: 5))
        .overlay(
            RoundedRectangle(cornerRadius: 5)
                .stroke(DashboardTheme.borderActive(lightTheme), lineWidth: 1)
        )
    }

    private var promptModule: some View {
        HStack(spacing: 10) {
            TextField("describe a sound or type a command...", text: $prompt)
                .textFieldStyle(.plain)
                .font(.system(size: 18, design: .monospaced))
                .foregroundStyle(DashboardTheme.text(lightTheme))
                .onSubmit { submitPrompt() }

            Button("↵") {
                submitPrompt()
            }
            .buttonStyle(.plain)
            .font(.system(size: 20, weight: .semibold, design: .monospaced))
            .foregroundStyle(DashboardTheme.accent(lightTheme))
        }
        .padding(.horizontal, 14)
        .frame(height: 72)
        .background(DashboardTheme.inset(lightTheme), in: RoundedRectangle(cornerRadius: 5))
        .overlay(
            RoundedRectangle(cornerRadius: 5)
                .stroke(store.state?.recording == true ? DashboardTheme.record : DashboardTheme.border(lightTheme), lineWidth: 1)
        )
    }

    private var fxPalette: some View {
        VStack(alignment: .leading, spacing: 10) {
            PaletteHeader(title: "dsp effects", theme: lightTheme) {
                showFX = false
            }

            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 8) {
                ForEach(fxCommands, id: \.name) { item in
                    Button {
                        Task { await store.sendCommand(item.command) }
                        showFX = false
                    } label: {
                        VStack(spacing: 4) {
                            Text(item.glyph)
                                .font(.system(size: 18, weight: .semibold, design: .monospaced))
                            Text(item.name)
                                .font(.system(size: 10, weight: .medium, design: .monospaced))
                        }
                        .frame(maxWidth: .infinity, minHeight: 52)
                    }
                    .buttonStyle(ChipButtonStyle(theme: lightTheme))
                    .onHoverHint(item.hint, $hint)
                }
            }
        }
        .padding(12)
        .dashboardPanel(theme: lightTheme)
    }

    private var layersPanel: some View {
        VStack(spacing: 0) {
            ForEach(visibleLayers) { layer in
                DashboardLayerRow(
                    layer: layer,
                    waveform: store.waveforms[layer.slot],
                    selected: layer.slot == store.state?.selectedLayer,
                    theme: lightTheme,
                    onHint: { hint = $0 },
                    onClearHint: { hint = "hover over a button for details" },
                    onCommand: { command in
                        Task { await store.sendCommand(command) }
                    },
                    onGenerate: {
                        Task {
                            if runtimeMode == "local" {
                                await store.stableAudioRender(stableAudioLayerPayload(sourceLayer: layer.slot))
                            } else {
                                await store.generateFromLayer(layer.slot, engine: selectedModel)
                            }
                        }
                    },
                    onExport: {
                        Task { await store.exportLayer(layer.slot) }
                    },
                    onClear: {
                        Task { await store.clearLayer(layer.slot) }
                    },
                    onVolume: { value in
                        Task { await store.setVolume(layer: layer.slot, volume: value) }
                    },
                    onReversePlayback: {
                        Task {
                            await store.togglePlaybackReverse(
                                layer: layer.slot,
                                enabled: !(layer.playbackReverse ?? false)
                            )
                        }
                    },
                    onLoopRegion: { start, end, enabled in
                        Task {
                            await store.setLoopRegion(
                                layer: layer.slot,
                                startPct: start,
                                endPct: end,
                                enabled: enabled
                            )
                        }
                    },
                    onLoopFades: { fadeIn, fadeOut in
                        Task {
                            await store.setLoopFades(layer: layer.slot, fadeInPct: fadeIn, fadeOutPct: fadeOut)
                        }
                    },
                    onInpaintRegions: { regions in
                        Task {
                            await store.setInpaintRegions(layer: layer.slot, regions: regions)
                        }
                    }
                )
            }
        }
        .background(DashboardTheme.raised(lightTheme), in: RoundedRectangle(cornerRadius: 5))
        .overlay(
            RoundedRectangle(cornerRadius: 5)
                .stroke(DashboardTheme.border(lightTheme), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 5))
    }

    private var visibleLayers: [LayerState] {
        let stateLayers = store.state?.layers ?? []
        let layers = stateLayers.isEmpty ? LayerState.placeholderLayers : stateLayers
        if showFourthLayer || layers.dropFirst(3).contains(where: { $0.state != "empty" }) {
            return layers
        }
        return Array(layers.prefix(3))
    }

    private var logPanel: some View {
        VStack(spacing: 0) {
            Button {
                showLog.toggle()
            } label: {
                HStack(spacing: 8) {
                    Text(Date.now.formatted(date: .omitted, time: .standard))
                        .foregroundStyle(DashboardTheme.logTime(lightTheme))
                    Text("·")
                        .foregroundStyle(DashboardTheme.dim(lightTheme))
                    Text(store.state?.log.last ?? store.errorMessage ?? "ready")
                        .lineLimit(1)
                        .truncationMode(.tail)
                        .foregroundStyle(DashboardTheme.secondary(lightTheme))
                    Spacer()
                    Text(showLog ? "▾" : "▸")
                        .foregroundStyle(DashboardTheme.dim(lightTheme))
                }
                .font(.system(size: 11, design: .monospaced))
                .padding(.horizontal, 12)
                .frame(height: 34)
            }
            .buttonStyle(.plain)
            .onHoverHint("show / hide agent log", $hint)

            if showLog {
                VStack(alignment: .leading, spacing: 6) {
                    Text("⧫ agent log")
                        .font(.system(size: 10, weight: .semibold, design: .monospaced))
                        .tracking(1.2)
                        .foregroundStyle(DashboardTheme.dim(lightTheme))
                    ForEach((store.state?.log ?? []).suffix(10), id: \.self) { line in
                        Text(line)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(logColor(line))
                            .lineLimit(2)
                            .textSelection(.enabled)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
            }
        }
        .background(DashboardTheme.logBackground(lightTheme), in: RoundedRectangle(cornerRadius: 5))
        .overlay(
            RoundedRectangle(cornerRadius: 5)
                .stroke(DashboardTheme.logBorder(lightTheme), lineWidth: 1)
        )
    }

    private func submitPrompt() {
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        prompt = ""
        if runtimeMode == "local" {
            Task {
                await store.stableAudioRender(stableAudioTextPayload(prompt: text))
            }
        } else if text.lowercased().contains("generate") || text.lowercased().contains("summon") || text.count > 40 {
            Task {
                await store.generate(
                    prompt: text,
                    duration: 8,
                    provider: selectedProvider,
                    model: selectedModel,
                    tags: []
                )
            }
        } else {
            Task { await store.sendCommand(text) }
        }
    }

    private func cycleSelectedLayer() async {
        let current = store.state?.selectedLayer ?? 1
        let next = current >= 4 ? 1 : current + 1
        await store.sendCommand("select layer \(next)")
    }

    private var generationEngines: [ProviderEngine] {
        let candidates = store.providers.filter { engine in
            engine.capabilities.contains("text_to_sound_effect")
                || engine.capabilities.contains("text_to_music")
        }
        guard !candidates.isEmpty else { return [] }
        return candidates.sorted { lhs, rhs in
            if lhs.available != rhs.available { return lhs.available && !rhs.available }
            if lhs.provider != rhs.provider { return lhs.provider < rhs.provider }
            return lhs.label < rhs.label
        }
    }

    private var inputDevices: [AudioDevice] {
        (store.devices?.devices ?? []).filter(\.isInput)
    }

    private var outputDevices: [AudioDevice] {
        (store.devices?.devices ?? []).filter(\.isOutput)
    }

    private var stableModeRequiresSource: Bool {
        stableAudioModeRequiresSource(stableMode)
    }

    private var stableAudioLayerMode: String {
        stableModeRequiresSource ? stableMode : "morph"
    }

    private func stableAudioModeRequiresSource(_ mode: String) -> Bool {
        mode == "morph" || mode == "continue" || mode == "inpaint" || mode == "latent"
    }

    private func syncDeviceDrafts() {
        inputDeviceDraft = store.devices?.currentInput ?? -1
        outputDeviceDraft = store.devices?.currentOutput ?? -1
    }

    private var selectedProvider: String {
        generationEngines.first { $0.id == selectedModel }?.provider ?? "auto"
    }

    private func ensureSelectedModel() {
        let ids = Set(generationEngines.map(\.id))
        if ids.contains(selectedModel) {
            return
        }
        if ids.contains("stable-audio-3-local") {
            selectedModel = "stable-audio-3-local"
        } else if let first = generationEngines.first {
            selectedModel = first.id
        }
    }

    private var stableAudioInlinePanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .bottom, spacing: 10) {
                StringSettingMenu(
                    title: "mode",
                    selection: $stableMode,
                    values: [
                        ("generate", "Generate"),
                        ("morph", "Morph"),
                        ("continue", "Continue"),
                        ("inpaint", "Inpaint"),
                        ("lora_mixer", "LoRA")
                    ],
                    theme: lightTheme
                )
                StringSettingMenu(
                    title: "runtime",
                    selection: $stableLocalProvider,
                    values: [
                        ("stable_audio_mlx", "MLX"),
                        ("stable_audio_python", "Python"),
                        ("mock", "Mock")
                    ],
                    theme: lightTheme
                )
                StringSettingMenu(
                    title: "model",
                    selection: $stableLocalModel,
                    values: [
                        ("sm-music", "Small Music"),
                        ("sm-sfx", "Small SFX"),
                        ("medium", "Medium"),
                        ("medium-mlx", "Medium MLX")
                    ],
                    theme: lightTheme
                )
                StringSettingMenu(
                    title: "decoder",
                    selection: $stableDecoder,
                    values: [
                        ("same-s", "same-s"),
                        ("same-l", "same-l")
                    ],
                    theme: lightTheme
                )
                Toggle("chunked", isOn: $stableChunkedDecode)
                    .toggleStyle(.checkbox)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(DashboardTheme.secondary(lightTheme))
                    .frame(height: 30)
            }

            HStack(alignment: .bottom, spacing: 10) {
                NumericSettingField(title: "duration", value: $stableDuration, suffix: "s", theme: lightTheme)
                IntSettingField(title: "steps", value: $stableSteps, theme: lightTheme)
                NumericSettingField(title: "cfg", value: $stableCfgScale, suffix: "", theme: lightTheme)
                NumericSettingField(title: "noise", value: $stableNoiseDepth, suffix: "", theme: lightTheme)
                TextSettingField(title: "seed", value: $stableSeed, placeholder: "-1", theme: lightTheme)
                TextSettingField(title: "negative", value: $stableNegativePrompt, placeholder: "voice, speech", theme: lightTheme)
            }

            HStack(alignment: .bottom, spacing: 10) {
                TextSettingField(title: "service", value: $stableServiceURL, placeholder: "http://127.0.0.1:8765", theme: lightTheme)
                SettingBlock(
                    title: "routing",
                    value: "prompt text-to-audio / layer audio-to-audio",
                    theme: lightTheme
                )
                Spacer(minLength: 0)
            }
        }
        .padding(10)
        .background(DashboardTheme.inset(lightTheme), in: RoundedRectangle(cornerRadius: 5))
        .overlay(
            RoundedRectangle(cornerRadius: 5)
                .stroke(DashboardTheme.border(lightTheme), lineWidth: 1)
        )
    }

    private func stableAudioTextPayload(prompt text: String) -> StableAudioRenderPayload {
        stableAudioPayload(prompt: text, mode: "generate", sourceLayer: nil)
    }

    private func stableAudioLayerPayload(sourceLayer: Int) -> StableAudioRenderPayload {
        let trimmedPrompt = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        let sourcePrompt: String
        if trimmedPrompt.isEmpty {
            sourcePrompt = "transform layer \(sourceLayer) into a complementary texture"
        } else {
            sourcePrompt = trimmedPrompt
        }
        return stableAudioPayload(prompt: sourcePrompt, mode: stableAudioLayerMode, sourceLayer: sourceLayer)
    }

    private func stableAudioPayload(prompt text: String, mode: String, sourceLayer: Int?) -> StableAudioRenderPayload {
        StableAudioRenderPayload(
            prompt: text,
            mode: mode,
            duration: stableDuration,
            provider: "local",
            model: "stable-audio-3-local",
            decoder: stableDecoder,
            localProvider: stableLocalProvider,
            localModel: stableLocalModel,
            serviceURL: stableServiceURL,
            chunkedDecode: stableChunkedDecode,
            sourceLayer: sourceLayer,
            targetLayer: "first_empty",
            assignLayer: true,
            tags: ["stable-audio", "mode:\(mode)", sourceLayer == nil ? "workflow:text-to-audio" : "workflow:audio-to-audio"],
            negativePrompt: stableNegativePrompt,
            seed: Int(stableSeed.trimmingCharacters(in: .whitespacesAndNewlines)),
            steps: stableSteps,
            cfgScale: stableCfgScale,
            noiseDepth: mode == "generate" ? nil : stableNoiseDepth,
            inpaintStart: nil,
            inpaintEnd: nil,
            variationCount: 1,
            loraStack: [],
            loraAPath: "",
            loraAStrength: 0,
            loraBPath: "",
            loraBStrength: 0,
            loraIntervalMin: 0,
            loraIntervalMax: 1
        )
    }

    private func logColor(_ line: String) -> Color {
        let lower = line.lowercased()
        if lower.contains("error") || lower.contains("failed") { return DashboardTheme.error }
        if lower.contains("generated") || lower.contains("prompt:") { return DashboardTheme.summon }
        if lower.contains("hears") || lower.contains("listening") { return DashboardTheme.generated }
        return DashboardTheme.secondary(lightTheme)
    }
}

private struct DashboardLayerRow: View {
    let layer: LayerState
    let waveform: WaveformPeaks?
    let selected: Bool
    let theme: Bool
    let onHint: (String) -> Void
    let onClearHint: () -> Void
    let onCommand: (String) -> Void
    let onGenerate: () -> Void
    let onExport: () -> Void
    let onClear: () -> Void
    let onVolume: (Double) -> Void
    let onReversePlayback: () -> Void
    let onLoopRegion: (Double, Double, Bool) -> Void
    let onLoopFades: (Double, Double) -> Void
    let onInpaintRegions: ([InpaintRegionPayload]) -> Void

    @State private var loopDragStart: Double?
    @State private var loopDragCurrent: Double?

    var body: some View {
        ZStack {
            HStack(spacing: 12) {
                waveformPanel

                VStack(alignment: .trailing, spacing: 5) {
                    HStack(spacing: 6) {
                        Text(layer.duration > 0 ? String(format: "%.1fs", layer.duration) : "")
                            .foregroundStyle(DashboardTheme.secondary(theme))
                        Text(stateTag)
                            .foregroundStyle(tagColor)
                    }
                    .font(.system(size: 10, design: .monospaced))

                    Text(metaText)
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundStyle(DashboardTheme.dim(theme))
                        .lineLimit(1)
                        .truncationMode(.middle)

                    if let loopText {
                        Text(loopText)
                            .font(.system(size: 9, design: .monospaced))
                            .foregroundStyle(DashboardTheme.accent(theme))
                            .lineLimit(1)
                    }

                    EffectChips(effects: layer.effects ?? [], theme: theme)
                    VolumeStrip(value: layer.volume, theme: theme, onCommit: onVolume)
                        .frame(width: 8, height: 58)
                }
                .frame(width: 88, alignment: .trailing)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 18)

            corner(
                "\(layer.slot)",
                role: .identity,
                alignment: .topLeading,
                hint: "click: select / toggle mute · right-click: solo"
            ) {
                if selected && layer.state != "empty" {
                    onCommand("mute layer \(layer.slot)")
                } else {
                    onCommand("select layer \(layer.slot)")
                }
            }
            .contextMenu {
                Button("Solo layer \(layer.slot)") {
                    onCommand("solo layer \(layer.slot)")
                }
                Button(layer.muted ? "Unmute layer \(layer.slot)" : "Mute layer \(layer.slot)") {
                    onCommand("mute layer \(layer.slot)")
                }
            }

            corner(
                "↶",
                role: .reverse,
                alignment: .topLeading,
                hint: layer.playbackReverse == true ? "reverse playback on — click to play forward" : "reverse playback — non-destructive",
                action: onReversePlayback
            )
            .offset(x: 52)

            corner(
                "↓",
                role: .export,
                alignment: .topTrailing,
                hint: "export / bounce layer to WAV",
                action: onExport
            )

            corner(
                "✦",
                role: .generate,
                alignment: .bottomLeading,
                hint: "auto-generate from what's sounding",
                action: onGenerate
            )

            corner(
                "⌫",
                role: .clear,
                alignment: .bottomTrailing,
                hint: "clear / delete layer audio",
                action: onClear
            )
        }
        .frame(maxWidth: .infinity)
        .background(rowBackground)
        .overlay(alignment: .leading) {
            Rectangle()
                .fill(selected ? DashboardTheme.accent(theme) : (layer.state == "recording" ? DashboardTheme.record : .clear))
                .frame(width: 2)
        }
    }

    private var waveformPanel: some View {
        GeometryReader { geo in
            ZStack {
                DashboardWaveformView(layer: layer, waveform: waveform, theme: theme)
                    .frame(width: geo.size.width, height: geo.size.height)
                    .opacity(layer.muted ? 0.42 : 1)

                if layer.state == "empty" {
                    Text("—")
                        .foregroundStyle(DashboardTheme.ghost(theme))
                }

                if let range = visibleLoopRange {
                    LoopRangeOverlay(startPct: range.start, endPct: range.end, theme: theme)
                        .allowsHitTesting(false)
                    FadeRampOverlay(
                        loopStartPct: range.start,
                        loopEndPct: range.end,
                        fadeInPct: currentFadeInPct,
                        fadeOutPct: currentFadeOutPct,
                        theme: theme
                    )
                    .allowsHitTesting(false)
                }

                InpaintRegionsOverlay(regions: layer.inpaintRegions ?? [], theme: theme)
                    .allowsHitTesting(false)

                if layer.state != "empty", layer.loopEnabled == true, visibleLoopRange != nil {
                    FadeHandle(kind: .in, pct: fadeInHandlePct, theme: theme)
                        .highPriorityGesture(fadeGesture(kind: .in, width: geo.size.width))
                    FadeHandle(kind: .out, pct: fadeOutHandlePct, theme: theme)
                        .highPriorityGesture(fadeGesture(kind: .out, width: geo.size.width))
                }

                if layer.state != "empty" {
                    Rectangle()
                        .fill(DashboardTheme.text(theme).opacity(0.62))
                        .frame(width: 1.4)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .offset(x: geo.size.width * CGFloat(visualPlayheadPct / 100))
                        .allowsHitTesting(false)
                }
            }
            .contentShape(Rectangle())
            .gesture(loopGesture(width: geo.size.width))
            .contextMenu {
                Button("Mark loop as inpaint") {
                    if let range = persistedLoopRange {
                        onInpaintRegions([InpaintRegionPayload(startPct: range.start, endPct: range.end)])
                    }
                }
                .disabled(persistedLoopRange == nil)

                Button("Clear inpaint regions") {
                    onInpaintRegions([])
                }
                .disabled((layer.inpaintRegions ?? []).isEmpty)
            }
        }
        .frame(height: 74)
        .frame(maxWidth: .infinity)
        .background(DashboardTheme.inset(theme), in: RoundedRectangle(cornerRadius: 4))
        .overlay(
            RoundedRectangle(cornerRadius: 4)
                .stroke(DashboardTheme.border(theme), lineWidth: 1)
        )
    }

    private var visibleLoopRange: (start: Double, end: Double)? {
        if let start = loopDragStart, let current = loopDragCurrent {
            return (min(start, current), max(start, current))
        }
        guard layer.loopEnabled == true, layer.state != "empty" else {
            return nil
        }
        return (
            min(max(layer.loopStartPct ?? 0, 0), 100),
            min(max(layer.loopEndPct ?? 100, 0), 100)
        )
    }

    private var persistedLoopRange: (start: Double, end: Double)? {
        guard layer.loopEnabled == true, layer.state != "empty" else {
            return nil
        }
        let start = min(max(layer.loopStartPct ?? 0, 0), 100)
        let end = min(max(layer.loopEndPct ?? 100, 0), 100)
        guard end - start >= 1 else { return nil }
        return (start, end)
    }

    private var loopLengthPct: Double {
        guard let range = persistedLoopRange else { return 100 }
        return max(0, range.end - range.start)
    }

    private var currentFadeInPct: Double {
        min(max(layer.loopFadeInPct ?? 0, 0), loopLengthPct)
    }

    private var currentFadeOutPct: Double {
        min(max(layer.loopFadeOutPct ?? 0, 0), loopLengthPct)
    }

    private var fadeInHandlePct: Double {
        guard let range = persistedLoopRange else { return 0 }
        return min(range.end, range.start + currentFadeInPct)
    }

    private var fadeOutHandlePct: Double {
        guard let range = persistedLoopRange else { return 100 }
        return max(range.start, range.end - currentFadeOutPct)
    }

    private var visualPlayheadPct: Double {
        let raw = min(max(layer.playheadPct ?? 0, 0), 100)
        guard layer.playbackReverse == true else { return raw }
        if let range = persistedLoopRange {
            return min(max(range.start + range.end - raw, range.start), range.end)
        }
        return 100 - raw
    }

    private func fadeGesture(kind: FadeHandle.Kind, width: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onEnded { value in
                guard let range = persistedLoopRange, width > 0 else { return }
                let pct = pctFrom(x: value.location.x, width: width)
                let fadeIn: Double
                let fadeOut: Double
                switch kind {
                case .in:
                    fadeIn = min(max(pct - range.start, 0), range.end - range.start)
                    fadeOut = currentFadeOutPct
                case .out:
                    fadeIn = currentFadeInPct
                    fadeOut = min(max(range.end - pct, 0), range.end - range.start)
                }
                onLoopFades(fadeIn, fadeOut)
            }
    }

    private func loopGesture(width: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                guard layer.state != "empty", width > 0 else { return }
                let pct = pctFrom(x: value.location.x, width: width)
                if loopDragStart == nil {
                    loopDragStart = pct
                }
                loopDragCurrent = pct
            }
            .onEnded { value in
                defer {
                    loopDragStart = nil
                    loopDragCurrent = nil
                }
                guard layer.state != "empty", width > 0, let start = loopDragStart else { return }
                let end = pctFrom(x: value.location.x, width: width)
                let lo = min(start, end)
                let hi = max(start, end)
                if hi - lo >= 1 {
                    onLoopRegion(lo, hi, true)
                } else if layer.loopEnabled == true {
                    onLoopRegion(0, 100, false)
                }
            }
    }

    private func pctFrom(x: CGFloat, width: CGFloat) -> Double {
        Double(min(max(x / max(width, 1), 0), 1) * 100)
    }

    private var rowBackground: Color {
        if layer.state == "recording" { return DashboardTheme.record.opacity(0.14) }
        if selected { return DashboardTheme.selected(theme) }
        return DashboardTheme.raised(theme)
    }

    private var stateTag: String {
        if layer.state == "recording" { return "rec" }
        if layer.isGenerated { return "gen d\(layer.generationDepth ?? 0)" }
        if layer.muted { return "muted" }
        if layer.solo { return "solo" }
        return ""
    }

    private var tagColor: Color {
        if layer.isGenerated { return DashboardTheme.generated }
        if layer.state == "recording" { return DashboardTheme.record }
        if layer.solo { return DashboardTheme.solo }
        return DashboardTheme.dim(theme)
    }

    private var metaText: String {
        if let prompt = layer.generationPrompt, !prompt.isEmpty {
            return prompt
        }
        if layer.state == "empty" {
            return "empty"
        }
        if layer.playbackReverse == true {
            return "\(layer.layerMode ?? layer.sourceType) · reverse"
        }
        return layer.layerMode ?? layer.sourceType
    }

    private var loopText: String? {
        guard layer.loopEnabled == true, layer.state != "empty" else { return nil }
        let start = layer.loopStartSeconds ?? 0
        let end = layer.loopEndSeconds ?? layer.duration
        let fades = (layer.loopFadeInSeconds ?? 0) > 0 || (layer.loopFadeOutSeconds ?? 0) > 0
            ? String(format: " · f %.2f/%.2f", layer.loopFadeInSeconds ?? 0, layer.loopFadeOutSeconds ?? 0)
            : ""
        let inpaint = (layer.inpaintRegions ?? []).isEmpty ? "" : " · ip \((layer.inpaintRegions ?? []).count)"
        return String(format: "%.2f-%.2f", start, end) + fades + inpaint
    }

    private func corner(
        _ text: String,
        role: LayerCornerRole,
        alignment: Alignment,
        hint: String,
        action: @escaping () -> Void
    ) -> some View {
        ZStack {
            Button(action: action) {
                Text(text)
                    .font(.system(size: text.count > 1 ? 10 : 12, weight: .bold, design: .monospaced))
                    .foregroundStyle(cornerColor(role))
                    .opacity(role == .identity ? identityOpacity : 0.62)
                    .frame(width: 68, height: 30, alignment: textAlignment(alignment))
                    .padding(.horizontal, 12)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .frame(width: 92, height: 30)
            .background(cornerGlow(role, alignment: alignment))
            .onHover { hovering in
                hovering ? onHint(hint) : onClearHint()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: alignment)
    }

    private func cornerColor(_ role: LayerCornerRole) -> Color {
        switch role {
        case .identity:
            if layer.solo { return DashboardTheme.solo }
            if layer.muted { return DashboardTheme.dim(theme) }
            return layerColor
        case .export:
            return DashboardTheme.generated
        case .generate:
            return DashboardTheme.summon
        case .clear:
            return DashboardTheme.record
        case .reverse:
            return layer.playbackReverse == true ? DashboardTheme.solo : DashboardTheme.accent(theme)
        }
    }

    private var layerColor: Color {
        switch layer.slot {
        case 1: DashboardTheme.accent(theme)
        case 2: DashboardTheme.generated
        case 3: DashboardTheme.summon
        case 4: DashboardTheme.solo
        default: DashboardTheme.accent(theme)
        }
    }

    private var identityOpacity: Double {
        if selected { return 1 }
        if layer.state == "empty" { return 0.52 }
        return 0.88
    }

    private func textAlignment(_ alignment: Alignment) -> Alignment {
        switch alignment {
        case .topLeading, .bottomLeading:
            return .leading
        case .topTrailing, .bottomTrailing:
            return .trailing
        default:
            return .center
        }
    }

    private func cornerGlow(_ role: LayerCornerRole, alignment: Alignment) -> some View {
        Rectangle()
            .fill(
                LinearGradient(
                    colors: [
                        cornerColor(role).opacity(role == .identity ? (selected ? 0.22 : 0.12) : 0.06),
                        .clear
                    ],
                    startPoint: gradientStart(alignment),
                    endPoint: .center
                )
            )
    }

    private func gradientStart(_ alignment: Alignment) -> UnitPoint {
        switch alignment {
        case .topLeading: return .topLeading
        case .topTrailing: return .topTrailing
        case .bottomLeading: return .bottomLeading
        case .bottomTrailing: return .bottomTrailing
        default: return .center
        }
    }

    private enum LayerCornerRole {
        case identity
        case export
        case generate
        case clear
        case reverse
    }
}

private struct PaletteHeader: View {
    let title: String
    let theme: Bool
    let close: () -> Void

    var body: some View {
        HStack {
            Text(title)
                .font(.system(size: 10, weight: .medium, design: .monospaced))
                .tracking(1.5)
                .textCase(.uppercase)
                .foregroundStyle(DashboardTheme.dim(theme))
            Spacer()
            Button("×", action: close)
                .buttonStyle(.plain)
                .foregroundStyle(DashboardTheme.secondary(theme))
        }
    }
}

private struct DashboardWaveformView: View {
    let layer: LayerState
    let waveform: WaveformPeaks?
    let theme: Bool

    var body: some View {
        Canvas { context, size in
            let values = layer.waveform.isEmpty ? Array(repeating: 0.0, count: 64) : layer.waveform
            let midY = size.height / 2

            var center = Path()
            center.move(to: CGPoint(x: 0, y: midY))
            center.addLine(to: CGPoint(x: size.width, y: midY))
            context.stroke(center, with: .color(DashboardTheme.border(theme).opacity(0.55)), lineWidth: 1)

            for index in 1..<4 {
                var grid = Path()
                let x = size.width * CGFloat(index) / 4
                grid.move(to: CGPoint(x: x, y: 0))
                grid.addLine(to: CGPoint(x: x, y: size.height))
                context.stroke(grid, with: .color(DashboardTheme.border(theme).opacity(0.22)), lineWidth: 1)
            }

            let hasPeaks = !(waveform?.peaks.isEmpty ?? true)
            if values.allSatisfy({ $0 == 0 }) && !hasPeaks {
                var dash = Path()
                dash.move(to: CGPoint(x: 0, y: midY))
                dash.addLine(to: CGPoint(x: size.width, y: midY))
                context.stroke(
                    dash,
                    with: .color(DashboardTheme.border(theme)),
                    style: StrokeStyle(lineWidth: 1, dash: [4, 4])
                )
                return
            }

            let maxValue = max(values.max() ?? 0.001, 0.001)
            let color = waveformColor
            if let peaks = waveform?.peaks, !peaks.isEmpty {
                let maxPeak = peaks.reduce(0.001) { partial, pair in
                    let lo = pair.indices.contains(0) ? abs(pair[0]) : 0
                    let hi = pair.indices.contains(1) ? abs(pair[1]) : 0
                    return max(partial, lo, hi)
                }
                let amp = (size.height / 2 - 3) / CGFloat(maxPeak)
                let step = size.width / CGFloat(max(peaks.count - 1, 1))
                for index in peaks.indices {
                    let pair = peaks[index]
                    let gain = fadeGain(at: Double(index) / Double(max(peaks.count - 1, 1)) * 100)
                    let minValue = (pair.indices.contains(0) ? pair[0] : 0) * gain
                    let maxValue = (pair.indices.contains(1) ? pair[1] : 0) * gain
                    let x = CGFloat(index) * step
                    var path = Path()
                    path.move(to: CGPoint(x: x, y: midY - CGFloat(maxValue) * amp))
                    path.addLine(to: CGPoint(x: x, y: midY - CGFloat(minValue) * amp))
                    context.stroke(path, with: .color(color.opacity(0.92)), lineWidth: 1)
                }
            } else {
                let barWidth = size.width / CGFloat(max(values.count, 1))
                for index in values.indices {
                    let gain = fadeGain(at: Double(index) / Double(max(values.count - 1, 1)) * 100)
                    let normalized = CGFloat(max(0, min(1, (values[index] * gain) / maxValue)))
                    let height = max(1, normalized * (size.height - 6))
                    let rect = CGRect(
                        x: CGFloat(index) * barWidth + 0.5,
                        y: midY - height / 2,
                        width: max(1, barWidth - 1),
                        height: height
                    )
                    context.fill(Path(rect), with: .color(color.opacity(0.35 + Double(normalized) * 0.55)))
                }
            }
        }
    }

    private var waveformColor: Color {
        if layer.muted || layer.state == "muted" {
            return DashboardTheme.dim(theme)
        }
        if layer.isGenerated {
            return DashboardTheme.generated
        }
        if layer.state == "active" {
            return DashboardTheme.accent(theme)
        }
        return DashboardTheme.secondary(theme)
    }

    private func fadeGain(at pct: Double) -> Double {
        guard layer.loopEnabled == true, layer.state != "empty" else { return 1 }
        let start = min(max(layer.loopStartPct ?? 0, 0), 100)
        let end = min(max(layer.loopEndPct ?? 100, 0), 100)
        guard end > start else { return 1 }
        let fadeIn = min(max(layer.loopFadeInPct ?? 0, 0), end - start)
        let fadeOut = min(max(layer.loopFadeOutPct ?? 0, 0), end - start)
        var gain = 1.0
        if fadeIn > 0, pct >= start, pct <= start + fadeIn {
            gain = min(gain, max(0, (pct - start) / fadeIn))
        }
        if fadeOut > 0, pct >= end - fadeOut, pct <= end {
            gain = min(gain, max(0, (end - pct) / fadeOut))
        }
        return gain
    }
}

private struct LoopRangeOverlay: View {
    let startPct: Double
    let endPct: Double
    let theme: Bool

    var body: some View {
        GeometryReader { geo in
            let start = CGFloat(min(max(startPct, 0), 100)) / 100
            let end = CGFloat(min(max(endPct, 0), 100)) / 100
            Rectangle()
                .fill(DashboardTheme.accent(theme).opacity(0.14))
                .frame(width: max(0, geo.size.width * (end - start)))
                .offset(x: geo.size.width * start)
        }
    }
}

private struct FadeRampOverlay: View {
    let loopStartPct: Double
    let loopEndPct: Double
    let fadeInPct: Double
    let fadeOutPct: Double
    let theme: Bool

    var body: some View {
        Canvas { context, size in
            let loopStart = CGFloat(min(max(loopStartPct, 0), 100)) / 100 * size.width
            let loopEnd = CGFloat(min(max(loopEndPct, 0), 100)) / 100 * size.width
            let fadeInEnd = min(loopEnd, loopStart + CGFloat(max(0, fadeInPct)) / 100 * size.width)
            let fadeOutStart = max(loopStart, loopEnd - CGFloat(max(0, fadeOutPct)) / 100 * size.width)
            let color = DashboardTheme.accent(theme).opacity(0.9)

            if fadeInEnd - loopStart > 1 {
                var path = Path()
                path.move(to: CGPoint(x: loopStart, y: size.height - 4))
                path.addLine(to: CGPoint(x: fadeInEnd, y: 4))
                context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: 1.5))
            }

            if loopEnd - fadeOutStart > 1 {
                var path = Path()
                path.move(to: CGPoint(x: fadeOutStart, y: 4))
                path.addLine(to: CGPoint(x: loopEnd, y: size.height - 4))
                context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: 1.5))
            }
        }
    }
}

private struct FadeHandle: View {
    enum Kind {
        case `in`
        case out
    }

    let kind: Kind
    let pct: Double
    let theme: Bool

    var body: some View {
        GeometryReader { geo in
            RoundedRectangle(cornerRadius: 2)
                .fill(DashboardTheme.accent(theme))
                .frame(width: 8, height: 18)
                .overlay(
                    RoundedRectangle(cornerRadius: 2)
                        .stroke(DashboardTheme.background(theme), lineWidth: 1)
                )
                .offset(
                    x: geo.size.width * CGFloat(min(max(pct, 0), 100) / 100) - 4,
                    y: kind == .in ? 4 : geo.size.height - 22
                )
        }
    }
}

private struct InpaintRegionsOverlay: View {
    let regions: [LayerInpaintRegion]
    let theme: Bool

    var body: some View {
        GeometryReader { geo in
            ForEach(Array(regions.enumerated()), id: \.offset) { _, region in
                let start = CGFloat(min(max(region.startPct, 0), 100)) / 100
                let end = CGFloat(min(max(region.endPct, 0), 100)) / 100
                Rectangle()
                    .fill(DashboardTheme.summon.opacity(0.17))
                    .frame(width: max(1, geo.size.width * max(0, end - start)))
                    .offset(x: geo.size.width * start)
            }
        }
    }
}

private struct EffectChips: View {
    let effects: [String]
    let theme: Bool

    var body: some View {
        if !effects.isEmpty {
            HStack(spacing: 4) {
                ForEach(effects.prefix(3), id: \.self) { effect in
                    Text(effect)
                        .font(.system(size: 8, weight: .medium, design: .monospaced))
                        .foregroundStyle(DashboardTheme.accent(theme))
                        .lineLimit(1)
                        .padding(.horizontal, 4)
                        .frame(height: 14)
                        .background(DashboardTheme.accent(theme).opacity(0.08), in: RoundedRectangle(cornerRadius: 2))
                        .overlay(
                            RoundedRectangle(cornerRadius: 2)
                                .stroke(DashboardTheme.accent(theme).opacity(0.22), lineWidth: 1)
                        )
                }
            }
        }
    }
}

private struct SettingBlock: View {
    let title: String
    let value: String
    let theme: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 9, weight: .medium, design: .monospaced))
                .tracking(1)
                .textCase(.uppercase)
                .foregroundStyle(DashboardTheme.dim(theme))
            Text(value)
                .font(.system(size: 11, design: .monospaced))
                .lineLimit(1)
                .truncationMode(.middle)
                .padding(.horizontal, 8)
                .frame(height: 30)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(DashboardTheme.inset(theme), in: RoundedRectangle(cornerRadius: 4))
                .overlay(
                    RoundedRectangle(cornerRadius: 4)
                        .stroke(DashboardTheme.border(theme), lineWidth: 1)
                )
        }
        .frame(minWidth: 110)
    }
}

private struct SettingMenu: View {
    let title: String
    @Binding var selection: Int
    let values: [Int]
    let label: (Int) -> String
    let theme: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 9, weight: .medium, design: .monospaced))
                .tracking(1)
                .textCase(.uppercase)
                .foregroundStyle(DashboardTheme.dim(theme))
            Picker(title, selection: $selection) {
                ForEach(values, id: \.self) { value in
                    Text(label(value)).tag(value)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .frame(height: 30)
            .frame(maxWidth: .infinity)
            .background(DashboardTheme.inset(theme), in: RoundedRectangle(cornerRadius: 4))
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .stroke(DashboardTheme.border(theme), lineWidth: 1)
            )
        }
        .frame(minWidth: 110)
    }
}

private struct StringSettingMenu: View {
    let title: String
    @Binding var selection: String
    let values: [(value: String, label: String)]
    let theme: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 9, weight: .medium, design: .monospaced))
                .tracking(1)
                .textCase(.uppercase)
                .foregroundStyle(DashboardTheme.dim(theme))
            Picker(title, selection: $selection) {
                ForEach(values, id: \.value) { item in
                    Text(item.label).tag(item.value)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .frame(height: 30)
            .frame(maxWidth: .infinity)
            .background(DashboardTheme.inset(theme), in: RoundedRectangle(cornerRadius: 4))
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .stroke(DashboardTheme.border(theme), lineWidth: 1)
            )
        }
        .frame(minWidth: 120)
    }
}

private struct NumericSettingField: View {
    let title: String
    @Binding var value: Double
    let suffix: String
    let theme: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 9, weight: .medium, design: .monospaced))
                .tracking(1)
                .textCase(.uppercase)
                .foregroundStyle(DashboardTheme.dim(theme))
            HStack(spacing: 4) {
                TextField(title, value: $value, format: .number.precision(.fractionLength(2)))
                    .labelsHidden()
                    .textFieldStyle(.plain)
                    .font(.system(size: 11, design: .monospaced))
                if !suffix.isEmpty {
                    Text(suffix)
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(DashboardTheme.dim(theme))
                }
            }
            .padding(.horizontal, 8)
            .frame(height: 30)
            .background(DashboardTheme.inset(theme), in: RoundedRectangle(cornerRadius: 4))
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .stroke(DashboardTheme.border(theme), lineWidth: 1)
            )
        }
        .frame(minWidth: 86)
    }
}

private struct IntSettingField: View {
    let title: String
    @Binding var value: Int
    let theme: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 9, weight: .medium, design: .monospaced))
                .tracking(1)
                .textCase(.uppercase)
                .foregroundStyle(DashboardTheme.dim(theme))
            TextField(title, value: $value, format: .number)
                .labelsHidden()
                .textFieldStyle(.plain)
                .font(.system(size: 11, design: .monospaced))
                .padding(.horizontal, 8)
                .frame(height: 30)
                .background(DashboardTheme.inset(theme), in: RoundedRectangle(cornerRadius: 4))
                .overlay(
                    RoundedRectangle(cornerRadius: 4)
                        .stroke(DashboardTheme.border(theme), lineWidth: 1)
                )
        }
        .frame(minWidth: 76)
    }
}

private struct TextSettingField: View {
    let title: String
    @Binding var value: String
    let placeholder: String
    let theme: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 9, weight: .medium, design: .monospaced))
                .tracking(1)
                .textCase(.uppercase)
                .foregroundStyle(DashboardTheme.dim(theme))
            TextField(placeholder, text: $value)
                .labelsHidden()
                .textFieldStyle(.plain)
                .font(.system(size: 11, design: .monospaced))
                .padding(.horizontal, 8)
                .frame(height: 30)
                .background(DashboardTheme.inset(theme), in: RoundedRectangle(cornerRadius: 4))
                .overlay(
                    RoundedRectangle(cornerRadius: 4)
                        .stroke(DashboardTheme.border(theme), lineWidth: 1)
                )
        }
        .frame(minWidth: 100)
    }
}

private struct DeviceMenu: View {
    let title: String
    @Binding var selection: Int
    let devices: [AudioDevice]
    let defaultDeviceID: Int?
    let systemLabel: String
    let theme: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 9, weight: .medium, design: .monospaced))
                .tracking(1)
                .textCase(.uppercase)
                .foregroundStyle(DashboardTheme.dim(theme))
            Picker(title, selection: $selection) {
                Text(systemLabel).tag(-1)
                ForEach(devices) { device in
                    Text(label(for: device)).tag(device.id)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .frame(height: 30)
            .frame(maxWidth: .infinity)
            .background(DashboardTheme.inset(theme), in: RoundedRectangle(cornerRadius: 4))
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .stroke(DashboardTheme.border(theme), lineWidth: 1)
            )
        }
        .frame(minWidth: 190)
    }

    private func label(for device: AudioDevice) -> String {
        let channelCount = device.isInput ? device.maxInputChannels : device.maxOutputChannels
        let defaultSuffix = device.id == defaultDeviceID ? " · default" : ""
        return "\(device.name) (\(channelCount) ch\(defaultSuffix))"
    }
}

private struct EngineMenu: View {
    let title: String
    @Binding var selection: String
    let engines: [ProviderEngine]
    let theme: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.system(size: 9, weight: .medium, design: .monospaced))
                .tracking(1)
                .textCase(.uppercase)
                .foregroundStyle(DashboardTheme.dim(theme))
            Picker(title, selection: $selection) {
                if engines.isEmpty {
                    Text("Local").tag("stable-audio-3-local")
                } else {
                    ForEach(engines) { engine in
                        Text(label(for: engine))
                            .tag(engine.id)
                    }
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .frame(height: 30)
            .frame(maxWidth: .infinity)
            .background(DashboardTheme.inset(theme), in: RoundedRectangle(cornerRadius: 4))
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .stroke(DashboardTheme.border(theme), lineWidth: 1)
            )
        }
        .frame(minWidth: 150)
    }

    private func label(for engine: ProviderEngine) -> String {
        let state = engine.available ? "" : " · key"
        return "\(engine.label)\(state)"
    }
}

private struct OramLogoView: View {
    let size: CGFloat

    var body: some View {
        if let image = NSImage(named: NSImage.Name("logo-oram")) ?? bundleLogo {
            Image(nsImage: image)
                .resizable()
                .scaledToFill()
                .frame(width: size, height: size)
                .clipShape(RoundedRectangle(cornerRadius: 4))
        } else {
            RoundedRectangle(cornerRadius: 4)
                .fill(Color.primary.opacity(0.12))
                .frame(width: size, height: size)
                .overlay(
                    Text("o")
                        .font(.system(size: size * 0.55, weight: .semibold, design: .monospaced))
                )
        }
    }

    private var bundleLogo: NSImage? {
        guard let url = Bundle.main.url(forResource: "logo-oram", withExtension: "png") else {
            return nil
        }
        return NSImage(contentsOf: url)
    }
}

private struct HeaderSeparator: View {
    var body: some View {
        Rectangle()
            .fill(Color.white.opacity(0.10))
            .frame(width: 1, height: 22)
    }
}

private enum HeaderRole {
    case normal
    case record
    case fx
    case summon
    case danger
}

private struct HeaderGlyph: View {
    let title: String
    var active = false
    var role: HeaderRole = .normal
    let theme: Bool
    let action: () -> Void

    init(_ title: String, active: Bool = false, role: HeaderRole = .normal, theme: Bool, action: @escaping () -> Void) {
        self.title = title
        self.active = active
        self.role = role
        self.theme = theme
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: title.count > 1 ? 10 : 14, weight: .semibold, design: .monospaced))
                .frame(width: 30, height: 30)
        }
        .buttonStyle(HeaderButtonStyle(theme: theme, active: active, role: role))
    }
}

private struct HeaderButtonStyle: ButtonStyle {
    let theme: Bool
    var active = false
    var role: HeaderRole = .normal

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(foreground)
            .background(background, in: RoundedRectangle(cornerRadius: 4))
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .stroke(border, lineWidth: 1)
            )
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
    }

    private var foreground: Color {
        if active {
            return roleColor
        }
        return DashboardTheme.secondary(theme)
    }

    private var background: Color {
        active ? roleColor.opacity(0.10) : .clear
    }

    private var border: Color {
        active ? roleColor : DashboardTheme.border(theme)
    }

    private var roleColor: Color {
        switch role {
        case .record, .danger: DashboardTheme.record
        case .fx: DashboardTheme.accent(theme)
        case .summon: DashboardTheme.summon
        case .normal: DashboardTheme.accent(theme)
        }
    }
}

private struct ChipButtonStyle: ButtonStyle {
    let theme: Bool
    var role: HeaderRole = .normal

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(role == .summon ? DashboardTheme.summon : DashboardTheme.secondary(theme))
            .background(DashboardTheme.inset(theme), in: RoundedRectangle(cornerRadius: 4))
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .stroke(role == .summon ? DashboardTheme.summon.opacity(0.5) : DashboardTheme.border(theme), lineWidth: 1)
            )
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}

private struct ApplyButtonStyle: ButtonStyle {
    let theme: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 10, weight: .semibold, design: .monospaced))
            .tracking(1)
            .textCase(.uppercase)
            .foregroundStyle(configuration.isPressed ? DashboardTheme.background(theme) : DashboardTheme.accent(theme))
            .padding(.horizontal, 16)
            .frame(height: 30)
            .background(configuration.isPressed ? DashboardTheme.accent(theme) : DashboardTheme.accent(theme).opacity(0.10), in: RoundedRectangle(cornerRadius: 4))
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .stroke(DashboardTheme.accent(theme), lineWidth: 1)
            )
    }
}

private struct MeterDots: View {
    let theme: Bool
    let running: Bool
    let inputLevel: Double
    let outputLevel: Double

    var body: some View {
        HStack(spacing: 5) {
            Circle()
                .fill(dotColor(inputLevel))
                .frame(width: 8, height: 8)
            Circle()
                .fill(dotColor(outputLevel))
                .frame(width: 8, height: 8)
        }
    }

    private func dotColor(_ level: Double) -> Color {
        guard running else { return DashboardTheme.meterBackground(theme) }
        if level > 0.70 {
            return DashboardTheme.record
        }
        if level > 0.12 {
            return DashboardTheme.accent(theme)
        }
        if level > 0.01 {
            return DashboardTheme.generated
        }
        return DashboardTheme.meterBackground(theme)
    }
}

private struct VolumeStrip: View {
    let value: Double
    let theme: Bool
    let onCommit: (Double) -> Void
    @State private var draftValue: Double?

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .bottom) {
                Rectangle()
                    .fill(DashboardTheme.inset(theme))
                Rectangle()
                    .fill(fillColor.opacity(0.72))
                    .frame(height: geo.size.height * CGFloat(position(for: currentValue)))
                Rectangle()
                    .fill(DashboardTheme.secondary(theme).opacity(0.45))
                    .frame(height: 1)
                    .offset(y: -geo.size.height / 2)
            }
            .clipShape(RoundedRectangle(cornerRadius: 2))
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { gesture in
                        draftValue = valueFor(y: gesture.location.y, height: geo.size.height)
                    }
                    .onEnded { gesture in
                        let next = valueFor(y: gesture.location.y, height: geo.size.height)
                        draftValue = nil
                        onCommit(next)
                    }
            )
        }
    }

    private var currentValue: Double {
        draftValue ?? value
    }

    private var fillColor: Color {
        currentValue > 1.1 ? DashboardTheme.solo : DashboardTheme.accent(theme)
    }

    private func valueFor(y: CGFloat, height: CGFloat) -> Double {
        let clamped = max(0, min(height, height - y))
        let normalized = Double(clamped / max(height, 1))
        return pow(normalized, 1.8) * 2
    }

    private func position(for volume: Double) -> Double {
        let clamped = min(max(volume, 0), 2)
        guard clamped > 0 else { return 0 }
        return pow(clamped / 2, 1 / 1.8)
    }
}

private struct AboutOverlay: View {
    let theme: Bool
    let close: () -> Void
    let openPrivacy: () -> Void

    var body: some View {
        ZStack {
            Color.black.opacity(theme ? 0.18 : 0.48)
                .ignoresSafeArea()
                .onTapGesture(perform: close)

            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    HStack(spacing: 10) {
                        OramLogoView(size: 42)
                        Text("oram")
                            .font(.system(size: 24, weight: .semibold, design: .monospaced))
                            .foregroundStyle(DashboardTheme.accent(theme))
                    }
                    Spacer()
                    Button("×", action: close)
                        .buttonStyle(.plain)
                        .font(.system(size: 16, weight: .medium, design: .monospaced))
                        .foregroundStyle(DashboardTheme.secondary(theme))
                }

                Text("agentic audio looper")
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(DashboardTheme.secondary(theme))

                Text("ORAM opens in local mode and routes generation to the configured local audio service when available. API / auto mode can use configured ElevenLabs and Stability AI APIs. Keys stay server-side.")
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(DashboardTheme.dim(theme))
                    .fixedSize(horizontal: false, vertical: true)

                Text("macOS: quit sends kill-all audio first, then stops any daemon process launched by the app. The kill control stops recording and command capture, mutes every layer, and discards pending generation output.")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(DashboardTheme.dim(theme))
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: 8) {
                    MiniFact("open source", theme: theme)
                    MiniFact("no telemetry", theme: theme)
                    MiniFact("localhost daemon", theme: theme)
                    MiniFact("local archive", theme: theme)
                }

                HStack {
                    Button("privacy") {
                        openPrivacy()
                    }
                    .buttonStyle(ApplyButtonStyle(theme: theme))
                    Spacer()
                    Text("⌘K opens commands")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(DashboardTheme.dim(theme))
                }
            }
            .padding(18)
            .frame(width: 520)
            .background(DashboardTheme.raised(theme), in: RoundedRectangle(cornerRadius: 6))
            .overlay(
                RoundedRectangle(cornerRadius: 6)
                    .stroke(DashboardTheme.borderActive(theme), lineWidth: 1)
            )
        }
    }
}

private struct MiniFact: View {
    let text: String
    let theme: Bool

    init(_ text: String, theme: Bool) {
        self.text = text
        self.theme = theme
    }

    var body: some View {
        Text(text)
            .font(.system(size: 9, weight: .medium, design: .monospaced))
            .foregroundStyle(DashboardTheme.accent(theme))
            .lineLimit(1)
            .padding(.horizontal, 7)
            .frame(height: 22)
            .background(DashboardTheme.accent(theme).opacity(0.08), in: RoundedRectangle(cornerRadius: 4))
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .stroke(DashboardTheme.accent(theme).opacity(0.25), lineWidth: 1)
            )
    }
}

private struct CommandPaletteOverlay: View {
    let theme: Bool
    @Binding var search: String
    let close: () -> Void
    let run: (String) -> Void

    private let commands = [
        "record",
        "stop recording",
        "kill audio",
        "overdub",
        "listen to the texture",
        "export mix",
        "reverse",
        "make it slower",
        "make it faster",
        "make it darker",
        "granulate softly",
        "wash it in reverb",
        "make it far away",
        "stretch until it breathes",
        "add distant metallic rain",
        "add low drone",
        "add synthetic forest",
        "save session"
    ]

    var body: some View {
        ZStack {
            Color.black.opacity(theme ? 0.16 : 0.44)
                .ignoresSafeArea()
                .onTapGesture(perform: close)

            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("command")
                        .font(.system(size: 10, weight: .medium, design: .monospaced))
                        .tracking(1.5)
                        .textCase(.uppercase)
                        .foregroundStyle(DashboardTheme.dim(theme))
                    Spacer()
                    Button("×", action: close)
                        .buttonStyle(.plain)
                        .foregroundStyle(DashboardTheme.secondary(theme))
                }

                TextField("type a command...", text: $search)
                    .textFieldStyle(.plain)
                    .font(.system(size: 18, design: .monospaced))
                    .foregroundStyle(DashboardTheme.text(theme))
                    .padding(.horizontal, 12)
                    .frame(height: 48)
                    .background(DashboardTheme.inset(theme), in: RoundedRectangle(cornerRadius: 4))
                    .overlay(
                        RoundedRectangle(cornerRadius: 4)
                            .stroke(DashboardTheme.border(theme), lineWidth: 1)
                    )
                    .onSubmit {
                        let value = search.trimmingCharacters(in: .whitespacesAndNewlines)
                        if !value.isEmpty {
                            run(value)
                            search = ""
                        }
                    }

                VStack(spacing: 0) {
                    ForEach(filteredCommands, id: \.self) { command in
                        Button {
                            run(command)
                            search = ""
                        } label: {
                            HStack {
                                Text(command)
                                    .font(.system(size: 12, design: .monospaced))
                                    .foregroundStyle(DashboardTheme.secondary(theme))
                                Spacer()
                                Text("↵")
                                    .font(.system(size: 11, design: .monospaced))
                                    .foregroundStyle(DashboardTheme.dim(theme))
                            }
                            .frame(height: 32)
                            .padding(.horizontal, 10)
                        }
                        .buttonStyle(.plain)
                        Divider()
                            .background(DashboardTheme.border(theme))
                    }
                }
                .background(DashboardTheme.inset(theme), in: RoundedRectangle(cornerRadius: 4))
                .overlay(
                    RoundedRectangle(cornerRadius: 4)
                        .stroke(DashboardTheme.border(theme), lineWidth: 1)
                )
            }
            .padding(14)
            .frame(width: 520)
            .background(DashboardTheme.raised(theme), in: RoundedRectangle(cornerRadius: 6))
            .overlay(
                RoundedRectangle(cornerRadius: 6)
                    .stroke(DashboardTheme.borderActive(theme), lineWidth: 1)
            )
        }
    }

    private var filteredCommands: [String] {
        let needle = search.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !needle.isEmpty else {
            return Array(commands.prefix(8))
        }
        return commands.filter { $0.lowercased().contains(needle) }.prefix(8).map { $0 }
    }
}

private struct DashboardPanelModifier: ViewModifier {
    let theme: Bool

    func body(content: Content) -> some View {
        content
            .background(DashboardTheme.raised(theme), in: RoundedRectangle(cornerRadius: 5))
            .overlay(
                RoundedRectangle(cornerRadius: 5)
                    .stroke(DashboardTheme.border(theme), lineWidth: 1)
            )
    }
}

private extension View {
    func dashboardPanel(theme: Bool) -> some View {
        modifier(DashboardPanelModifier(theme: theme))
    }

    func onHoverHint(_ value: String, _ hint: Binding<String>) -> some View {
        onHover { hovering in
            hint.wrappedValue = hovering ? value : "hover over a button for details"
        }
    }
}

private enum DashboardTheme {
    static let record = Color(red: 0.77, green: 0.35, blue: 0.33)
    static let generated = Color(red: 0.30, green: 0.66, blue: 0.51)
    static let summon = Color(red: 0.69, green: 0.48, blue: 0.68)
    static let solo = Color(red: 0.77, green: 0.65, blue: 0.27)
    static let error = Color(red: 0.77, green: 0.44, blue: 0.38)

    static func background(_ light: Bool) -> Color {
        light ? Color(red: 0.92, green: 0.93, blue: 0.91) : Color(red: 0.031, green: 0.035, blue: 0.039)
    }

    static func raised(_ light: Bool) -> Color {
        light ? Color(red: 0.97, green: 0.97, blue: 0.95) : Color(red: 0.055, green: 0.063, blue: 0.067)
    }

    static func hover(_ light: Bool) -> Color {
        light ? Color(red: 0.90, green: 0.92, blue: 0.90) : Color(red: 0.082, green: 0.090, blue: 0.094)
    }

    static func inset(_ light: Bool) -> Color {
        light ? Color(red: 0.88, green: 0.90, blue: 0.88) : Color(red: 0.024, green: 0.027, blue: 0.031)
    }

    static func settings(_ light: Bool) -> Color {
        light ? Color(red: 0.94, green: 0.95, blue: 0.93) : Color(red: 0.063, green: 0.071, blue: 0.075)
    }

    static func selected(_ light: Bool) -> Color {
        light ? Color.black.opacity(0.04) : Color.white.opacity(0.03)
    }

    static func text(_ light: Bool) -> Color {
        light ? Color(red: 0.08, green: 0.09, blue: 0.09) : Color(red: 0.94, green: 0.95, blue: 0.95)
    }

    static func secondary(_ light: Bool) -> Color {
        light ? Color(red: 0.26, green: 0.30, blue: 0.30) : Color(red: 0.72, green: 0.75, blue: 0.75)
    }

    static func dim(_ light: Bool) -> Color {
        light ? Color(red: 0.42, green: 0.46, blue: 0.46) : Color(red: 0.54, green: 0.58, blue: 0.59)
    }

    static func ghost(_ light: Bool) -> Color {
        light ? Color.black.opacity(0.15) : Color.white.opacity(0.18)
    }

    static func border(_ light: Bool) -> Color {
        light ? Color.black.opacity(0.18) : Color(red: 0.15, green: 0.17, blue: 0.18)
    }

    static func borderActive(_ light: Bool) -> Color {
        light ? Color.black.opacity(0.28) : Color(red: 0.24, green: 0.28, blue: 0.29)
    }

    static func accent(_ light: Bool) -> Color {
        light ? Color(red: 0.20, green: 0.45, blue: 0.55) : Color(red: 0.35, green: 0.60, blue: 0.71)
    }

    static func meterBackground(_ light: Bool) -> Color {
        light ? Color.black.opacity(0.18) : Color(red: 0.07, green: 0.08, blue: 0.08)
    }

    static func logBackground(_ light: Bool) -> Color {
        light ? Color(red: 0.88, green: 0.89, blue: 0.87) : Color(red: 0.024, green: 0.027, blue: 0.031)
    }

    static func logBorder(_ light: Bool) -> Color {
        light ? Color.black.opacity(0.12) : Color(red: 0.10, green: 0.12, blue: 0.13)
    }

    static func logTime(_ light: Bool) -> Color {
        light ? Color.black.opacity(0.28) : Color(red: 0.24, green: 0.26, blue: 0.27)
    }
}
