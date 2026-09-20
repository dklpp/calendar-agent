import Foundation

struct Choice: Codable, Identifiable, Hashable {
    let label: String
    let token: String
    var id: String { token }
}

struct Reply: Codable {
    let text: String
    let kind: String
    let choices: [Choice]
}

struct AgendaEvent: Codable, Identifiable, Hashable {
    let id: String
    let summary: String
    let start: Date
    let end: Date
    /// Pre-rendered by the backend so both front ends show identical strings.
    let primary: String
    let secondary: String
    let both: String
}

enum AgentError: LocalizedError {
    case notConfigured
    case server(String)

    var errorDescription: String? {
        switch self {
        case .notConfigured:
            return "Set the backend address in Settings."
        case .server(let message):
            return message
        }
    }
}

/// Thin HTTP client over the same FastAPI endpoints the Telegram bot uses.
actor AgentClient {
    static let shared = AgentClient()

    private var decoder: JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }

    /// Read straight from UserDefaults rather than through `AppSettings`,
    /// which is main-actor bound -- this keeps the client free of actor hops.
    private func baseURL() throws -> URL {
        let host = UserDefaults.standard.string(forKey: "backendHost")
            ?? "http://raspberrypi.local:8000"
        guard let url = URL(string: host), url.host != nil else {
            throw AgentError.notConfigured
        }
        return url
    }

    func ask(_ text: String) async throws -> Reply {
        var request = URLRequest(url: try baseURL().appendingPathComponent("ask"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(["text": text])
        return try await send(request)
    }

    func confirm(token: String) async throws -> Reply {
        var request = URLRequest(
            url: try baseURL().appendingPathComponent("confirm/\(token)"))
        request.httpMethod = "POST"
        return try await send(request)
    }

    /// Uploads a recording as multipart form data to POST /voice.
    func sendVoice(fileURL: URL) async throws -> Reply {
        let boundary = "Boundary-\(UUID().uuidString)"
        var request = URLRequest(url: try baseURL().appendingPathComponent("voice"))
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)",
                         forHTTPHeaderField: "Content-Type")
        // Transcription on a Pi can take ~20s; the default 60s timeout is
        // tight once a slow model is in play.
        request.timeoutInterval = 120

        var body = Data()
        let filename = fileURL.lastPathComponent
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append(
            "Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n"
                .data(using: .utf8)!)
        body.append("Content-Type: audio/wav\r\n\r\n".data(using: .utf8)!)
        body.append(try Data(contentsOf: fileURL))
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        request.httpBody = body

        return try await send(request)
    }

    func agenda(days: Int = 1) async throws -> [AgendaEvent] {
        var components = URLComponents(
            url: try baseURL().appendingPathComponent("agenda"),
            resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "days", value: String(days))]
        let (data, response) = try await URLSession.shared.data(from: components.url!)
        try check(response, data)
        return try decoder.decode([AgendaEvent].self, from: data)
    }

    private func send(_ request: URLRequest) async throws -> Reply {
        let (data, response) = try await URLSession.shared.data(for: request)
        try check(response, data)
        return try decoder.decode(Reply.self, from: data)
    }

    private func check(_ response: URLResponse, _ data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            let detail = String(data: data, encoding: .utf8) ?? "unknown error"
            throw AgentError.server("Backend returned \(http.statusCode): \(detail)")
        }
    }
}
