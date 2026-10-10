<#
  Builds Install\setup.exe from Install\payload\ + Install\setup_template.ps1.

  Run this whenever payload\ (the app source, kept in sync with the
  project's backend\/static\ manually) or setup_template.ps1 changes.
  Requires the ps2exe module (Install-Module ps2exe -Scope CurrentUser).

  What it does:
    1. Zips payload\, base64-encodes it.
    2. Substitutes that blob into setup_template.ps1's __PAYLOAD_B64__
       placeholder, writing setup_standalone.ps1 (a generated file --
       don't hand-edit it; edit setup_template.ps1 instead and rebuild).
    3. Compiles setup_standalone.ps1 into setup.exe via ps2exe -- a real,
       standalone, self-contained executable. Nothing else needs to ship
       alongside it.
#>
$ErrorActionPreference = "Continue"
$installDir = $PSScriptRoot

Write-Host "Zipping payload..."
$zipPath = Join-Path $installDir "payload.zip"
if (Test-Path $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Compress-Archive -Path (Join-Path $installDir "payload\*") -DestinationPath $zipPath -CompressionLevel Optimal

$bytes = [System.IO.File]::ReadAllBytes($zipPath)
$b64 = [Convert]::ToBase64String($bytes)
Write-Host "  payload.zip: $($bytes.Length) bytes -> base64: $($b64.Length) chars"

Write-Host "Assembling setup_standalone.ps1..."
$template = Get-Content -LiteralPath (Join-Path $installDir "setup_template.ps1") -Raw
$standalone = $template.Replace('__PAYLOAD_B64__', $b64)
$standalonePath = Join-Path $installDir "setup_standalone.ps1"
Set-Content -LiteralPath $standalonePath -Value $standalone -Encoding UTF8 -NoNewline

Write-Host "Compiling setup.exe..."
if (-not (Get-Module -ListAvailable -Name ps2exe)) {
    Write-Host "ps2exe module not found -- installing (requires internet access)..."
    Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -ForceBootstrap -Scope CurrentUser | Out-Null
    Install-Module -Name ps2exe -Scope CurrentUser -Force -AllowClobber
}
Import-Module ps2exe
$exePath = Join-Path $installDir "setup.exe"
Invoke-ps2exe -inputFile $standalonePath -outputFile $exePath `
    -title "SDRduo Display Setup" -description "Installs SDRduo Display" `
    -company "VK3EI" -version "0.20.0.0" -requireAdmin:$false -noConsole:$false

if (Test-Path $exePath) {
    $size = (Get-Item $exePath).Length
    Write-Host "Built: $exePath ($size bytes)" -ForegroundColor Green
} else {
    Write-Host "ps2exe did not produce setup.exe -- check the output above." -ForegroundColor Red
    exit 1
}
