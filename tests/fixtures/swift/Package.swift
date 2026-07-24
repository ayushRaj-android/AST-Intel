// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "MySwiftApp",
    platforms: [
        .macOS(.v13),
        .iOS(.v16)
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-argument-parser.git", from: "1.2.0"),
        .package(url: "https://github.com/vapor/vapor.git", from: "4.89.0"),
        .package(url: "https://github.com/apple/swift-log.git", .upToNextMajor(from: "1.5.0")),
    ],
    targets: [
        .target(
            name: "MySwiftApp",
            dependencies: [
                .product(name: "ArgumentParser", package: "swift-argument-parser"),
                .product(name: "Vapor", package: "vapor"),
            ]
        ),
        .testTarget(
            name: "MySwiftAppTests",
            dependencies: ["MySwiftApp"]
        ),
    ]
)
