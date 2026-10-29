; Inno Setup script for PyDM. Built by build_installer.bat; expects the
; PyInstaller output in build\dist\PyDM.
;
; Installs per user (no admin prompt) into %LOCALAPPDATA%\Programs\PyDM and
; registers the browser connector under HKCU, like the app does from source.

#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif

[Setup]
AppId={{8C5E2C9A-6B1D-4C61-9F0E-2B7D5A1F3E44}
AppName=PyDM
AppVersion={#AppVersion}
AppVerName=PyDM {#AppVersion}
AppPublisher=PyDM
DefaultDirName={localappdata}\Programs\PyDM
DefaultGroupName=PyDM
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=PyDM-Setup-{#AppVersion}
SetupIconFile=..\pydm\assets\pydm.ico
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\PyDM.exe
UninstallDisplayName=PyDM download manager
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoDescription=PyDM download manager setup

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start PyDM when I sign in to Windows (runs in the notification area)"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\build\dist\PyDM\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\browser_extension\*"; DestDir: "{app}\browser_extension"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; files from an older version that a newer one no longer ships
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\PyDM"; Filename: "{app}\PyDM.exe"; Comment: "PyDM download manager"
Name: "{autodesktop}\PyDM"; Filename: "{app}\PyDM.exe"; Tasks: desktopicon
Name: "{userstartup}\PyDM"; Filename: "{app}\PyDM.exe"; Parameters: "--minimized"; Tasks: autostart

[Run]
Filename: "{app}\PyDM.exe"; Parameters: "--install-browser"; StatusMsg: "Connecting PyDM to your browsers..."; Flags: runhidden waituntilterminated
Filename: "{app}\PyDM.exe"; Description: "Start PyDM now"; Flags: nowait postinstall skipifsilent
Filename: "{app}\browser_extension"; Description: "Open the browser extension folder (to add PyDM to Brave, Chrome or Edge)"; Flags: postinstall shellexec skipifsilent unchecked

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM PyDM.exe"; Flags: runhidden; RunOnceId: "StopPyDM"
Filename: "{app}\PyDM.exe"; Parameters: "--uninstall-browser"; Flags: runhidden waituntilterminated; RunOnceId: "UnregisterBrowser"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\PyDM\native_host"
