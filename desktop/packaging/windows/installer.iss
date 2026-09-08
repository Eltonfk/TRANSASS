#define AppName "Transass"
; Keep this value aligned with pyproject.toml, src/subtranslate/_version.py and the Desktop shell.
#define AppVersion "2.5.1"
#define AppExeName "Transass.exe"

[Setup]
AppId={{B13A4F17-4A7C-4A85-9A3C-250000000000}}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Transass
DefaultGroupName=Transass
OutputDir=..\..\..\Output
OutputBaseFilename=Transass-Setup-{#AppVersion}
SetupIconFile="..\..\..\build\transass.ico"
PrivilegesRequired=lowest
Uninstallable=yes
; User state/media are deliberately outside this directory and are preserved.

[Files]
Source: "..\..\..\build\desktop\Transass\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{userprograms}\Transass"; Filename: "{app}\{#AppExeName}"
Name: "{userdesktop}\Transass"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; Flags: unchecked

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
