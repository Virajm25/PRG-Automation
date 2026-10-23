; Inno Setup 6 script - packages the PyInstaller output as a standard Windows installer.
#define AppName "PRG Drawing Data Extractor"
#define AppVersion "1.0.0"

[Setup]
AppId={{6B1F7C0E-3C54-4C9A-9A55-5D1A2E2B7A10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Straveda Tech
DefaultDirName={autopf}\PRG-Extractor
DefaultGroupName={#AppName}
OutputDir=Output
OutputBaseFilename=PRG-Extractor-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\PRG-Extractor.exe

[Files]
Source: "..\dist\PRG-Extractor\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\documentation\USER_MANUAL.md"; DestDir: "{app}\documentation"; Flags: ignoreversion
Source: "..\documentation\TECHNICAL.md"; DestDir: "{app}\documentation"; Flags: ignoreversion
Source: "..\documentation\TRAINING.md"; DestDir: "{app}\documentation"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\PRG-Extractor.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\PRG-Extractor.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\PRG-Extractor.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
; Project data lives in %LOCALAPPDATA%\PRG-Extractor and is kept on uninstall.
