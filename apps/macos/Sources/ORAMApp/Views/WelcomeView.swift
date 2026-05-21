import SwiftUI

struct WelcomeView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            VStack(alignment: .leading, spacing: 8) {
                Text("ORAM")
                    .font(.system(size: 46, weight: .semibold, design: .rounded))
                Text("recorder / looper / summoner / listener / archive")
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }

            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 14) {
                GridRow {
                    WelcomeStatus(label: "Engine", value: store.connectionStatus)
                    WelcomeStatus(label: "Backend", value: store.state?.gateway ?? "mock")
                    WelcomeStatus(label: "Library", value: store.state?.libraryDir ?? "ORAM Library")
                }
                GridRow {
                    WelcomeStatus(label: "Sample Rate", value: "\(store.state?.sampleRate ?? 48000) Hz")
                    WelcomeStatus(label: "Layers", value: "\(store.state?.layers.count ?? 4)")
                    WelcomeStatus(label: "Sounds", value: "\(store.sounds.count)")
                }
            }

            if let error = store.errorMessage {
                Label(error, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange)
                    .textSelection(.enabled)
            }

            Spacer()
        }
        .padding(32)
    }
}

struct WelcomeStatus: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.headline)
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }
}
