import SwiftUI

struct LibraryView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        HSplitView {
            List(selection: $store.selectedSoundID) {
                ForEach(store.sounds) { sound in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Text(sound.prompt)
                                .lineLimit(1)
                            if sound.favorite {
                                Image(systemName: "star.fill")
                                    .foregroundStyle(.yellow)
                            }
                        }
                        Text("\(sound.provider) / \(sound.model) / \(String(format: "%.1fs", sound.durationSeconds))")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .tag(sound.id)
                    .onDrag {
                        let url = URL(fileURLWithPath: sound.path)
                        return NSItemProvider(contentsOf: url) ?? NSItemProvider(object: sound.path as NSString)
                    }
                }
            }
            .frame(minWidth: 320)

            VStack(alignment: .leading, spacing: 16) {
                if let sound = store.selectedSound {
                    SoundDetail(sound: sound)
                    HStack {
                        Button {
                            Task { await store.revealSelectedSound() }
                        } label: {
                            Label("Reveal", systemImage: "finder")
                        }
                        Button {
                            Task {
                                _ = try? await store.client.favorite(soundID: sound.id, favorite: !sound.favorite)
                                await store.refreshAll()
                            }
                        } label: {
                            Label(sound.favorite ? "Unfavorite" : "Favorite", systemImage: sound.favorite ? "star.slash" : "star")
                        }
                    }
                    Text(sound.tags.joined(separator: ", "))
                        .foregroundStyle(.secondary)
                    Spacer()
                } else {
                    VStack(spacing: 10) {
                        Image(systemName: "waveform")
                            .font(.system(size: 40))
                            .foregroundStyle(.secondary)
                        Text("No Sound Selected")
                            .font(.headline)
                        Text("Generated material will appear here.")
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .padding(24)
            .frame(minWidth: 420)
        }
    }
}
