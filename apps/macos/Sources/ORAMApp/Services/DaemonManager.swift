import Darwin
import Foundation

final class DaemonManager: @unchecked Sendable {
    private var process: Process?
    private let metadataURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/ORAM/oram-daemon.json")

    deinit {
        stop()
    }

    func stop() {
        process?.terminate()
        process = nil
    }

    func launchIfNeeded(client: DaemonClient) async -> String {
        guard let root = findPythonProject() else {
            return "daemon not running"
        }

        if let metadata = try? client.loadMetadata(), (try? await client.health()) != nil {
            if metadata.projectPath == root.path {
                return "connected"
            }
            terminateDaemon(pid: metadata.pid)
        }

        try? FileManager.default.removeItem(at: metadataURL)

        let process = Process()
        let uv = bundledUV()
        process.executableURL = uv ?? URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = uv == nil
            ? ["uv"] + daemonArguments(root: root)
            : daemonArguments(root: root)
        process.currentDirectoryURL = root
        process.environment = daemonEnvironment()
        let logHandle = daemonLogHandle()
        process.standardOutput = logHandle
        process.standardError = logHandle

        do {
            try process.run()
            self.process = process
        } catch {
            return "daemon launch failed"
        }

        for _ in 0..<30 {
            try? await Task.sleep(nanoseconds: 300_000_000)
            if (try? client.loadMetadata()) != nil, (try? await client.health()) != nil {
                return "connected"
            }
        }

        if process.isRunning {
            return "daemon starting"
        }
        return "daemon exited — see ORAM/daemon.log"
    }

    private func findPythonProject() -> URL? {
        if let resourceURL = Bundle.main.resourceURL {
            let bundled = resourceURL.appendingPathComponent("oram-python")
            if FileManager.default.fileExists(atPath: bundled.appendingPathComponent("pyproject.toml").path) {
                return bundled
            }
        }

        var url = Bundle.main.bundleURL
        for _ in 0..<8 {
            let candidate = url.appendingPathComponent("pyproject.toml")
            if FileManager.default.fileExists(atPath: candidate.path) {
                return url
            }
            url.deleteLastPathComponent()
        }
        return nil
    }

    private func bundledUV() -> URL? {
        guard let resourceURL = Bundle.main.resourceURL else {
            return nil
        }
        let url = resourceURL.appendingPathComponent("bin/uv")
        return FileManager.default.isExecutableFile(atPath: url.path) ? url : nil
    }

    private func daemonArguments(root: URL) -> [String] {
        [
            "run",
            "--extra",
            "web",
            "--project",
            root.path,
            "oram",
            "daemon",
            "--host",
            "127.0.0.1",
            "--port",
            "auto"
        ]
    }

    private func daemonEnvironment() -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("ORAM", isDirectory: true)
        let cache = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("ORAM", isDirectory: true)
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
        try? FileManager.default.createDirectory(at: cache, withIntermediateDirectories: true)
        environment["UV_PROJECT_ENVIRONMENT"] = support.appendingPathComponent("venv").path
        environment["UV_CACHE_DIR"] = cache.appendingPathComponent("uv").path
        environment["UV_PYTHON_INSTALL_DIR"] = support.appendingPathComponent("python").path
        environment["ORAM_DISABLE_DOTENV"] = "1"
        return environment
    }

    private func daemonLogHandle() -> FileHandle {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("ORAM", isDirectory: true)
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
        let logURL = support.appendingPathComponent("daemon.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        if let handle = try? FileHandle(forWritingTo: logURL) {
            _ = try? handle.seekToEnd()
            if let marker = "\n--- ORAM daemon launch \(Date()) ---\n".data(using: .utf8) {
                try? handle.write(contentsOf: marker)
            }
            return handle
        }
        return FileHandle.nullDevice
    }

    private func terminateDaemon(pid: Int) {
        guard pid > 0 else { return }
        _ = kill(pid_t(pid), SIGTERM)
    }
}
