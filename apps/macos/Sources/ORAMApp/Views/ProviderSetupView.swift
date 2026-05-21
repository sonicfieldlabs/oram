import SwiftUI

struct ProviderSetupView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        Form {
            ProviderCredentialPanel(
                provider: "elevenlabs",
                title: "ElevenLabs",
                symbol: "waveform.badge.mic",
                detail: "Sound effects, voice, music, Scribe, voice design, and isolation."
            )

            ProviderCredentialPanel(
                provider: "stability",
                title: "Stability AI · Stable Audio",
                symbol: "sparkles",
                detail: "Stable Audio text-to-music and sound material generation."
            )

            Section("Provider Engines") {
                ForEach(store.providers) { engine in
                    HStack {
                        Image(systemName: engine.available ? "checkmark.circle" : "circle")
                            .foregroundStyle(engine.available ? .green : .secondary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(engine.label)
                            Text(engine.capabilities.joined(separator: ", "))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                        Spacer()
                        Text(engine.provider)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}

private struct ProviderCredentialPanel: View {
    @EnvironmentObject private var store: AppStore
    let provider: String
    let title: String
    let symbol: String
    let detail: String

    @State private var apiKey = ""
    @State private var showKey = false
    @State private var statusText = ""

    private var status: CredentialStatus? {
        store.credentials[provider]
    }

    var body: some View {
        Section(title) {
            HStack {
                Label(
                    status?.configured == true ? "Configured" : "Missing",
                    systemImage: status?.configured == true ? "checkmark.seal" : "key"
                )
                Spacer()
                Text(status?.source ?? "none")
                    .foregroundStyle(.secondary)
            }

            Label(detail, systemImage: symbol)
                .font(.caption)
                .foregroundStyle(.secondary)

            if showKey {
                TextField("API key", text: $apiKey)
                    .textFieldStyle(.roundedBorder)
            } else {
                SecureField("API key", text: $apiKey)
                    .textFieldStyle(.roundedBorder)
            }

            Toggle("Show key while editing", isOn: $showKey)

            HStack {
                Button {
                    saveKey()
                } label: {
                    Label("Save to Keychain", systemImage: "lock")
                }
                .disabled(apiKey.isEmpty)

                Button {
                    testKey()
                } label: {
                    Label("Test", systemImage: "checkmark.circle")
                }
                .disabled(status?.configured != true)

                Button(role: .destructive) {
                    deleteKey()
                } label: {
                    Label("Delete", systemImage: "trash")
                }

                Spacer()
            }

            if !statusText.isEmpty {
                Text(statusText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func saveKey() {
        do {
            try KeychainStore.shared.setSecret(apiKey, provider: provider)
            apiKey = ""
            statusText = "Stored in macOS Keychain."
            Task { await store.refreshAll() }
        } catch {
            statusText = error.localizedDescription
        }
    }

    private func deleteKey() {
        do {
            try KeychainStore.shared.deleteSecret(provider: provider)
            apiKey = ""
            statusText = "Deleted from macOS Keychain."
            Task { await store.refreshAll() }
        } catch {
            statusText = error.localizedDescription
        }
    }

    private func testKey() {
        Task {
            let result = await store.testCredential(provider: provider)
            statusText = "Credential test: \(result)"
        }
    }
}
