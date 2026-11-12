; Retis Terminal for Windows: a per-user installer that needs no administrator rights.
; scripts/build_desktop_app.py --platform windows compiles it with:
;   iscc /DAppVersion=0.1.0-beta /DSourceDir=<staging folder> /DOutputDir=<dist> [/Sretis="signtool ... $f" /DSignTool=retis]
; The user's data lives in %LOCALAPPDATA%\Retis Terminal, outside the program folder: updates and uninstalling keep
; it unless the user chooses to remove it.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef SourceDir
  #error Pass /DSourceDir=<the staging folder build_desktop_app.py wrote>
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif
#define Chinese FileExists(CompilerPath + "Languages\ChineseTraditional.isl")

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
#ifdef SignTool
SignTool={#SignTool}
SignedUninstaller=yes
#endif

[Languages]
#if Chinese
Name: "zh"; MessagesFile: "compiler:Languages\ChineseTraditional.isl"
#endif
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
#if Chinese
zh.RemoveData=要一併刪除 Retis Terminal 的資料嗎？（工作區、設定與 API 金鑰）%n%n選「否」會保留，重新安裝後可繼續使用。
zh.OpenNow=開啟 Retis Terminal
#endif
en.RemoveData=Also remove your Retis Terminal data (workspaces, settings and API keys)?%n%nChoose No to keep it for a later reinstall.
en.OpenNow=Open Retis Terminal

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; An update replaces the whole program; the user's data is elsewhere.
Type: filesandordirs; Name: "{app}\kit"
Type: filesandordirs; Name: "{app}\python"

[Icons]
Name: "{autoprograms}\Retis Terminal"; Filename: "{app}\RetisTerminal.exe"
Name: "{autodesktop}\Retis Terminal"; Filename: "{app}\RetisTerminal.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RetisTerminal.exe"; Description: "{cm:OpenNow}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\python\python.exe"; Parameters: "-I ""{app}\kit\retis_desktop.py"" stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopTerminal"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM RetisTerminal.exe"; Flags: runhidden waituntilterminated; RunOnceId: "StopTray"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure StopRunning();
var
  Code: Integer;
begin
  { Stop a running Terminal and tray app before their files are replaced; the user's data stays. }
  if FileExists(ExpandConstant('{app}\python\python.exe')) then
    Exec(ExpandConstant('{app}\python\python.exe'), '-I "' + ExpandConstant('{app}\kit\retis_desktop.py') + '" stop',
         '', SW_HIDE, ewWaitUntilTerminated, Code);
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
