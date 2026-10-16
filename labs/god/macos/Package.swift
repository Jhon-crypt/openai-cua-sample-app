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
            path: "Sources/God",
            exclude: ["Info.plist"],
            linkerSettings: [
                .unsafeFlags([
                    "-Xlinker", "-sectcreate",
                    "-Xlinker", "__TEXT",
                    "-Xlinker", "__info_plist",
                    "-Xlinker", "Sources/God/Info.plist",
                ], .when(configuration: .debug)),
                .unsafeFlags([
                    "-Xlinker", "-sectcreate",
                    "-Xlinker", "__TEXT",
                    "-Xlinker", "__info_plist",
                    "-Xlinker", "Sources/God/Info.plist",
                ], .when(configuration: .release)),
            ]
        ),
    ]
)
