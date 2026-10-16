// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "God",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "God", targets: ["God"]),
    ],
    targets: [
        .executableTarget(
            name: "God",
            path: "Sources/God"
        ),
    ]
)
