import SwiftUI

@main
struct ORAMApplication: App {
    @StateObject private var store = AppStore()

    var body: some Scene {
        WindowGroup("ORAM") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 1040, minHeight: 680)
                .task {
                    await store.bootstrap()
                }
        }
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Refresh Engine") {
                    Task { await store.refreshAll() }
                }
                .keyboardShortcut("r", modifiers: [.command])
            }
        }

        Settings {
            SettingsView()
                .environmentObject(store)
                .frame(width: 520)
                .padding()
        }
    }
}
