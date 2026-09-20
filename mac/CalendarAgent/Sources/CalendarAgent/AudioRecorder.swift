import AVFoundation
import Foundation

/// Records 16 kHz mono PCM -- exactly what whisper.cpp wants, so the Pi does
/// no resampling and the upload stays small.
@MainActor
final class AudioRecorder: NSObject, ObservableObject {
    @Published private(set) var isRecording = false

    private var recorder: AVAudioRecorder?
    private var destination: URL?

    private static let settings: [String: Any] = [
        AVFormatIDKey: Int(kAudioFormatLinearPCM),
        AVSampleRateKey: 16000.0,
        AVNumberOfChannelsKey: 1,
        AVLinearPCMBitDepthKey: 16,
        AVLinearPCMIsFloatKey: false,
        AVLinearPCMIsBigEndianKey: false,
    ]

    /// Prompts for microphone access on first use.
    func requestPermission() async -> Bool {
        await withCheckedContinuation { continuation in
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                continuation.resume(returning: granted)
            }
        }
    }

    func start() throws {
        guard !isRecording else { return }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("capture-\(UUID().uuidString).wav")
        let recorder = try AVAudioRecorder(url: url, settings: Self.settings)
        recorder.record()
        self.recorder = recorder
        self.destination = url
        isRecording = true
    }

    /// Stops and returns the file, or nil if the clip was too short to be
    /// speech (a stray hotkey tap rather than an actual request).
    func stop() -> URL? {
        guard let recorder, isRecording else { return nil }
        let duration = recorder.currentTime
        recorder.stop()
        isRecording = false
        self.recorder = nil
        guard duration > 0.4, let url = destination else {
            destination.map { try? FileManager.default.removeItem(at: $0) }
            return nil
        }
        return url
    }
}
