import SwiftUI

struct RecorderView: View {
    @EnvironmentObject private var store: AppStore
    @State private var commandText = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Button {
                    Task { await store.startRecording() }
                } label: {
                    Label("Record", systemImage: "record.circle")
                }
                .keyboardShortcut("r", modifiers: [])

                Button {
                    Task { await store.stopRecording() }
                } label: {
                    Label("Stop", systemImage: "stop.circle")
                }

                Spacer()

                Text(store.state?.recording == true ? "recording" : "idle")
                    .foregroundStyle(store.state?.recording == true ? .red : .secondary)
            }

            HStack {
                TextField("command", text: $commandText)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit {
                        send()
                    }
                Button {
                    send()
                } label: {
                    Label("Send", systemImage: "return")
                }
            }

            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 12), count: 2), spacing: 12) {
                ForEach(store.state?.layers ?? []) { layer in
                    LayerPanel(layer: layer)
                }
            }

            Spacer()
        }
        .padding(24)
    }

    private func send() {
        let text = commandText
        commandText = ""
        Task { await store.sendCommand(text) }
    }
}
