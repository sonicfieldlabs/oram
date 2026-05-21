import Foundation

final class DaemonManager {
    private var process: Process?

    func launchIfNeeded(client: DaemonClient) async -> String {
        if (try? client.loadMetadata()) != nil, (try? await client.health()) != nil {
            return "connected"
        }

        guard let root = findPythonProject() else {
            return "daemon not running"
        }

        let process = Process()
        let uv = bundledUV()
        process.executableURL = uv ?? URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = uv == nil
            ? ["uv"] + daemonArguments(root: root)
            : daemonArguments(root: root)
        process.currentDirectoryURL = root
        process.environment = daemonEnvironment()
        process.standardOutput = Pipe()
        process.standardError = Pipe()

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

        return "daemon starting"
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
            "--project",
            root.path,
            "oram",
            "daemon",
            "--host",
            "127.0.0.1",
            "--port",
            "auto",
            "--mock-audio"
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
}
