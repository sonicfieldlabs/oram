import SwiftUI

struct EngineStatusView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(spacing: 16) {
                    Metric(label: "Audio", value: store.state?.audioRunning == true ? "running" : "stopped")
                    Metric(label: "Mode", value: store.state?.mode ?? "record")
                    Metric(label: "Selected", value: "Layer \(store.state?.selectedLayer ?? 1)")
                    Metric(label: "Engines", value: "\(store.providers.filter(\.available).count)")
                }

                Text("Layers")
                    .font(.title2.bold())

                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 12), count: 2), spacing: 12) {
                    ForEach(store.state?.layers ?? []) { layer in
                        LayerPanel(layer: layer)
                    }
                }

                Text("Recent Engine Log")
                    .font(.title2.bold())

                VStack(alignment: .leading, spacing: 6) {
                    ForEach((store.state?.log ?? []).indices, id: \.self) { index in
                        Text(store.state?.log[index] ?? "")
                            .font(.system(.body, design: .monospaced))
                            .foregroundStyle(.secondary)
                    }
                }
                .textSelection(.enabled)
            }
            .padding(24)
        }
    }
}

struct Metric: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.headline)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }
}

struct LayerPanel: View {
    let layer: LayerState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("L\(layer.slot)")
                    .font(.headline)
                Text(layer.sourceType)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if layer.muted {
                    Image(systemName: "speaker.slash")
                        .foregroundStyle(.secondary)
                }
                if layer.solo {
                    Image(systemName: "headphones")
                        .foregroundStyle(.secondary)
                }
            }
            WaveformView(samples: layer.waveform)
                .frame(height: 62)
            Text(layer.generationPrompt ?? layer.state)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(2)
            HStack {
                Text(String(format: "%.2fs", layer.duration))
                Spacer()
                Text(String(format: "vol %.2f pan %.2f", layer.volume, layer.pan))
            }
            .font(.caption2)
            .foregroundStyle(.secondary)
        }
        .padding(12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }
}
