import SwiftUI

struct MenuBarView: View {
    @ObservedObject var model: AgentViewModel
    @ObservedObject private var settings = AppSettings.shared
    @State private var showingSettings = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header

            if let reply = model.pendingReply {
                confirmationCard(reply)
            } else {
                agendaList
            }

            if !model.status.isEmpty {
                Text(model.status)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Divider()
            composer
            Divider()
            footer
        }
        .padding(12)
        .frame(width: 340)
        .task { await model.refreshAgenda() }
    }

    private var header: some View {
        HStack {
            Text("Today").font(.headline)
            Spacer()
            if model.isBusy { ProgressView().controlSize(.small) }
            Button {
                Task { await model.refreshAgenda() }
            } label: { Image(systemName: "arrow.clockwise") }
                .buttonStyle(.borderless)
                .help("Refresh")
        }
    }

    @ViewBuilder
    private var agendaList: some View {
        if model.agenda.isEmpty {
            Text("Nothing scheduled.")
                .foregroundStyle(.secondary)
                .font(.callout)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(model.agenda) { event in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(event.summary).font(.body)
                        // Both zones, always -- the whole point of the app.
                        Text(event.both)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func confirmationCard(_ reply: Reply) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(reply.text)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(reply.choices) { choice in
                Button(choice.label) { Task { await model.confirm(choice) } }
                    .buttonStyle(.borderedProminent)
            }
            Button("Cancel") { model.dismiss() }
                .buttonStyle(.borderless)
        }
        .padding(8)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
    }

    private var composer: some View {
        HStack(spacing: 6) {
            TextField("Type a request...", text: $model.typed)
                .textFieldStyle(.roundedBorder)
                .onSubmit { Task { await model.submitTyped() } }
            Button {
                Task { await model.toggleRecording() }
            } label: {
                Image(systemName: model.recorder.isRecording
                      ? "stop.circle.fill" : "mic.fill")
            }
            .buttonStyle(.borderless)
            .help(model.recorder.isRecording
                  ? "Stop and send" : "Record (Control-Option-Space)")
        }
    }

    private var footer: some View {
        HStack {
            Button("Settings") { showingSettings.toggle() }
                .buttonStyle(.borderless)
            Spacer()
            Button("Quit") { NSApplication.shared.terminate(nil) }
                .buttonStyle(.borderless)
        }
        .font(.caption)
        .popover(isPresented: $showingSettings) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Backend address").font(.headline)
                TextField("http://raspberrypi.local:8000",
                          text: $settings.backendHost)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 260)
                Text("The Raspberry Pi running the agent. LAN only.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(12)
        }
    }
}
