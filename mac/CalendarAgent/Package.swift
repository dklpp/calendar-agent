// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "CalendarAgent",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "CalendarAgent",
            path: "Sources/CalendarAgent"
        )
    ]
)
