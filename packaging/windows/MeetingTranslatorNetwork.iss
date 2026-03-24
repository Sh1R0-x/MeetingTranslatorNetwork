; Inno Setup script for MeetingTranslatorNetwork V1

#define MyAppName "MeetingTranslatorNetwork"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "MeetingTranslatorNetwork"
#define MyAppExeName "MeetingTranslatorNetwork.exe"
#ifndef MyOutputDir
  #define MyOutputDir "artifacts\\windows\\installer"
#endif
#ifndef MyDistDir
  #define MyDistDir "artifacts\\windows\\dist\\MeetingTranslatorNetwork"
#endif
#ifndef MySetupIconFile
  #define MySetupIconFile ""
#endif
#ifndef MyWizardImageFile
  #define MyWizardImageFile ""
#endif
#ifndef MyWizardSmallImageFile
  #define MyWizardSmallImageFile ""
#endif

[Setup]
AppId={{6EA4C1D1-5758-4CA2-A6A3-9A84C5BA3C9A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir={#MyOutputDir}
OutputBaseFilename=MeetingTranslatorNetwork-Setup-v1
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#if MySetupIconFile != ""
SetupIconFile={#MySetupIconFile}
#endif
#if MyWizardImageFile != ""
WizardImageFile={#MyWizardImageFile}
#endif
#if MyWizardSmallImageFile != ""
WizardSmallImageFile={#MyWizardSmallImageFile}
#endif

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis:"

[Files]
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer {#MyAppName}"; Flags: nowait postinstall skipifsilent
