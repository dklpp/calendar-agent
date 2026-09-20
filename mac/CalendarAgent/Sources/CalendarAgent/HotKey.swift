import Carbon.HIToolbox
import Foundation

/// A process-wide hotkey via Carbon's `RegisterEventHotKey`.
///
/// Chosen over an `NSEvent` global monitor deliberately: this API does not
/// require Accessibility permission, so the app works the moment it launches
/// instead of after a trip through System Settings.
final class HotKey {
    private var hotKeyRef: EventHotKeyRef?
    private var handlerRef: EventHandlerRef?
    private let handler: () -> Void

    /// Default binding: Control-Option-Space.
    init(keyCode: UInt32 = UInt32(kVK_Space),
         modifiers: UInt32 = UInt32(controlKey | optionKey),
         handler: @escaping () -> Void) {
        self.handler = handler
        register(keyCode: keyCode, modifiers: modifiers)
    }

    private func register(keyCode: UInt32, modifiers: UInt32) {
        var eventType = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: UInt32(kEventHotKeyPressed))

        let callback: EventHandlerUPP = { _, _, userData in
            guard let userData else { return noErr }
            let hotKey = Unmanaged<HotKey>.fromOpaque(userData).takeUnretainedValue()
            DispatchQueue.main.async { hotKey.handler() }
            return noErr
        }

        InstallEventHandler(
            GetApplicationEventTarget(), callback, 1, &eventType,
            Unmanaged.passUnretained(self).toOpaque(), &handlerRef)

        let id = EventHotKeyID(signature: OSType(0x43414745), id: 1)  // 'CAGE'
        RegisterEventHotKey(keyCode, modifiers, id, GetApplicationEventTarget(),
                            0, &hotKeyRef)
    }

    deinit {
        if let hotKeyRef { UnregisterEventHotKey(hotKeyRef) }
        if let handlerRef { RemoveEventHandler(handlerRef) }
    }
}
