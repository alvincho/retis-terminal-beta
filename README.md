# Retis Terminal 公開測試版

Retis Terminal 是在您自己電腦上執行的投資研究工具。安裝後登入，就能用「策略監看」建立配對交易策略、看即時價格、做回測與模擬交易。不需要寫程式，也不需要打任何指令。您的工作區、設定與 API 金鑰都留在您的電腦上。

**免費測試中。** 這是測試版，只做模擬交易，不會真的下單，也不是投資建議。

## 三個步驟開始

1. **下載**：到 [最新版本](https://github.com/alvincho/retis-terminal-beta/releases/latest) 頁面，Mac 下載 `Retis-Terminal-<版本>.dmg`，Windows 下載 `Retis-Terminal-Setup-<版本>.exe`。
2. **安裝**：照著 [Mac 安裝](docs/zh-TW/install-mac.md) 或 [Windows 安裝](docs/zh-TW/install-windows.md) 做，約 3 分鐘，不需要命令列。
3. **開啟並登入**：打開 Retis Terminal，按 **登入 tw-001-olive-canyon.retis.ai**，建立帳號並驗證 email。回到 Retis Terminal 後，「台灣半導體觀察」與「策略監看」已經開好了。詳見 [第一次使用](docs/zh-TW/first-run.md)。

選單列（Mac）或系統匣（Windows）的 Retis Terminal 圖示有：**開啟**、**儲存問題報告**、**檢查更新**、**結束**。遇到問題時選 **儲存問題報告**，把檔案附加到 [問題回報](https://github.com/alvincho/retis-terminal-beta/issues/new/choose)；報告不含密碼與金鑰。

## 說明文件

| 繁體中文 | English |
| --- | --- |
| [Mac 安裝](docs/zh-TW/install-mac.md) | [Install on Mac](docs/en/install-mac.md) |
| [Windows 安裝](docs/zh-TW/install-windows.md) | [Install on Windows](docs/en/install-windows.md) |
| [第一次使用](docs/zh-TW/first-run.md) | [First run](docs/en/first-run.md) |
| [策略監看入門](docs/zh-TW/strategy-monitor.md) | [Strategy Monitor basics](docs/en/strategy-monitor.md) |
| [點數說明](docs/zh-TW/points.md) | [Points](docs/en/points.md) |
| [常見問題](docs/zh-TW/faq.md) | [FAQ](docs/en/faq.md) |
| [已知限制](docs/zh-TW/known-limits.md) | [Known limits](docs/en/known-limits.md) |
| [隱私](legal/PRIVACY.zh-TW.md) | [Privacy](legal/PRIVACY.en.md) |
| [使用條款](legal/TERMS.zh-TW.md) | [Terms of use](legal/TERMS.en.md) |
| [回報問題](docs/zh-TW/report-a-problem.md) | [Report a problem](docs/en/report-a-problem.md) |

## 開放原始碼

這個儲存庫包含每個版本實際附帶的 Retis Terminal 原始碼（`attas/`、`phemacast/`、`prompits/`、`scripts/`）與桌面程式（`desktop/`），以 [MIT 授權](LICENSE) 釋出；附帶的第三方軟體見 `THIRD_PARTY_NOTICES.txt`。Retis Terminal 連線使用的 tw-001 服務及其上的代理程式不在此授權範圍內，使用 tw-001 適用[使用條款](legal/TERMS.zh-TW.md)。「Retis」與「Attas」名稱及標誌不在 MIT 授權範圍內，請見 [TRADEMARKS.md](TRADEMARKS.md)。想從原始碼自行建置 Mac 或 Windows 安裝檔，請見 [BUILDING.md](BUILDING.md)。參與貢獻請見 [CONTRIBUTING.md](CONTRIBUTING.md)，安全性問題請見 [SECURITY.md](SECURITY.md)。

---

# Retis Terminal public beta

Retis Terminal is an investment research app that runs on your own computer. Install it, sign in, and use Strategy Monitor to build a pairs-trading strategy, watch live prices, backtest and paper trade. No coding and no command line. Your workspaces, settings and API keys stay on your computer.

**Free while in beta.** It paper trades only, never places real orders, and nothing in it is investment advice.

1. **Download** from the [latest release](https://github.com/alvincho/retis-terminal-beta/releases/latest): `Retis-Terminal-<version>.dmg` for Mac or `Retis-Terminal-Setup-<version>.exe` for Windows.
2. **Install** with the [Mac](docs/en/install-mac.md) or [Windows](docs/en/install-windows.md) guide (about 3 minutes, no command line).
3. **Open and sign in**: choose **Sign in to tw-001-olive-canyon.retis.ai**, create an account and verify your email. Back in Retis Terminal, Taiwan Semiconductor Watch and Strategy Monitor are already open. See [First run](docs/en/first-run.md).

The Retis Terminal icon in the menu bar (Mac) or system tray (Windows) offers **Open**, **Save Problem Report**, **Check for Updates** and **Quit**. When something goes wrong, choose **Save Problem Report** and attach the file to a [new issue](https://github.com/alvincho/retis-terminal-beta/issues/new/choose); the report contains no passwords or keys.

This repository holds the Retis Terminal source each version ships (`attas/`, `phemacast/`, `prompits/`, `scripts/`) and the desktop app (`desktop/`) under the [MIT license](LICENSE); bundled third-party software is listed in `THIRD_PARTY_NOTICES.txt`. The tw-001 service and its agents are not covered by it; using tw-001 is governed by the [Terms of use](legal/TERMS.en.md). The Retis and Attas names and logos are not licensed; see [TRADEMARKS.md](TRADEMARKS.md). To build the Mac or Windows app yourself from this source, see [BUILDING.md](BUILDING.md). See also [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
