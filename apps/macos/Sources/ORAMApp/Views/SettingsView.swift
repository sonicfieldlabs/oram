import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var store: AppStore
    @State private var sampleRate = 48000
    @State private var seeded = false

    var body: some View {
        Form {
            Picker("Sample rate", selection: $sampleRate) {
                Text("44.1 kHz").tag(44100)
                Text("48 kHz").tag(48000)
                Text("96 kHz").tag(96000)
            }
            .onChange(of: sampleRate) { newValue in
                guard seeded else { return }
                Task { await store.updateAudioSettings(sampleRate: newValue, blockSize: nil) }
            }

            LabeledContent("Session directory", value: store.state?.sessionDir ?? "")
            LabeledContent("Library directory", value: store.state?.libraryDir ?? "")
            LabeledContent("Provider backend", value: store.state?.gateway ?? "mock")

            Text("Audio input/output devices are chosen in the main window's settings panel.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .task {
            // seed from the live daemon state so the picker reflects reality,
            // and don't fire an update for the seed itself
            if let sr = store.state?.sampleRate { sampleRate = sr }
            seeded = true
        }
    }
}
