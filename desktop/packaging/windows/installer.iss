#define AppName "Transass"
; Keep this value aligned with pyproject.toml, src/subtranslate/_version.py and the Desktop shell.
#define AppVersion "3.0.1"
#define AppExeName "Transass.exe"

[Setup]
AppId={{B13A4F17-4A7C-4A85-9A3C-250000000000}}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Transass
; Never select the legacy directory, which also contains user data, from
; this AppId's previous registration. Keep AppId continuity for upgrades.
UsePreviousAppDir=no
DefaultGroupName=Transass
OutputDir=..\..\..\Output
OutputBaseFilename=Transass-Setup-{#AppVersion}
SetupIconFile="..\..\..\build\transass.ico"
PrivilegesRequired=lowest
Uninstallable=yes
; User state/media are deliberately outside this directory and are preserved.

[Files]
Source: "..\..\..\build\desktop\Transass\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "data-safe-installer.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Transass"; Filename: "{app}\{#AppExeName}"
Name: "{userdesktop}\Transass"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; Flags: unchecked

; Uninstall only files registered by [Files]. Never recursively delete {app}:
; even a custom install directory may contain files created by the user.

[Code]
function PathsOverlap(const Left, Right: String): Boolean;
var
  L, R: String;
begin
  L := AddBackslash(Lowercase(ExpandFileName(Left)));
  R := AddBackslash(Lowercase(ExpandFileName(Right)));
  Result := (Pos(L, R) = 1) or (Pos(R, L) = 1);
end;

function HasOldUninstaller(const Directory: String): Boolean;
var
  Entry: TFindRec;
begin
  Result := FindFirst(AddBackslash(Directory) + 'unins*.dat', Entry);
  if Result then
    FindClose(Entry)
  else begin
    Result := FindFirst(AddBackslash(Directory) + 'unins*.exe', Entry);
    if Result then
      FindClose(Entry);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Directory, Configured: String;
  Policy: AnsiString;
  Variables: TArrayOfString;
  I: Integer;
begin
  Result := '';
  Directory := ExpandConstant('{app}');
  if PathsOverlap(Directory, ExpandConstant('{localappdata}\Transass')) or
     PathsOverlap(Directory, ExpandConstant('{userappdata}\Transass')) then begin
    Result := 'Escolha uma pasta de programa separada dos dados do Transass.';
    Exit;
  end;
  Variables := ['TRANSASS_DATA_DIR', 'TRANSASS_CONFIG_DIR', 'STATE_DIR',
    'TRANSLATOR_WEB_STATE_DIR', 'MEDIA_ROOT', 'TRANSLATOR_BASE_LIBRARY',
    'ANIME_SUBTITLE_LIBRARY_ROOT', 'TRANSLATOR_FAILURE_LEDGER_ROOT'];
  for I := 0 to GetArrayLength(Variables) - 1 do begin
    Configured := GetEnv(Variables[I]);
    if (Configured <> '') and PathsOverlap(Directory, Configured) then begin
      Result := 'Escolha uma pasta de programa separada de ' + Variables[I] + '.';
      Exit;
    end;
  end;
  if HasOldUninstaller(Directory) then begin
    if not LoadStringFromFile(AddBackslash(Directory) + 'data-safe-installer.txt', Policy) then
      Policy := '';
    if Trim(String(Policy)) <> 'transass-user-data-preserving-installer-v1' then
      Result := 'Esta pasta contém um desinstalador antigo. Escolha uma pasta nova; ' +
        'os arquivos antigos serão preservados. Não execute o desinstalador antigo.';
  end;
end;
