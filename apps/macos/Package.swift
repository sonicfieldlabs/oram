// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "ORAMMac",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "ORAM", targets: ["ORAMApp"])
    ],
    targets: [
        .executableTarget(
            name: "ORAMApp",
            path: "Sources/ORAMApp",
            resources: [
                .process("Resources")
            ]
        )
    ]
)
