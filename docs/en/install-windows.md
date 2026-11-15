# Install Retis Terminal on Windows

About 3 minutes, no administrator rights and no command line. Windows 10 and 11 (64-bit).

1. On the [latest release](https://github.com/alvincho/retis-terminal-beta/releases/latest) page, under **Assets**, download `Retis-Terminal-Setup-<version>.exe`.
2. Open it. This beta isn't signed yet, so Windows may say **Windows protected your PC**: choose **More info** → **Run anyway**. Then choose **Next** → **Install** → **Finish**.
3. Open **Retis Terminal** from the **Start** menu.
4. A Retis Terminal icon appears in the system tray (the notification area at the bottom right; Windows may first keep it under **^**, Show hidden icons) and your browser opens Retis Terminal. Continue with [First run](first-run.md).

## Check the download (optional)

Download only from this repository's [Releases](https://github.com/alvincho/retis-terminal-beta/releases). Each installer has a `.sha256` file beside it with the installer's SHA-256 fingerprint. To check that your download is complete and unchanged, run this in PowerShell:

```powershell
Get-FileHash "$env:USERPROFILE\Downloads\Retis-Terminal-Setup-<version>.exe" -Algorithm SHA256
```

Compare the long string it prints with the one in the `.sha256` file. If they match, you're fine; if not, don't install it, and download it again.

## Good to know

- **Closing the browser tab doesn't quit Retis Terminal.** Reopen it from the system tray icon → **Open**; quit with **Quit**.
- **Updates:** the icon shows **New version available**; choose **Download** and run the new installer. Your data and sign-in are kept.
- **Uninstall:** **Settings → Apps**, Retis Terminal, **Uninstall**. Your workspaces stay in `%LOCALAPPDATA%\Retis Terminal` until you delete them.
