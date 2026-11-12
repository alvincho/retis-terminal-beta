// Retis Terminal for macOS: a menu-bar app that starts the Terminal and opens it in the default browser.
//
// The Terminal itself is Python (Contents/Resources/kit) run with the bundled Python (Contents/Resources/python).
// This shell only: keeps the vault key in the Keychain, asks packaging/desktop/retis_desktop.py to start, stop,
// report and check for updates, and opens the address it prints. The address carries this launch's
// local-access secret, so it goes straight to the browser and is never shown.
import Cocoa
import Security

private let product = "Retis Terminal"
private let checkEvery: TimeInterval = 6 * 3600  // the controller checks the feed at most once a day

private enum Words {
    static let chinese = (Locale.preferredLanguages.first ?? "zh-Hant").hasPrefix("zh")
    static func t(_ zh: String, _ en: String) -> String { chinese ? zh : en }
    static let open = t("開啟", "Open")
    static let report = t("儲存問題報告", "Save Problem Report")
    static let update = t("檢查更新", "Check for Updates")
    static let quit = t("結束", "Quit")
    static let starting = t("啟動中…", "Starting…")
    static let running = t("執行中", "Running")
    static let stopping = t("正在結束…", "Stopping…")
    static let failed = t("無法啟動", "Not running")
    static let later = t("稍後", "Later")
    static let download = t("下載", "Download")
    static let ok = t("好", "OK")
}

/// The key that encrypts saved API keys and sessions in the Terminal's vault; kept in this Mac's Keychain only.
private enum VaultKey {
    static let service = "ai.retis.terminal"
    static let account = "vault-key"

    enum Problem: Error { case keychain(OSStatus) }

    static func load() throws -> String {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                    kSecAttrAccount as String: account, kSecReturnData as String: true]
        var item: CFTypeRef?
        let found = SecItemCopyMatching(query as CFDictionary, &item)
        if found == errSecSuccess, let data = item as? Data, let key = String(data: data, encoding: .utf8) {
            return key
        }
        // Only a missing key is made anew: a refused or locked Keychain must never replace the key the vault uses.
        guard found == errSecItemNotFound else { throw Problem.keychain(found) }
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { throw Problem.keychain(errSecParam) }
        let key = Data(bytes).base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
        let add: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                  kSecAttrAccount as String: account, kSecAttrLabel as String: "\(product) vault key",
                                  kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
                                  kSecValueData as String: Data(key.utf8)]
        let added = SecItemAdd(add as CFDictionary, nil)
        guard added == errSecSuccess else { throw Problem.keychain(added) }
        return key
    }
}

private struct Controller {
    let resources = Bundle.main.resourceURL!
    var python: URL { resources.appendingPathComponent("python/bin/python3") }
    var script: URL { resources.appendingPathComponent("kit/retis_desktop.py") }
    var state: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent(product)
    }
    var vaultKey = ""

    /// Run one controller command off the main thread and hand its JSON line back on the main thread.
    func run(_ arguments: [String], done: @escaping ([String: Any]) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let result = self.runNow(arguments)
            DispatchQueue.main.async { done(result) }
        }
    }

    func runNow(_ arguments: [String]) -> [String: Any] {
        let process = Process()
        process.executableURL = python
        process.arguments = ["-I", script.path] + arguments
        var environment = ProcessInfo.processInfo.environment
        for name in ["PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP"] { environment.removeValue(forKey: name) }
        environment["ATTAS_TERMINAL_VAULT_KEY"] = vaultKey
        process.environment = environment
        process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
        let output = Pipe()
        process.standardOutput = output
        try? FileManager.default.createDirectory(at: state, withIntermediateDirectories: true)
        let errors = state.appendingPathComponent("desktop-stderr.log")
        if !FileManager.default.fileExists(atPath: errors.path) { FileManager.default.createFile(atPath: errors.path, contents: nil) }
        let errorLog = try? FileHandle(forWritingTo: errors)
        errorLog?.seekToEndOfFile()
        process.standardError = errorLog ?? FileHandle.nullDevice
        do { try process.run() } catch { return ["error": "\(error.localizedDescription)"] }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        try? errorLog?.close()
        let lines = String(decoding: data, as: UTF8.self).split(separator: "\n")
        for line in lines.reversed() {
            if let value = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] { return value }
        }
        return ["error": "The Terminal controller stopped with code \(process.terminationStatus)."]
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var controller = Controller()
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private let menu = NSMenu()
    private let statusLine = NSMenuItem(title: Words.starting, action: nil, keyEquivalent: "")
    private let updateLine = NSMenuItem(title: "", action: #selector(downloadUpdate), keyEquivalent: "")
    private var download: URL?
    private var running = false
    private var quitting = false
    private var timer: Timer?

    private var version: String {
        Bundle.main.object(forInfoDictionaryKey: "RetisTerminalVersion") as? String ?? ""
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        item.button?.image = Self.menuIcon()
        item.button?.toolTip = product
        statusLine.isEnabled = false
        updateLine.isHidden = true
        updateLine.target = self
        menu.addItem(statusLine)
        menu.addItem(updateLine)
        menu.addItem(.separator())
        menu.addItem(withTitle: Words.open, action: #selector(openTerminal), keyEquivalent: "o").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: Words.report + "…", action: #selector(saveReport), keyEquivalent: "").target = self
        menu.addItem(withTitle: Words.update + "…", action: #selector(checkUpdateNow), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: Words.quit, action: #selector(quit), keyEquivalent: "q").target = self
        item.menu = menu
        do {
            controller.vaultKey = try VaultKey.load()
        } catch {
            alert(Words.t("Retis Terminal 需要使用鑰匙圈", "Retis Terminal needs the Keychain"),
                  Words.t("無法讀取保護您資料的金鑰。請允許 Retis Terminal 使用鑰匙圈，然後再開啟一次。",
                          "It couldn't read the key that protects your data. Allow Retis Terminal to use the Keychain, then open it again."))
            NSApp.terminate(nil)
            return
        }
        start(openAfter: true)
        timer = Timer.scheduledTimer(withTimeInterval: checkEvery, repeats: true) { [weak self] _ in self?.checkUpdate(force: false) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) { [weak self] in self?.checkUpdate(force: false) }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        openTerminal()  // Opening the app again opens the Terminal again.
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting || !running { return .terminateNow }
        quitting = true
        statusLine.title = Words.stopping
        controller.run(["stop"]) { _ in NSApp.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }

    private func start(openAfter: Bool) {
        statusLine.title = "\(product) \(version) — \(Words.starting)"
        controller.run(["start"]) { [weak self] result in
            guard let self else { return }
            if let address = result["open"] as? String, let url = URL(string: address) {
                self.running = true
                self.statusLine.title = "\(product) \(self.version) — \(Words.running)"
                if openAfter { NSWorkspace.shared.open(url) }
            } else {
                self.running = false
                self.statusLine.title = "\(product) \(self.version) — \(Words.failed)"
                self.failed(result["error"] as? String ?? "")
            }
        }
    }

    private func failed(_ detail: String) {
        let answer = alert(Words.t("Retis Terminal 無法啟動", "Retis Terminal couldn't start"), detail,
                           buttons: [Words.t("再試一次", "Try Again"), Words.report, Words.later])
        if answer == .alertFirstButtonReturn { start(openAfter: true) }
        if answer == .alertSecondButtonReturn { saveReport() }
    }

    @objc private func openTerminal() {
        if !running { start(openAfter: true); return }
        controller.run(["address"]) { [weak self] result in
            if let address = result["open"] as? String, let url = URL(string: address) {
                NSWorkspace.shared.open(url)
            } else {
                self?.running = false
                self?.start(openAfter: true)  // It stopped since; start it again.
            }
        }
    }

    @objc private func saveReport() {
        controller.run(["report"]) { [weak self] result in
            if let path = result["report"] as? String {
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
                self?.alert(Words.t("已儲存問題報告", "Problem report saved"),
                            Words.t("檔案：\((path as NSString).lastPathComponent)\n報告不含密碼與金鑰，附加到問題回報前請先看過內容。",
                                    "File: \((path as NSString).lastPathComponent)\nIt has no passwords or keys; please look it over before attaching it."))
            } else {
                self?.alert(Words.t("無法儲存問題報告", "Couldn't save the report"), result["error"] as? String ?? "")
            }
        }
    }

    @objc private func checkUpdateNow() { checkUpdate(force: true) }

    private func checkUpdate(force: Bool) {
        controller.run(force ? ["check-update", "--force"] : ["check-update"]) { [weak self] result in
            guard let self else { return }
            if let error = result["error"] as? String {
                if force { self.alert(Words.t("無法檢查更新", "Couldn't check for updates"), error) }
                return
            }
            guard result["updateAvailable"] as? Bool == true, let latest = result["latest"] as? String else {
                self.updateLine.isHidden = true
                if force {
                    self.alert(Words.t("已是最新版本", "You're up to date"),
                               Words.t("Retis Terminal \(self.version) 是最新版本。", "Retis Terminal \(self.version) is the latest version."))
                }
                return
            }
            self.download = (result["download"] as? String).flatMap(URL.init(string:))
            self.updateLine.title = Words.t("有新版本 \(latest) → 下載", "New version \(latest) → Download")
            self.updateLine.isHidden = false
            let required = result["required"] as? Bool == true
            let seen = UserDefaults.standard.string(forKey: "announcedVersion")
            if force || required || seen != latest {
                UserDefaults.standard.set(latest, forKey: "announcedVersion")
                let message = required
                    ? Words.t("這個版本已不再支援，請下載 \(latest)。", "This version is no longer supported. Please download \(latest).")
                    : Words.t("Retis Terminal \(latest) 已推出（您使用的是 \(self.version)）。", "Retis Terminal \(latest) is available (you have \(self.version)).")
                if self.alert(Words.t("有新版本", "New version"), message, buttons: [Words.download, Words.later]) == .alertFirstButtonReturn {
                    self.downloadUpdate()
                }
            }
        }
    }

    @objc private func downloadUpdate() {
        if let download { NSWorkspace.shared.open(download) }  // The browser downloads it; nothing installs itself.
    }

    @objc private func quit() { NSApp.terminate(nil) }

    @discardableResult
    private func alert(_ title: String, _ text: String, buttons: [String] = [Words.ok]) -> NSApplication.ModalResponse {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = text
        for button in buttons { alert.addButton(withTitle: button) }
        return alert.runModal()
    }

    /// The Retis mark (a tall bar, a wide block, a small block) as a template image for the menu bar.
    static func menuIcon() -> NSImage {
        let image = NSImage(size: NSSize(width: 18, height: 18), flipped: true) { _ in
            NSColor.black.setFill()
            for rect in [NSRect(x: 3, y: 2.5, width: 3.6, height: 13.5), NSRect(x: 8, y: 2.5, width: 7.5, height: 6),
                         NSRect(x: 8, y: 10, width: 3.6, height: 6)] {
                NSBezierPath(roundedRect: rect, xRadius: 1, yRadius: 1).fill()
            }
            return true
        }
        image.isTemplate = true
        return image
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
