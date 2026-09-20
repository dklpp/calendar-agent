import Foundation
import SwiftUI

/// Drives the menu bar UI: recording, asking, confirming, and the agenda.
@MainActor
final class AgentViewModel: ObservableObject {
    @Published var agenda: [AgendaEvent] = []
    @Published var status: String = ""
    @Published var pendingReply: Reply?
    @Published var isBusy = false
    @Published var typed: String = ""

    let recorder = AudioRecorder()
    private var hotKey: HotKey?

    func installHotKey() {
        hotKey = HotKey { [weak self] in
            Task { await self?.toggleRecording() }
        }
    }

    // MARK: - Voice

    func toggleRecording() async {
        if recorder.isRecording {
            await finishRecording()
        } else {
            await beginRecording()
        }
    }

    private func beginRecording() async {
        guard await recorder.requestPermission() else {
            status = "Microphone access denied. Enable it in System Settings."
            return
        }
        do {
            try recorder.start()
            status = "Listening... press the hotkey again to send."
        } catch {
            status = "Could not start recording: \(error.localizedDescription)"
        }
    }

    private func finishRecording() async {
        guard let url = recorder.stop() else {
            status = "That was too short -- nothing sent."
            return
        }
        defer { try? FileManager.default.removeItem(at: url) }
        isBusy = true
        status = "Transcribing..."
        do {
            let reply = try await AgentClient.shared.sendVoice(fileURL: url)
            present(reply)
        } catch {
            status = error.localizedDescription
        }
        isBusy = false
    }

    // MARK: - Text

    func submitTyped() async {
        let text = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        typed = ""
        isBusy = true
        status = "Thinking..."
        do {
            present(try await AgentClient.shared.ask(text))
        } catch {
            status = error.localizedDescription
        }
        isBusy = false
    }

    // MARK: - Confirmation

    /// The only path that causes a calendar write, mirroring the Telegram
    /// button flow so both front ends behave identically.
    func confirm(_ choice: Choice) async {
        isBusy = true
        do {
            let reply = try await AgentClient.shared.confirm(token: choice.token)
            pendingReply = nil
            status = reply.text
            await refreshAgenda()
        } catch {
            status = error.localizedDescription
        }
        isBusy = false
    }

    func dismiss() {
        pendingReply = nil
        status = ""
    }

    private func present(_ reply: Reply) {
        if reply.choices.isEmpty {
            pendingReply = nil
            status = reply.text
        } else {
            pendingReply = reply
            status = ""
        }
    }

    // MARK: - Agenda

    func refreshAgenda() async {
        do {
            agenda = try await AgentClient.shared.agenda(days: 1)
        } catch {
            status = error.localizedDescription
        }
    }
}
