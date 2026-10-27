; Windows installer for DDP Streamer (Inno Setup 6). build.py runs it after the
; PyInstaller build:  ISCC /DAppVersion=1.0.0 windows\installer.iss
; Installs for the current user only (no admin, no UAC prompt) to
; %LOCALAPPDATA%\Programs\DDP Streamer. Settings in %APPDATA%\DDP Streamer stay
; on update and uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "DDP Streamer"
#define AppExe "DDP Streamer.exe"

[Setup]
; Never change AppId: Windows uses it to find the installed copy on updates
AppId={{77A779C2-E42C-4F99-BD3B-42CD7AC103E6}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Bob Kojima
AppCopyright=Copyright (C) Bob Kojima, GPL-3.0
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\bong69.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
; Close a running copy before replacing its files
CloseApplications=yes
OutputDir=..\dist
OutputBaseFilename=DDP-Streamer-setup

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; An update replaces the whole program folder: files left from an older version could clash
Source: "..\dist\DDP Streamer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; "Start with Windows" (set by the app): remove it on uninstall, never create it here
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#AppName}"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
