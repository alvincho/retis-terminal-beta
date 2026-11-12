# 從原始碼建置 / Building from source

一般使用者請直接下載 [最新版本](https://github.com/alvincho/retis-terminal-beta/releases/latest) 的安裝檔。這份說明給想從這個儲存庫的原始碼自行建置 Mac 或 Windows 版 Retis Terminal 的人。

## 需要的工具

| | Mac | Windows |
| --- | --- | --- |
| 作業系統 | macOS 12 以上，Apple 晶片較順 | Windows 10 或 11 |
| Python | 3.13（建置用，例如 `brew install python@3.13`） | 3.13（[python.org](https://www.python.org/downloads/)） |
| Node.js | 20 以上，含 npm（只在建置時用來預先建好代理程式畫面） | 20 以上，含 npm |
| 其他 | Xcode 命令列工具：`xcode-select --install` | [Inno Setup 6](https://jrsoftware.org/isinfo.php)；.NET Framework 4.8 的 `csc` 已內建於 Windows |

建置過程會下載 python-build-standalone 的 Python 3.13 與 `requirements.lock` 列出的套件，每個檔案都以 SHA-256 核對。安裝檔帶著自己的 Python，使用者不需要另外安裝 Python 或 Node.js。

## 建置步驟

```bash
git clone https://github.com/alvincho/retis-terminal-beta.git
cd retis-terminal-beta
python3.13 -m venv .venv          # Windows：py -3.13 -m venv .venv
source .venv/bin/activate        # Windows：.venv\Scripts\activate
python -m pip install --require-hashes -r requirements.lock
python desktop/build_desktop_app.py --platform mac        # Windows：--platform windows
```

- **Mac**：產生 `dist/Retis-Terminal-<版本>.dmg` 與它的 `.sha256`，裡面是同時支援 Apple 晶片與 Intel 的 `Retis Terminal.app`。
- **Windows**：產生 `dist/Retis-Terminal-Setup-<版本>.exe` 與它的 `.sha256`，安裝時不需要系統管理員權限。安裝介面要有繁體中文，請先把 Inno Setup 的非官方翻譯 [ChineseTraditional.isl](https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/Unofficial/ChineseTraditional.isl) 放進 Inno Setup 6 的 `Languages` 資料夾；沒有它，安裝介面只有英文。

版本、連線的網站與更新來源預設取自 `VERSION.json`，可以用 `--version`、`--site`、`--update-feed` 改掉。`python desktop/build_desktop_app.py --help` 列出所有選項。

## 簽署

不加選項時建置出來的程式沒有簽署（Mac 是 ad hoc 簽署），安裝時會出現與測試版相同的警告，見 [Mac 安裝](docs/zh-TW/install-mac.md) 與 [Windows 安裝](docs/zh-TW/install-windows.md)。有自己的憑證時：

- **Mac**：`--sign-identity "Developer ID Application: …"` 簽署，再加 `--notary-profile <notarytool 鑰匙圈設定檔>` 公證並裝訂 .dmg。
- **Windows**：`--windows-sign-command "signtool sign /fd SHA256 … {file}"`，`{file}` 是每個要簽署的檔案。

## 和下載的版本比對

每個 Retis Terminal 都帶著 `kit/package-manifest.json`，列出程式裡每個檔案的 SHA-256。用同一版的原始碼建置後，可以把您的 `dist/kit/package-manifest.json` 和下載版本裡的同名檔案比對：

- Mac：`Retis Terminal.app/Contents/Resources/kit/package-manifest.json`
- Windows：`%LOCALAPPDATA%\Programs\Retis Terminal\kit\package-manifest.json`

```bash
python desktop/build_desktop_app.py --compare dist/kit/package-manifest.json "<下載版本的 package-manifest.json>"
```

應該只有三個檔案不同：`prebuilt/prebuilt.json`、`prebuilt/agent-view/container-config.json` 與 `prebuilt/agent-view/host-release.json`。它們記錄建置時所在的資料夾，Retis Terminal 啟動時會改用安裝位置。其他檔案都應該完全相同。

---

Most people should download the installer from the [latest release](https://github.com/alvincho/retis-terminal-beta/releases/latest). This guide is for anyone who wants to build Retis Terminal for Mac or Windows from the source in this repository.

## What you need

| | Mac | Windows |
| --- | --- | --- |
| System | macOS 12 or later; Apple silicon is smoothest | Windows 10 or 11 |
| Python | 3.13 to run the build (for example `brew install python@3.13`) | 3.13 ([python.org](https://www.python.org/downloads/)) |
| Node.js | 20 or later, with npm (used only during the build, to prebuild agent views) | 20 or later, with npm |
| Also | Xcode Command Line Tools: `xcode-select --install` | [Inno Setup 6](https://jrsoftware.org/isinfo.php); `csc` from .NET Framework 4.8 comes with Windows |

The build downloads Python 3.13 from python-build-standalone and the libraries in `requirements.lock`, and checks every file's SHA-256. The app carries its own Python, so its users need neither Python nor Node.js.

## Build

```bash
git clone https://github.com/alvincho/retis-terminal-beta.git
cd retis-terminal-beta
python3.13 -m venv .venv          # Windows: py -3.13 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install --require-hashes -r requirements.lock
python desktop/build_desktop_app.py --platform mac        # Windows: --platform windows
```

- **Mac:** makes `dist/Retis-Terminal-<version>.dmg` and its `.sha256`, holding one `Retis Terminal.app` for both Apple silicon and Intel.
- **Windows:** makes `dist/Retis-Terminal-Setup-<version>.exe` and its `.sha256`, a per-user installer that needs no administrator rights. For a Traditional Chinese installer, first put Inno Setup's unofficial [ChineseTraditional.isl](https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/Unofficial/ChineseTraditional.isl) in Inno Setup 6's `Languages` folder; without it the installer is English only.

The version, the site the app connects to and the update feed come from `VERSION.json`; `--version`, `--site` and `--update-feed` change them. `python desktop/build_desktop_app.py --help` lists every option.

## Signing

Without options the build is unsigned (ad hoc signed on Mac), so installing it shows the same warning as the beta; see [Install on Mac](docs/en/install-mac.md) and [Install on Windows](docs/en/install-windows.md). With your own certificates:

- **Mac:** `--sign-identity "Developer ID Application: …"` signs it, and `--notary-profile <notarytool keychain profile>` notarizes and staples the .dmg.
- **Windows:** `--windows-sign-command "signtool sign /fd SHA256 … {file}"`, where `{file}` is each file to sign.

## Compare with the download

Every Retis Terminal carries `kit/package-manifest.json`, the SHA-256 of every file in the app. After building the same version's source, compare your `dist/kit/package-manifest.json` with the one in the downloaded app:

- Mac: `Retis Terminal.app/Contents/Resources/kit/package-manifest.json`
- Windows: `%LOCALAPPDATA%\Programs\Retis Terminal\kit\package-manifest.json`

```bash
python desktop/build_desktop_app.py --compare dist/kit/package-manifest.json "<the downloaded app's package-manifest.json>"
```

Exactly three files should differ: `prebuilt/prebuilt.json`, `prebuilt/agent-view/container-config.json` and `prebuilt/agent-view/host-release.json`. They record the folder the build ran in, which Retis Terminal replaces with its own location when it starts. Every other file should be the same.
