// Retis Terminal for Windows: a tray app that starts the Terminal and opens it in the default browser.
//
// The Terminal itself is Python (kit\) run with the bundled Python (python\python.exe). This shell only keeps the
// vault key with Windows DPAPI, asks kit\retis_desktop.py to start, stop, report and check for updates, and opens the
// address it prints. The address carries this launch's local-access secret, so it goes straight to the browser.
//
// Written for the C# 5 compiler that ships with .NET Framework 4.8 on every Windows 10 and 11:
//   csc /target:winexe /win32icon:RetisTerminal.ico /win32manifest:RetisTerminal.manifest
//       /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Security.dll /r:System.Web.Extensions.dll
//       /out:RetisTerminal.exe RetisTerminal.cs
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace Retis
{
    /// The tray speaks Windows' display language: Traditional Chinese (zh-TW, zh-HK, zh-MO), Simplified Chinese
    /// (zh-CN, zh-SG) or English. Public so the build can check LanguageOf in the compiled app.
    public static class Words
    {
        public static string LanguageOf(CultureInfo culture)
        {
            // zh-CN's parents are zh-CHS and zh-Hans, zh-TW's zh-CHT and zh-Hant: the script decides, whichever name.
            for (CultureInfo c = culture; !string.IsNullOrEmpty(c.Name); c = c.Parent)
            {
                switch (c.Name.ToLowerInvariant())
                {
                    case "zh-hans": case "zh-chs": case "zh-cn": case "zh-sg": return "zh-Hans";
                    case "zh-hant": case "zh-cht": case "zh-tw": case "zh-hk": case "zh-mo": return "zh-Hant";
                }
            }
            return culture.Name.StartsWith("zh", StringComparison.OrdinalIgnoreCase) ? "zh-Hant" : "en";
        }

        static readonly string Language = LanguageOf(CultureInfo.CurrentUICulture);

        public static string T(string hant, string hans, string en)
        {
            return Language == "zh-Hant" ? hant : Language == "zh-Hans" ? hans : en;
        }
    }

    /// The key that encrypts saved API keys and sessions in the Terminal's vault, protected for this Windows user.
    static class VaultKey
    {
        public static string Load(string folder)
        {
            string path = Path.Combine(folder, "vault-key.dpapi");
            if (File.Exists(path))
            {
                // A key that can't be read stops the app: it is never replaced, or the saved data could not be opened.
                byte[] plain = ProtectedData.Unprotect(File.ReadAllBytes(path), null, DataProtectionScope.CurrentUser);
                return Encoding.UTF8.GetString(plain);
            }
            byte[] bytes = new byte[32];
            using (RandomNumberGenerator random = RandomNumberGenerator.Create()) random.GetBytes(bytes);
            string key = Convert.ToBase64String(bytes).Replace('+', '-').Replace('/', '_');
            Directory.CreateDirectory(folder);
            string temporary = path + ".tmp";
            File.WriteAllBytes(temporary, ProtectedData.Protect(Encoding.UTF8.GetBytes(key), null, DataProtectionScope.CurrentUser));
            File.Move(temporary, path);
            return key;
        }
    }

    class Controller
    {
        public readonly string AppFolder = AppDomain.CurrentDomain.BaseDirectory;
        public readonly string State = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Retis Terminal");
        public string VaultKeyValue = "";

        public Dictionary<string, object> Run(params string[] arguments)
        {
            ProcessStartInfo info = new ProcessStartInfo(Path.Combine(AppFolder, "python", "python.exe"));
            StringBuilder line = new StringBuilder("-I \"" + Path.Combine(AppFolder, "kit", "retis_desktop.py") + "\"");
            foreach (string argument in arguments) line.Append(" " + argument);
            info.Arguments = line.ToString();
            info.UseShellExecute = false;
            // No console window for the controller, and none for the Terminal it starts: they share this hidden console.
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.WorkingDirectory = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            foreach (string name in new[] { "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP" }) info.EnvironmentVariables.Remove(name);
            info.EnvironmentVariables["ATTAS_TERMINAL_VAULT_KEY"] = VaultKeyValue;
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            Directory.CreateDirectory(State);
            string errors = Path.Combine(State, "desktop-stderr.log");
            try
            {
                using (Process process = Process.Start(info))
                {
                    process.ErrorDataReceived += (sender, e) => { if (e.Data != null) { try { File.AppendAllText(errors, e.Data + Environment.NewLine); } catch (IOException) { } } };
                    process.BeginErrorReadLine();
                    string output = process.StandardOutput.ReadToEnd();
                    process.WaitForExit();
                    string[] lines = output.Split(new[] { '\n' }, StringSplitOptions.RemoveEmptyEntries);
                    JavaScriptSerializer json = new JavaScriptSerializer();
                    for (int i = lines.Length - 1; i >= 0; i--)
                    {
                        try { return json.Deserialize<Dictionary<string, object>>(lines[i].Trim()); }
                        catch (ArgumentException) { }
                        catch (InvalidOperationException) { }
                    }
                    return Failure("The Terminal controller stopped with code " + process.ExitCode + ".");
                }
            }
            catch (Exception error) { return Failure(error.Message); }
        }

        static Dictionary<string, object> Failure(string message)
        {
            Dictionary<string, object> value = new Dictionary<string, object>();
            value["error"] = message;
            return value;
        }
    }

    class TrayApp : ApplicationContext
    {
        const int CheckEvery = 6 * 3600 * 1000;  // the controller checks the feed at most once a day
        readonly Controller controller = new Controller();
        readonly NotifyIcon icon = new NotifyIcon();
        readonly ContextMenuStrip menu = new ContextMenuStrip();
        readonly ToolStripMenuItem statusLine = new ToolStripMenuItem();
        readonly ToolStripMenuItem updateLine = new ToolStripMenuItem();
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        readonly Control invoker = new Control();
        readonly string version;
        string download;
        bool running;

        public TrayApp()
        {
            invoker.CreateControl();
            version = ReadVersion();
            statusLine.Enabled = false;
            updateLine.Visible = false;
            updateLine.Click += (s, e) => OpenDownload();
            menu.Items.Add(statusLine);
            menu.Items.Add(updateLine);
            menu.Items.Add(new ToolStripSeparator());
            ToolStripMenuItem open = new ToolStripMenuItem(Words.T("開啟", "打开", "Open"), null, (s, e) => OpenTerminal());
            open.Font = new Font(open.Font, FontStyle.Bold);
            menu.Items.Add(open);
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(new ToolStripMenuItem(Words.T("儲存問題報告…", "保存问题报告…", "Save Problem Report…"), null, (s, e) => SaveReport()));
            menu.Items.Add(new ToolStripMenuItem(Words.T("檢查更新…", "检查更新…", "Check for Updates…"), null, (s, e) => CheckUpdate(true)));
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add(new ToolStripMenuItem(Words.T("結束", "退出", "Quit"), null, (s, e) => Quit()));
            string iconPath = Path.Combine(controller.AppFolder, "RetisTerminal.ico");
            icon.Icon = File.Exists(iconPath) ? new Icon(iconPath) : SystemIcons.Application;
            icon.Text = "Retis Terminal";
            icon.ContextMenuStrip = menu;
            icon.DoubleClick += (s, e) => OpenTerminal();
            icon.Visible = true;
            try { controller.VaultKeyValue = VaultKey.Load(controller.State); }
            catch (Exception error)
            {
                Show(Words.T("無法讀取保護您資料的金鑰。請用安裝 Retis Terminal 的同一個 Windows 帳號開啟。",
                             "无法读取保护您数据的密钥。请使用安装 Retis Terminal 的同一个 Windows 账户打开。",
                             "It couldn't read the key that protects your data. Open Retis Terminal with the Windows account that installed it.")
                     + Environment.NewLine + error.Message, MessageBoxIcon.Error);
                icon.Visible = false;
                Environment.Exit(1);
            }
            Start(true);
            timer.Interval = CheckEvery;
            timer.Tick += (s, e) => CheckUpdate(false);
            timer.Start();
            System.Windows.Forms.Timer first = new System.Windows.Forms.Timer();
            first.Interval = 20000;
            first.Tick += (s, e) => { first.Stop(); CheckUpdate(false); };
            first.Start();
        }

        string ReadVersion()
        {
            string path = Path.Combine(controller.AppFolder, "kit", "package-manifest.json");
            try
            {
                Dictionary<string, object> manifest = new JavaScriptSerializer() { MaxJsonLength = 64 * 1024 * 1024 }
                    .Deserialize<Dictionary<string, object>>(File.ReadAllText(path));
                return manifest.ContainsKey("version") ? Convert.ToString(manifest["version"]) : "";
            }
            catch (Exception) { return ""; }
        }

        /// Run a controller command on a worker thread and hand its result back on the UI thread.
        void Background(string[] arguments, Action<Dictionary<string, object>> done)
        {
            ThreadPool.QueueUserWorkItem(_ =>
            {
                Dictionary<string, object> result = controller.Run(arguments);
                invoker.BeginInvoke(new Action(() => done(result)));
            });
        }

        static string Text(Dictionary<string, object> value, string key)
        {
            object found;
            return value.TryGetValue(key, out found) && found != null ? Convert.ToString(found) : null;
        }

        static bool Flag(Dictionary<string, object> value, string key)
        {
            object found;
            return value.TryGetValue(key, out found) && found is bool && (bool)found;
        }

        void SetStatus(string state)
        {
            statusLine.Text = "Retis Terminal " + version + " — " + state;
            string tip = "Retis Terminal — " + state;
            icon.Text = tip.Length > 63 ? tip.Substring(0, 63) : tip;
        }

        void Start(bool openAfter)
        {
            SetStatus(Words.T("啟動中…", "正在启动…", "Starting…"));
            Background(new[] { "start" }, result =>
            {
                string address = Text(result, "open");
                if (address != null)
                {
                    running = true;
                    SetStatus(Words.T("執行中", "正在运行", "Running"));
                    if (openAfter) Browse(address);
                    return;
                }
                running = false;
                SetStatus(Words.T("無法啟動", "无法启动", "Not running"));
                DialogResult answer = MessageBox.Show(
                    (Text(result, "error") ?? "") + Environment.NewLine + Environment.NewLine +
                    Words.T("要再試一次嗎？也可以從系統匣圖示選「儲存問題報告」。", "要再试一次吗？也可以从系统托盘图标选择“保存问题报告”。", "Try again? You can also choose Save Problem Report from the tray icon."),
                    Words.T("Retis Terminal 無法啟動", "Retis Terminal 无法启动", "Retis Terminal couldn't start"), MessageBoxButtons.RetryCancel, MessageBoxIcon.Warning);
                if (answer == DialogResult.Retry) Start(true);
            });
        }

        void OpenTerminal()
        {
            if (!running) { Start(true); return; }
            Background(new[] { "address" }, result =>
            {
                string address = Text(result, "open");
                if (address != null) Browse(address);
                else { running = false; Start(true); }  // It stopped since; start it again.
            });
        }

        void SaveReport()
        {
            Background(new[] { "report" }, result =>
            {
                string path = Text(result, "report");
                if (path == null) { Show(Text(result, "error") ?? "", MessageBoxIcon.Warning); return; }
                Process.Start("explorer.exe", "/select,\"" + path + "\"");
                Show(Words.T("已儲存問題報告：", "已保存问题报告：", "Problem report saved: ") + Path.GetFileName(path) + Environment.NewLine +
                     Words.T("報告不含密碼與金鑰，附加到問題回報前請先看過內容。", "报告不含密码和密钥，附加到问题反馈前请先查看内容。", "It has no passwords or keys; please look it over before attaching it."),
                     MessageBoxIcon.Information);
            });
        }

        void CheckUpdate(bool force)
        {
            Background(force ? new[] { "check-update", "--force" } : new[] { "check-update" }, result =>
            {
                string error = Text(result, "error");
                if (error != null) { if (force) Show(error, MessageBoxIcon.Warning); return; }
                string latest = Text(result, "latest");
                if (!Flag(result, "updateAvailable") || latest == null)
                {
                    updateLine.Visible = false;
                    if (force) Show(Words.T("Retis Terminal " + version + " 是最新版本。", "Retis Terminal " + version + " 已是最新版本。", "Retis Terminal " + version + " is the latest version."), MessageBoxIcon.Information);
                    return;
                }
                download = Text(result, "download");
                updateLine.Text = Words.T("有新版本 " + latest + " → 下載", "有新版本 " + latest + " → 下载", "New version " + latest + " → Download");
                updateLine.Visible = true;
                bool required = Flag(result, "required");
                string seenPath = Path.Combine(controller.State, "announced-version.txt");
                string seen = File.Exists(seenPath) ? File.ReadAllText(seenPath).Trim() : "";
                if (force || required || seen != latest)
                {
                    try { File.WriteAllText(seenPath, latest); } catch (IOException) { }
                    string message = required
                        ? Words.T("這個版本已不再支援，請下載 " + latest + "。", "此版本已不再受支持，请下载 " + latest + "。", "This version is no longer supported. Please download " + latest + ".")
                        : Words.T("Retis Terminal " + latest + " 已推出（您使用的是 " + version + "）。要開啟下載頁面嗎？",
                                  "Retis Terminal " + latest + " 已发布（您使用的是 " + version + "）。要打开下载页面吗？",
                                  "Retis Terminal " + latest + " is available (you have " + version + "). Open the download page?");
                    if (MessageBox.Show(message, Words.T("有新版本", "有新版本", "New version"), MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
                        OpenDownload();
                }
            });
        }

        void OpenDownload()
        {
            if (!string.IsNullOrEmpty(download) && download.StartsWith("https://", StringComparison.Ordinal)) Browse(download);
        }

        static void Browse(string address)
        {
            // The browser downloads updates itself; nothing installs silently.
            Process.Start(new ProcessStartInfo(address) { UseShellExecute = true });
        }

        static void Show(string text, MessageBoxIcon kind)
        {
            MessageBox.Show(text, "Retis Terminal", MessageBoxButtons.OK, kind);
        }

        void Quit()
        {
            SetStatus(Words.T("正在結束…", "正在退出…", "Stopping…"));
            timer.Stop();
            if (!running) { Finish(); return; }
            Background(new[] { "stop" }, result => Finish());
        }

        void Finish()
        {
            icon.Visible = false;
            icon.Dispose();
            ExitThread();
        }

        [STAThread]
        static void Main()
        {
            bool first;
            using (Mutex single = new Mutex(true, "Local\\RetisTerminalTray", out first))
            {
                if (!first)
                {
                    // Opening the app again opens the Terminal again.
                    Controller other = new Controller();
                    try { other.VaultKeyValue = VaultKey.Load(other.State); } catch (Exception) { return; }
                    string address = Text(other.Run("address"), "open");
                    if (address != null) Browse(address);
                    return;
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new TrayApp());
            }
        }
    }
}
