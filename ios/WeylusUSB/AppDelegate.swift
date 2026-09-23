import UIKit
import WebKit

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    private let controller = TabletController()

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = controller
        window.makeKeyAndVisible()
        self.window = window
        return true
    }

    func applicationDidBecomeActive(_ application: UIApplication) {
        application.isIdleTimerDisabled = true
        controller.start()
    }

    func applicationDidEnterBackground(_ application: UIApplication) {
        application.isIdleTimerDisabled = false
        controller.stop()
    }
}

final class TabletController: UIViewController, WKNavigationDelegate {
    private let bridge = USBBridge()
    private let status = UILabel()
    private var webView: WKWebView!
    private var connected = false
    private let localURL = URL(string: "http://127.0.0.1:1701/")!

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        let configuration = WKWebViewConfiguration()
        configuration.allowsInlineMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        configuration.allowsAirPlayForMediaPlayback = false
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.scrollView.bounces = false
        webView.isHidden = true
        status.text = "Connect USB-C, open Weylus and start the USB relay on Linux."
        status.numberOfLines = 0
        status.font = .preferredFont(forTextStyle: .footnote)
        let reload = UIButton(type: .system)
        reload.setTitle("Reload", for: .normal)
        reload.addTarget(self, action: #selector(reloadPage), for: .touchUpInside)
        let bar = UIStackView(arrangedSubviews: [status, reload])
        bar.spacing = 12
        bar.alignment = .center
        for item in [bar, webView!] {
            item.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(item)
        }
        NSLayoutConstraint.activate([
            bar.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 8),
            bar.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 12),
            bar.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -12),
            webView.topAnchor.constraint(equalTo: bar.bottomAnchor, constant: 8),
            webView.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor),
            webView.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor),
        ])
        bridge.onStatus = { [weak self] ready, message in
            DispatchQueue.main.async {
                guard let self = self else { return }
                let wasConnected = self.connected
                self.connected = ready
                self.status.text = message
                self.webView.isHidden = !ready
                if ready && !wasConnected {
                    self.reloadPage()
                } else if !ready {
                    self.webView.stopLoading()
                }
            }
        }
    }

    func start() {
        loadViewIfNeeded()
        bridge.start()
    }

    func stop() {
        bridge.stop()
    }

    @objc private func reloadPage() {
        if connected {
            webView.load(URLRequest(url: localURL))
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        // Keep all top-level navigation inside the local tunnel, including the
        // existing Weylus access-code form. Never follow external links here.
        let url = action.request.url
        let local = url?.scheme == "http" && url?.host == "127.0.0.1" && url?.port == 1701
        decisionHandler(local ? .allow : .cancel)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        if connected {
            status.text = "Page could not load. Check Weylus on Linux, then tap Reload."
        }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        reloadPage()
    }
}
