import AppKit
import SwiftUI

@MainActor
final class ORAMAppDelegate: NSObject, NSApplicationDelegate {
    weak var store: AppStore?
    private var isTerminating = false

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !isTerminating else {
            return .terminateNow
        }
        isTerminating = true
        Task {
            await store?.shutdown()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}

@main
struct ORAMApplication: App {
    @NSApplicationDelegateAdaptor(ORAMAppDelegate.self) private var appDelegate
    @StateObject private var store = AppStore()

    var body: some Scene {
        WindowGroup("ORAM") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 1040, minHeight: 680)
                .task {
                    appDelegate.store = store
                    await store.bootstrap()
                }
                .onDisappear {
                    Task {
                        await store.shutdown()
                    }
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
