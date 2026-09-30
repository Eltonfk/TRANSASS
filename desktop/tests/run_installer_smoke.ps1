# Native installer proof for a disposable GitHub Actions Windows profile.
# Fake legacy uninstallers/logs are evidence only and are never executed.
param(
    [Parameter(Mandatory = $true)]
    [string]$Installer
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ($env:OS -ne "Windows_NT" -or $env:CI -ne "true" -or -not $env:RUNNER_TEMP) {
    throw "Execute este smoke somente em um perfil Windows descartável de CI."
}
$installerFile = (Get-Item -LiteralPath $Installer).FullName
$localData = [Environment]::GetFolderPath("LocalApplicationData")
$dataDir = Join-Path $localData "Transass"
$installDir = Join-Path $localData "Programs\Transass"
$evidenceDir = Join-Path $env:RUNNER_TEMP ("transass-installer-smoke-" + [Guid]::NewGuid().ToString())
$customDir = Join-Path $evidenceDir "custom app with user data"
$manifest = Join-Path $PSScriptRoot "..\packaging\windows\installer.iss"
$appIdLine = Get-Content -LiteralPath $manifest | Where-Object { $_ -match '^AppId=(.+)$' }
if (@($appIdLine).Count -ne 1) { throw "AppId do manifesto não é único." }
# Inno escapes the opening brace using {{. Preserve the exact existing ID.
$appId = ($appIdLine -replace '^AppId=', '').Replace('{{', '{')
$registration = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\${appId}_is1"
foreach ($path in @($dataDir, $installDir, $registration)) {
    if (Test-Path -LiteralPath $path) {
        throw "Perfil de CI não está vazio: $path. Nenhum dado existente será alterado."
    }
}
New-Item -ItemType Directory -Path $evidenceDir | Out-Null
$sentinels = @{}
function Add-Sentinel([string]$Path, [string]$Content) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    [IO.File]::WriteAllText($Path, $Content)
    $sentinels[$Path] = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}
function Assert-Sentinels {
    foreach ($path in $sentinels.Keys) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Sentinela removida: $path"
        }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $sentinels[$path]) {
            throw "Sentinela alterada: $path"
        }
    }
}
function Invoke-Setup([string]$Executable, [string]$LogName, [string[]]$ExtraArguments = @()) {
    $logPath = Join-Path $evidenceDir ($LogName + ".log")
    $arguments = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/LOG=`"$logPath`"")
    $process = Start-Process -FilePath $Executable -ArgumentList ($arguments + $ExtraArguments) -Wait -PassThru
    return $process.ExitCode
}
function Assert-SafeUninstall([string]$Directory, [string]$LogName) {
    $uninstallers = @(Get-ChildItem -LiteralPath $Directory -Filter "unins*.exe" -File)
    if ($uninstallers.Count -ne 1) { throw "Desinstalador seguro não é único: $Directory" }
    if (Invoke-Setup $uninstallers[0].FullName $LogName) {
        throw "Desinstalação falhou: $Directory"
    }
    if (Test-Path -LiteralPath (Join-Path $Directory "Transass.exe")) {
        throw "Executável permaneceu após desinstalação: $Directory"
    }
    Assert-Sentinels
}

# Default legacy install: data and application historically shared this root.
Add-Sentinel (Join-Path $dataDir "state\jobs.json") 'legacy-history'
Add-Sentinel (Join-Path $dataDir "state\anime-subtitle-library\episode.pt-BR.ass") 'legacy-library'
Add-Sentinel (Join-Path $dataDir "media\episode.mkv") 'legacy-media'
Add-Sentinel (Join-Path $dataDir "Transass.exe") 'fake-legacy-executable-never-run'
Add-Sentinel (Join-Path $dataDir "unins000.exe") 'fake-legacy-uninstaller-never-run'
Add-Sentinel (Join-Path $dataDir "unins000.dat") 'fake-legacy-log-with-broad-delete-rule'
New-Item -Path $registration -Force | Out-Null
New-ItemProperty -Path $registration -Name "DisplayName" -Value "Transass legacy fixture" -PropertyType String | Out-Null
New-ItemProperty -Path $registration -Name "DisplayVersion" -Value "2.5.2" -PropertyType String | Out-Null
New-ItemProperty -Path $registration -Name "InstallLocation" -Value $dataDir -PropertyType String | Out-Null
New-ItemProperty -Path $registration -Name "Inno Setup: App Path" -Value $dataDir -PropertyType String | Out-Null
New-ItemProperty -Path $registration -Name "UninstallString" -Value ('"' + (Join-Path $dataDir "unins000.exe") + '"') -PropertyType String | Out-Null

if ((Invoke-Setup $installerFile "reject-default-legacy" @("/DIR=`"$dataDir`"")) -eq 0) {
    throw "Instalador aceitou reutilizar o diretório de dados legado."
}
Assert-Sentinels
# No /DIR: exercise the actual Windows default and previous AppId registration.
if (Invoke-Setup $installerFile "default-install") { throw "Instalação padrão falhou." }
if (-not (Test-Path -LiteralPath (Join-Path $installDir "Transass.exe") -PathType Leaf)) {
    throw "Instalador reutilizou o caminho legado em vez de Programs\Transass."
}
$registeredPath = (Get-ItemProperty -LiteralPath $registration)."Inno Setup: App Path"
if ($registeredPath.TrimEnd('\') -ne $installDir.TrimEnd('\')) {
    throw "Registro AppId não aponta para a instalação nova: $registeredPath"
}
Assert-Sentinels
Add-Sentinel (Join-Path $installDir "state\user-history.json") 'data-inside-application-directory'
Add-Sentinel (Join-Path $installDir "media\user-video.mkv") 'media-inside-application-directory'
if (Invoke-Setup $installerFile "safe-reinstall") { throw "Reinstalação segura falhou." }
Assert-Sentinels
python (Join-Path $PSScriptRoot "run_bundle_smoke.py") (Join-Path $installDir "Transass.exe")
if ($LASTEXITCODE -ne 0) { throw "Smoke do executável instalado falhou." }
Assert-SafeUninstall $installDir "default-uninstall"

# A custom legacy directory must also fail before its old log can be reused.
$customLegacy = Join-Path $evidenceDir "custom legacy app"
Add-Sentinel (Join-Path $customLegacy "unins000.dat") 'custom-legacy-uninstall-log'
Add-Sentinel (Join-Path $customLegacy "unins000.exe") 'custom-legacy-uninstaller-never-run'
Add-Sentinel (Join-Path $customLegacy "state\history.json") 'custom-legacy-history'
if ((Invoke-Setup $installerFile "reject-custom-legacy" @("/DIR=`"$customLegacy`"")) -eq 0) {
    throw "Instalador aceitou um desinstalador legado em pasta personalizada."
}
Assert-Sentinels

# User-created files in an otherwise usable custom installation survive too.
Add-Sentinel (Join-Path $customDir "state\history.json") 'custom-user-history'
Add-Sentinel (Join-Path $customDir "media\video.mkv") 'custom-user-media'
Add-Sentinel (Join-Path $customDir "user-notes.txt") 'custom-user-notes'
if (Invoke-Setup $installerFile "custom-install" @("/DIR=`"$customDir`"")) {
    throw "Instalação personalizada falhou."
}
Assert-SafeUninstall $customDir "custom-uninstall"
Write-Output "INSTALLER_SMOKE_OK default,legacy-registration,legacy-guard,reinstall,custom,sentinels"
Write-Output "Evidências preservadas em $evidenceDir"
