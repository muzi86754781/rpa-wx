#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{BA64E0ED-14BF-43F7-9E39-BE05101A1033}
AppName=WeChat Agent
AppVersion={#AppVersion}
AppPublisher=WeChat Agent contributors
AppPublisherURL=https://github.com/muzi86754781/rpa-wx
DefaultDirName={localappdata}\Programs\RpaWx
DefaultGroupName=WeChat Agent
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\release
OutputBaseFilename=RpaWx-v{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\RpaWx.exe

[Files]
Source: "..\dist\RpaWx\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\WeChat Agent"; Filename: "{app}\RpaWx.exe"
Name: "{autodesktop}\WeChat Agent"; Filename: "{app}\RpaWx.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\RpaWx.exe"; Description: "Launch WeChat Agent"; Flags: nowait postinstall skipifsilent
