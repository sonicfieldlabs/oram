import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var store: AppStore
    @AppStorage("sampleRate") private var sampleRate = 48000
    @AppStorage("blockSize") private var blockSize = 512
    @AppStorage("privacyMode") private var privacyMode = true

    var body: some View {
        Form {
            Picker("Sample rate", selection: $sampleRate) {
                Text("44.1 kHz").tag(44100)
                Text("48 kHz").tag(48000)
                Text("96 kHz").tag(96000)
            }
            Picker("Block size", selection: $blockSize) {
                Text("256").tag(256)
                Text("512").tag(512)
                Text("1024").tag(1024)
            }
            Toggle("Privacy mode", isOn: $privacyMode)
            LabeledContent("Session directory", value: store.state?.sessionDir ?? "")
            LabeledContent("Library directory", value: store.state?.libraryDir ?? "")
            LabeledContent("Provider backend", value: store.state?.gateway ?? "mock")
        }
    }
}
