; Retis Terminal for Windows: a per-user installer that needs no administrator rights.
; scripts/build_desktop_app.py --platform windows compiles it with:
;   iscc /DAppVersion=0.1.0-beta /DSourceDir=<staging folder> /DOutputDir=<dist> [/Sretis="signtool ... $f" /DSignTool=retis]
; The user's data lives in %LOCALAPPDATA%\Retis Terminal, outside the program folder: updates and uninstalling keep
; it unless the user chooses to remove it.
; One installer in the Terminal's six languages, chosen from Windows' display language with no language dialog. The
; build puts the five translations (pinned and checked) beside this script; without them it doesn't compile, so an
; installer missing a language can't be made by accident.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef SourceDir
  #error Pass /DSourceDir=<the staging folder build_desktop_app.py wrote>
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
AppId={{8F6C2B7E-3D4A-4C1B-9E2F-5A6D7C8B9E01}
AppName=Retis Terminal
AppVersion={#AppVersion}
AppVerName=Retis Terminal {#AppVersion}
AppPublisher=Retis
AppPublisherURL=https://github.com/alvincho/retis-terminal-beta
AppSupportURL=https://github.com/alvincho/retis-terminal-beta/issues
DefaultDirName={localappdata}\Programs\Retis Terminal
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=Retis-Terminal-Setup-{#AppVersion}
SetupIconFile={#SourceDir}\RetisTerminal.ico
UninstallDisplayIcon={app}\RetisTerminal.exe
UninstallDisplayName=Retis Terminal
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no
; Always Windows' display language: 0.1.0-beta installs recorded English, and an update would otherwise keep it.
UsePreviousLanguage=no
#ifdef SignTool
SignTool={#SignTool}
SignedUninstaller=yes
#endif

[Languages]
; Setup takes the language whose LanguageID matches the display language exactly (en-US, zh-TW, zh-CN, ja-JP, es-ES,
; fr-FR), then one of the same language (es-MX, fr-CA, en-GB...). For Chinese that second step also needs the same
; script, so zh-HK and zh-MO get Traditional and zh-SG Simplified. Any other language gets the first entry: English.
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "zh_hant"; MessagesFile: "ChineseTraditional.isl"
Name: "zh_hans"; MessagesFile: "ChineseSimplified.isl"
Name: "ja"; MessagesFile: "Japanese.isl"
Name: "es"; MessagesFile: "Spanish.isl"
Name: "fr"; MessagesFile: "French.isl"

[CustomMessages]
; Workspaces, settings and API keys use the Terminal's own terms (attas/data_terminal_phemar/web/ui-messages.tsv).
; The No button is Windows' own, in its display language.
en.RemoveData=Also remove your Retis Terminal data (workspaces, settings and API keys)?%n%nChoose No to keep it for a later reinstall.
en.OpenNow=Open Retis Terminal
zh_hant.RemoveData=要一併刪除 Retis Terminal 的資料嗎？（工作區、設定與 API 金鑰）%n%n選「否」會保留，重新安裝後可繼續使用。
zh_hant.OpenNow=開啟 Retis Terminal
zh_hans.RemoveData=要一并删除 Retis Terminal 的数据吗？（工作区、设置和 API 密钥）%n%n选择“否”会保留，重新安装后可继续使用。
zh_hans.OpenNow=打开 Retis Terminal
ja.RemoveData=Retis Terminal のデータ（ワークスペース、設定、API キー）も削除しますか？%n%n「いいえ」を選ぶと保持され、再インストール後も引き続き使えます。
ja.OpenNow=Retis Terminal を開く
es.RemoveData=¿Eliminar también sus datos de Retis Terminal (espacios de trabajo, configuración y claves API)?%n%nElija «No» para conservarlos y seguir usándolos si vuelve a instalarlo.
es.OpenNow=Abrir Retis Terminal
fr.RemoveData=Supprimer aussi vos données Retis Terminal (espaces de travail, paramètres et clés API) ?%n%nChoisissez « Non » pour les conserver et les retrouver après une réinstallation.
fr.OpenNow=Ouvrir Retis Terminal

[Messages]
; Replaces Inno Setup's own first question, which asks to "completely remove" the app "and all of its components" and
; reads as if it deletes the user's data too. It removes only the program; RemoveData then asks about the data.
en.ConfirmUninstall=Remove the %1 program?%n%nIf your Retis Terminal data (workspaces, settings and API keys) is on this computer, the next question asks whether to remove it too.
zh_hant.ConfirmUninstall=要移除 %1 程式嗎？%n%n如果這台電腦上有 Retis Terminal 的資料（工作區、設定與 API 金鑰），接著會再問您是否一併刪除。
zh_hans.ConfirmUninstall=要移除 %1 程序吗？%n%n如果这台电脑上有 Retis Terminal 的数据（工作区、设置和 API 密钥），接着会再问您是否一并删除。
ja.ConfirmUninstall=%1 のプログラムを削除しますか？%n%nこのコンピューターに Retis Terminal のデータ（ワークスペース、設定、API キー）がある場合は、続けてデータも削除するかどうかを確認します。
es.ConfirmUninstall=¿Quitar el programa %1?%n%nSi sus datos de Retis Terminal (espacios de trabajo, configuración y claves API) están en este equipo, a continuación se le preguntará si también desea eliminarlos.
fr.ConfirmUninstall=Supprimer le programme %1 ?%n%nSi vos données Retis Terminal (espaces de travail, paramètres et clés API) sont sur cet ordinateur, la question suivante vous demandera si vous voulez aussi les supprimer.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; An update replaces the whole program; the user's data is elsewhere.
Type: filesandordirs; Name: "{app}\kit"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\pip"

[Icons]
Name: "{autoprograms}\Retis Terminal"; Filename: "{app}\RetisTerminal.exe"
Name: "{autodesktop}\Retis Terminal"; Filename: "{app}\RetisTerminal.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RetisTerminal.exe"; Description: "{cm:OpenNow}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; The MAS Manager and the components it runs (Local yfinance) use this app's Python: stop them and remove the
; manager's login item first, so no running program keeps the app's files.
Filename: "{app}\python\python.exe"; Parameters: "-I ""{app}\kit\retis_desktop.py"" manager-stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopManager"
Filename: "{app}\python\python.exe"; Parameters: "-I ""{app}\kit\retis_desktop.py"" stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopTerminal"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM RetisTerminal.exe"; Flags: runhidden waituntilterminated; RunOnceId: "StopTray"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure StopRunning();
var
  Code: Integer;
begin
  { Stop the MAS Manager with the components it runs, a running Terminal and the tray app before their files are
    replaced; the user's data stays. The manager starts its components again when the updated app runs it. An app
    from before the manager answers manager-stop with its usage and goes on. }
  if FileExists(ExpandConstant('{app}\python\python.exe')) then
  begin
    Exec(ExpandConstant('{app}\python\python.exe'), '-I "' + ExpandConstant('{app}\kit\retis_desktop.py') + '" manager-stop',
         '', SW_HIDE, ewWaitUntilTerminated, Code);
    Exec(ExpandConstant('{app}\python\python.exe'), '-I "' + ExpandConstant('{app}\kit\retis_desktop.py') + '" stop',
         '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM RetisTerminal.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunning();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{localappdata}\Retis Terminal');
    if DirExists(Data) then
      if SuppressibleMsgBox(CustomMessage('RemoveData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
