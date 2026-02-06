; Inno Setup script for MeetingTranslatorNetwork V1

#define MyAppName "MeetingTranslatorNetwork"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "MeetingTranslatorNetwork"
#define MyAppExeName "MeetingTranslatorNetwork.exe"

[Setup]
AppId={{6EA4C1D1-5758-4CA2-A6A3-9A84C5BA3C9A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=dist
OutputBaseFilename=MeetingTranslatorNetwork-Setup-v1
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis:"

[Files]
Source: "dist\MeetingTranslatorNetwork\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer {#MyAppName}"; Flags: nowait postinstall skipifsilent
