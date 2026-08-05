#Requires -Version 5.1
<#
  SDRduo Display -- standalone installer.
  Self-contained: the app source is embedded below as a zip, so this
  single .exe is everything needed to install on another computer.
#>
# NOT "Stop": external tools (python, pip, winget) writing anything to
# stderr -- even a harmless warning, like venv's "moved due to redirects
# or junctions" notice under AppData -- would otherwise be treated as a
# terminating error and abort the whole install. Failures from those are
# instead checked explicitly via $LASTEXITCODE below.
$ErrorActionPreference = "Continue"

function Write-Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

# ps2exe's compiled Read-Host throws rather than returning an empty string
# when stdin isn't a real interactive console (e.g. launched from certain
# automated contexts) -- fall back to the given default instead of crashing
# the whole install over what should just mean "accept the default".
function Read-HostSafe($prompt, $default) {
    try {
        $val = Read-Host $prompt
        if ([string]::IsNullOrWhiteSpace($val)) { return $default }
        return $val
    } catch {
        return $default
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  SDRduo Display -- Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------
# 1. Extract the embedded payload to a temp folder.
# ---------------------------------------------------------------------
Write-Step "Extracting application files"
$payloadB64 = "__PAYLOAD_B64__"
$tempZip = Join-Path $env:TEMP ("sdrduo_display_payload_" + [guid]::NewGuid().ToString("N") + ".zip")
$tempExtract = Join-Path $env:TEMP ("sdrduo_display_payload_" + [guid]::NewGuid().ToString("N"))
[System.IO.File]::WriteAllBytes($tempZip, [Convert]::FromBase64String($payloadB64))
Expand-Archive -LiteralPath $tempZip -DestinationPath $tempExtract -Force
Remove-Item -LiteralPath $tempZip -Force
$payloadDir = $tempExtract
Write-Ok "Extracted to a temp folder"

# ---------------------------------------------------------------------
# 2. Choose an install location. Defaults to the current user's
#    LocalAppData so no administrator rights are needed.
# ---------------------------------------------------------------------
$defaultTarget = Join-Path $env:LOCALAPPDATA "SDRduoDisplay"
Write-Step "Install location"
$targetDir = Read-HostSafe "Install to [$defaultTarget] (press Enter to accept)" $defaultTarget
Write-Ok "Installing to: $targetDir"

# ---------------------------------------------------------------------
# 3. Find (or install) Python 3.
# ---------------------------------------------------------------------
Write-Step "Checking for Python 3"

function Get-PythonCommand {
    foreach ($cand in @("py", "python", "python3")) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if ($cmd) {
            try {
                $verOut = & $cand --version 2>&1
                if ($verOut -match "Python 3\.") {
                    if ($cand -eq "py") { return @($cand, "-3") }
                    return @($cand)
                }
            } catch {}
        }
    }
    return $null
}

$pythonCmd = Get-PythonCommand

if (-not $pythonCmd) {
    Write-Warn2 "Python 3 not found."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        $answer = Read-HostSafe "Install Python 3 now via winget? [Y/n]" "Y"
        if ($answer -match "^[Yy]") {
            Write-Step "Installing Python 3 via winget (this can take a few minutes)"
            winget install --id Python.Python.3.13 -e --accept-source-agreements --accept-package-agreements
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
            $pythonCmd = Get-PythonCommand
        }
    }
    if (-not $pythonCmd) {
        Write-Host ""
        Write-Host "ERROR: Python 3 is required and could not be found or installed automatically." -ForegroundColor Red
        Write-Host "Install it from https://www.python.org/downloads/ (check 'Add python.exe to PATH')"
        Write-Host "then re-run this setup."
        Read-HostSafe "Press Enter to exit" "" | Out-Null
        exit 1
    }
}
$pyVersion = & $pythonCmd[0] $pythonCmd[1..($pythonCmd.Length-1)] --version
Write-Ok "Found $pyVersion"

# ---------------------------------------------------------------------
# 4. Copy the app files into place.
# ---------------------------------------------------------------------
Write-Step "Copying application files"
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
Get-ChildItem -LiteralPath $payloadDir | Copy-Item -Destination $targetDir -Recurse -Force
Write-Ok "Copied to $targetDir"
Remove-Item -LiteralPath $tempExtract -Recurse -Force -ErrorAction SilentlyContinue

# ---------------------------------------------------------------------
# 5. Create the virtual environment and install dependencies.
# ---------------------------------------------------------------------
Write-Step "Creating virtual environment"
$venvDir = Join-Path $targetDir "venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvPip = Join-Path $venvDir "Scripts\pip.exe"

if (Test-Path $venvPython) {
    Write-Ok "venv already exists, reusing it"
} else {
    & $pythonCmd[0] $pythonCmd[1..($pythonCmd.Length-1)] -m venv $venvDir
    # venv itself can print a harmless warning (e.g. "moved due to
    # redirects or junctions" under AppData) and still exit 0 having
    # actually succeeded -- the real check is whether it produced a
    # working interpreter, not the exit code alone.
    if (-not (Test-Path $venvPython)) {
        Write-Host ""
        Write-Host "ERROR: venv creation did not produce $venvPython" -ForegroundColor Red
        Read-HostSafe "Press Enter to exit" "" | Out-Null
        exit 1
    }
    Write-Ok "Created $venvDir"
}

Write-Step "Installing dependencies (fastapi, uvicorn, numpy, scipy)"
& $venvPython -m pip install --upgrade pip --quiet
& $venvPip install -r (Join-Path $targetDir "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: pip install failed (exit code $LASTEXITCODE) -- see the output above." -ForegroundColor Red
    Read-HostSafe "Press Enter to exit" "" | Out-Null
    exit 1
}
Write-Ok "Dependencies installed"

# ---------------------------------------------------------------------
# 6. Write a launch script.
# ---------------------------------------------------------------------
$launchBat = Join-Path $targetDir "launch.bat"
$launchBatContent = "@echo off`r`nsetlocal`r`ncd /d ""%~dp0""`r`nvenv\Scripts\python.exe run.py`r`n"
Set-Content -LiteralPath $launchBat -Value $launchBatContent -Encoding ASCII -NoNewline
Write-Ok "Wrote $launchBat"

# ---------------------------------------------------------------------
# 7. Desktop + Start Menu shortcuts.
# ---------------------------------------------------------------------
Write-Step "Creating shortcuts"
try {
    $wsh = New-Object -ComObject WScript.Shell

    $desktopLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "SDRduo Display.lnk"
    $s = $wsh.CreateShortcut($desktopLnk)
    $s.TargetPath = $launchBat
    $s.WorkingDirectory = $targetDir
    $s.IconLocation = "shell32.dll,43"
    $s.Description = "SDRduo Display -- browser SDR receiver for the SDRplay RSPduo"
    $s.Save()
    Write-Ok "Desktop shortcut created"

    $startMenuDir = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"
    $startLnk = Join-Path $startMenuDir "SDRduo Display.lnk"
    $s2 = $wsh.CreateShortcut($startLnk)
    $s2.TargetPath = $launchBat
    $s2.WorkingDirectory = $targetDir
    $s2.IconLocation = "shell32.dll,43"
    $s2.Description = "SDRduo Display -- browser SDR receiver for the SDRplay RSPduo"
    $s2.Save()
    Write-Ok "Start Menu shortcut created"
} catch {
    Write-Warn2 ("Could not create shortcuts (" + $_.Exception.Message + ") -- you can still run " + $launchBat + " directly.")
}

# ---------------------------------------------------------------------
# 8. Done -- SDRplay driver reminder.
# ---------------------------------------------------------------------
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Install complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "IMPORTANT -- one more thing this installer does NOT do:" -ForegroundColor Yellow
Write-Host "  SDRduo Display needs the SDRplay API driver installed separately." -ForegroundColor Yellow
Write-Host "  That is the vendor's own USB driver for your RSPduo, not something" -ForegroundColor Yellow
Write-Host "  this installer bundles or redistributes." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Download and run the installer from:"
Write-Host "    https://www.sdrplay.com/software/" -ForegroundColor White
Write-Host "  (get the 'SDRplay API' package for Windows, matching v3.15 or newer.)"
Write-Host ""
Write-Host "Once that is installed, launch SDRduo Display from the Desktop shortcut,"
Write-Host "the Start Menu, or by running:"
Write-Host ("    " + $launchBat)
Write-Host ""

$launchNow = Read-HostSafe "Launch SDRduo Display now? [y/N]" "N"
if ($launchNow -match "^[Yy]") {
    Start-Process -FilePath $launchBat -WorkingDirectory $targetDir
}

Read-HostSafe "Press Enter to close" "" | Out-Null
