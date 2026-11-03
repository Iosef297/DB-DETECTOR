# Arma el instalador: primero el .exe (build-desktop.ps1) y luego el Setup con Inno Setup 6.
# Uso: .\build-installer.ps1 [-Version 1.0.0]
# Requiere Inno Setup 6:  winget install JRSoftware.InnoSetup --scope user

param([string]$Version = "1.0.0")

Set-Location $PSScriptRoot

& (Join-Path $PSScriptRoot "build-desktop.ps1")
if ($LASTEXITCODE -ne 0) { Write-Output "Fallo el build del .exe - no se arma el instalador."; exit 1 }

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Output "No se encontro Inno Setup 6. Instalalo con: winget install JRSoftware.InnoSetup --scope user"
    exit 1
}

& $iscc "/DAppVersion=$Version" (Join-Path $PSScriptRoot "installer.iss")
if ($LASTEXITCODE -ne 0) { Write-Output "Fallo Inno Setup."; exit 1 }

Write-Output ""
Write-Output "Instalador listo: installer_output\DB-Detector-Orquestador-Setup-$Version.exe"
