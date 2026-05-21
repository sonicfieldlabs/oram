import SwiftUI

struct PrivacyView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                PrivacyRow(symbol: "network.slash", title: "No ORAM cloud", value: "The app talks to a localhost daemon.")
                PrivacyRow(symbol: "key", title: "BYOK", value: "Provider keys are stored in macOS Keychain.")
                PrivacyRow(symbol: "waveform", title: "Local library", value: store.state?.libraryDir ?? "~/Music/ORAM Library")
                PrivacyRow(symbol: "antenna.radiowaves.left.and.right.slash", title: "Telemetry", value: "Off by default.")
                PrivacyRow(symbol: "lock.shield", title: "Daemon auth", value: store.client.isConfigured ? "Local bearer token configured when daemon metadata includes one." : "Daemon unavailable.")
            }
            .padding(24)
        }
    }
}

struct PrivacyRow: View {
    let symbol: String
    let title: String
    let value: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol)
                .frame(width: 24)
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.headline)
                Text(value)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            Spacer()
        }
        .padding(12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }
}
