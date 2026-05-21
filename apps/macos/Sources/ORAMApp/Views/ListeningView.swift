import SwiftUI

struct ListeningView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Button {
                    Task { await store.sendCommand("listen to the texture") }
                } label: {
                    Label("Listen", systemImage: "ear")
                }
                Button {
                    Task { await store.sendCommand("describe the current mix") }
                } label: {
                    Label("Analyze Mix", systemImage: "waveform.badge.magnifyingglass")
                }
                Spacer()
            }

            VStack(alignment: .leading, spacing: 8) {
                ForEach((store.state?.log ?? []).indices, id: \.self) { index in
                    Text(store.state?.log[index] ?? "")
                        .font(.system(.body, design: .monospaced))
                        .textSelection(.enabled)
                }
            }
            .padding(12)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))

            Spacer()
        }
        .padding(24)
    }
}
