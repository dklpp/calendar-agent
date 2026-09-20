import SwiftUI

@main
struct CalendarAgentApp: App {
    @StateObject private var model = AgentViewModel()

    var body: some Scene {
        MenuBarExtra("Calendar Agent", systemImage: "calendar.badge.clock") {
            MenuBarView(model: model)
                .onAppear { model.installHotKey() }
        }
        .menuBarExtraStyle(.window)
    }
}
