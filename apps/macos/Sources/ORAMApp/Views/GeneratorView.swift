import SwiftUI

struct GeneratorView: View {
    @EnvironmentObject private var store: AppStore
    @State private var prompt = "distant metallic rain inside a concrete tunnel"
    @State private var duration = 8.0
    @State private var provider = "auto"
    @State private var model = "local-mock"
    @State private var tags = "texture, metallic"

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            TextEditor(text: $prompt)
                .font(.title3)
                .frame(minHeight: 110)
                .scrollContentBackground(.hidden)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))

            HStack {
                Picker("Provider", selection: $provider) {
                    Text("Auto").tag("auto")
                    Text("Local").tag("local")
                    Text("ElevenLabs").tag("elevenlabs")
                    Text("Stability").tag("stability")
                }
                .pickerStyle(.segmented)

                Picker("Model", selection: $model) {
                    Text("Local Mock").tag("local-mock")
                    Text("ElevenLabs SFX").tag("elevenlabs-sfx")
                    Text("ElevenLabs Music").tag("elevenlabs-music")
                    Text("Stable Audio").tag("stability-stable-audio-2")
                }
                .frame(width: 220)
            }

            HStack {
                Slider(value: $duration, in: 0.5...30, step: 0.5)
                Text(String(format: "%.1fs", duration))
                    .frame(width: 54, alignment: .trailing)
            }

            TextField("tags", text: $tags)
                .textFieldStyle(.roundedBorder)

            HStack {
                Button {
                    Task {
                        await store.generate(
                            prompt: prompt,
                            duration: duration,
                            provider: provider,
                            model: model,
                            tags: tags.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
                        )
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
