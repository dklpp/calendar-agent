import Foundation

/// User-visible settings, persisted in UserDefaults.
///
/// The backend host is LAN-only by design, so it defaults to the Pi's
/// mDNS name rather than anything routable from outside the house.
final class AppSettings: ObservableObject {
    static let shared = AppSettings()

    @Published var backendHost: String {
        didSet { UserDefaults.standard.set(backendHost, forKey: "backendHost") }
    }

    private init() {
        backendHost = UserDefaults.standard.string(forKey: "backendHost")
            ?? "http://raspberrypi.local:8000"
    }

    var baseURL: URL? { URL(string: backendHost) }
}
