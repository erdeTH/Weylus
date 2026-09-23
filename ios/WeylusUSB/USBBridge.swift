import Foundation
import Network

private let hello = Data("WEYLUS-USB/1\n".utf8)
private let startStream = Data("START\n".utf8)

// All mutable transport state lives on one serial queue. Each accepted USB
// connection is reserved for exactly one browser TCP stream, with no framing
// after START. This preserves HTTP keep-alive and WebSocket upgrades verbatim.
final class USBBridge {
    var onStatus: ((Bool, String) -> Void)?
    private let queue = DispatchQueue(label: "org.weylus.usb")
    private var usbListener: NWListener?
    private var webListener: NWListener?
    private var running = false
    private var webReady = false
    private var usbReady = false
    private var tunnels: [UUID: Tunnel] = [:]
    private var available: [UUID] = []
    private var browsers: [UUID: NWConnection] = [:]
    private var waiting: [UUID] = []

    func start() {
        queue.async { [self] in
            guard !running else { return }
            running = true
            do {
                usbListener = try makeListener(port: 49151, isUSB: true)
                webListener = try makeListener(port: 1701, isUSB: false)
                usbListener?.start(queue: queue)
                webListener?.start(queue: queue)
                report()
            } catch {
                shutdown("Could not start the local connection. Reopen the app.")
            }
        }
    }

    func stop() {
        queue.async { [self] in shutdown("Connection paused. Reopen the app to reconnect.") }
    }

    private func makeListener(port: UInt16, isUSB: Bool) throws -> NWListener {
        let parameters = NWParameters.tcp
        parameters.allowLocalEndpointReuse = true
        // usbmuxd reaches a device-side loopback TCP service. Neither listener
        // is exposed on the iPad's Wi-Fi interface.
        parameters.requiredLocalEndpoint = .hostPort(host: .ipv4(.loopback),
                                                     port: NWEndpoint.Port(rawValue: port)!)
        let listener = try NWListener(using: parameters)
        listener.stateUpdateHandler = { [weak self, weak listener] state in
            guard let self = self, let listener = listener, self.running,
                  listener === (isUSB ? self.usbListener : self.webListener) else { return }
            switch state {
            case .ready:
                if isUSB { self.usbReady = true } else { self.webReady = true }
                self.report()
            case .failed:
                self.shutdown("Local connection failed. Reopen the app to retry.")
            default:
                break
            }
        }
        listener.newConnectionHandler = { [weak self, weak listener] connection in
            guard let self = self, let listener = listener, self.running,
                  listener === (isUSB ? self.usbListener : self.webListener) else {
                connection.cancel()
                return
            }
            if isUSB { self.acceptTunnel(connection) } else { self.acceptBrowser(connection) }
        }
        return listener
    }

    private func acceptTunnel(_ connection: NWConnection) {
        guard tunnels.count < 32 else { connection.cancel(); return }
        let tunnel = Tunnel(connection: connection, queue: queue)
        tunnels[tunnel.id] = tunnel
        tunnel.onReady = { [weak self, weak tunnel] in
            guard let self = self, let tunnel = tunnel, self.tunnels[tunnel.id] != nil else { return }
            self.available.append(tunnel.id)
            self.pairConnections()
            self.report()
        }
        tunnel.onClose = { [weak self] id in
            guard let self = self else { return }
            self.tunnels.removeValue(forKey: id)
            self.available.removeAll { $0 == id }
            if self.running { self.report() }
        }
        tunnel.start()
    }

    private func acceptBrowser(_ connection: NWConnection) {
        guard browsers.count < 32 else { connection.cancel(); return }
        let id = UUID()
        browsers[id] = connection
        connection.stateUpdateHandler = { [weak self] state in
            guard let self = self, self.browsers[id] != nil else { return }
            switch state {
            case .ready:
                self.waiting.append(id)
                self.pairConnections()
            case .failed, .cancelled:
                self.removeBrowser(id)
            default:
                break
            }
        }
        connection.start(queue: queue)
        queue.asyncAfter(deadline: .now() + 10) { [weak self] in self?.removeBrowser(id) }
    }

    private func removeBrowser(_ id: UUID) {
        browsers.removeValue(forKey: id)?.cancel()
        waiting.removeAll { $0 == id }
    }

    private func pairConnections() {
        while !available.isEmpty && !waiting.isEmpty {
            let tunnelID = available.removeFirst()
            guard let tunnel = tunnels[tunnelID] else { continue }
            let browserID = waiting.removeFirst()
            guard let browser = browsers.removeValue(forKey: browserID) else {
                available.insert(tunnelID, at: 0)
                continue
            }
            tunnel.attach(browser)
        }
    }

    private func report() {
        let ready = webReady && usbReady && tunnels.values.contains { $0.isReady }
        onStatus?(ready, ready ? "USB connected" : "Connect USB-C and start Weylus and the USB relay on Linux.")
    }

    private func shutdown(_ message: String) {
        running = false
        webReady = false
        usbReady = false
        usbListener?.cancel()
        webListener?.cancel()
        usbListener = nil
        webListener = nil
        let oldTunnels = Array(tunnels.values)
        tunnels.removeAll()
        available.removeAll()
        for tunnel in oldTunnels { tunnel.finish() }
        for browser in browsers.values { browser.cancel() }
        browsers.removeAll()
        waiting.removeAll()
        onStatus?(false, message)
    }
}

private final class Tunnel {
    let id = UUID()
    var onReady: (() -> Void)?
    var onClose: ((UUID) -> Void)?
    private(set) var isReady = false
    private let usb: NWConnection
    private let queue: DispatchQueue
    private var browser: NWConnection?
    private var closed = false
    private var usbEOF = false
    private var browserEOF = false

    init(connection: NWConnection, queue: DispatchQueue) {
        usb = connection
        self.queue = queue
    }

    func start() {
        usb.stateUpdateHandler = { [weak self] state in
            guard let self = self, !self.closed else { return }
            switch state {
            case .ready:
                self.readHello()
            case .failed, .cancelled:
                self.finish()
            default:
                break
            }
        }
        usb.start(queue: queue)
        queue.asyncAfter(deadline: .now() + 10) { [weak self] in
            if let self = self, !self.isReady { self.finish() }
        }
    }

    private func readHello() {
        usb.receive(minimumIncompleteLength: hello.count, maximumLength: hello.count) {
            [weak self] data, _, complete, error in
            guard let self = self, !self.closed else { return }
            guard error == nil, !complete, data == hello else { self.finish(); return }
            self.isReady = true
            self.onReady?()
            // Start receiving even while idle so cable removal/EOF is detected.
            self.receive(fromUSB: true)
        }
    }

    func attach(_ connection: NWConnection) {
        guard !closed, browser == nil else { connection.cancel(); return }
        browser = connection
        connection.stateUpdateHandler = { [weak self] state in
            switch state {
            case .failed, .cancelled: self?.finish()
            default: break
            }
        }
        usb.send(content: startStream, completion: .contentProcessed { [weak self] error in
            guard let self = self, !self.closed else { return }
            if error != nil { self.finish() } else { self.receive(fromUSB: false) }
        })
    }

    private func receive(fromUSB: Bool) {
        guard !closed, let source = fromUSB ? usb : browser else { return }
        source.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1024) {
            [weak self] data, _, complete, error in
            guard let self = self, !self.closed else { return }
            guard error == nil, let destination = fromUSB ? self.browser : self.usb else {
                self.finish()
                return
            }
            // Wait for each send completion before reading again: memory stays
            // bounded even when video is produced faster than the iPad reads.
            destination.send(content: data, contentContext: complete ? .finalMessage : .defaultMessage,
                             isComplete: true, completion: .contentProcessed { [weak self] error in
                guard let self = self, !self.closed else { return }
                if error != nil {
                    self.finish()
                } else if complete {
                    if fromUSB { self.usbEOF = true } else { self.browserEOF = true }
                    if self.usbEOF && self.browserEOF { self.finish() }
                } else {
                    self.receive(fromUSB: fromUSB)
                }
            })
        }
    }

    func finish() {
        guard !closed else { return }
        closed = true
        isReady = false
        usb.cancel()
        browser?.cancel()
        onClose?(id)
        onClose = nil
        onReady = nil
    }
}
