SDRduo Display -- Installer
============================

For end users
-------------
Run setup.exe. That's it -- it's a single self-contained file (the app
source is embedded inside it), so it's all you need to copy to another
computer.

It will:
  1. Check for Python 3 (offers to install it via winget if missing).
  2. Copy the app to an install folder (defaults to
     %LOCALAPPDATA%\SDRduoDisplay -- no administrator rights needed).
  3. Create a virtual environment and install dependencies
     (fastapi, uvicorn, numpy, scipy).
  4. Create Desktop and Start Menu shortcuts.

One thing it deliberately does NOT do: install the SDRplay API driver.
That's SDRplay's own vendor USB driver for the RSPduo hardware, not
something this installer bundles or redistributes. Get it from
https://www.sdrplay.com/software/ (the "SDRplay API" package, v3.15 or
newer) before first run.

For rebuilding setup.exe (after changing the app)
---------------------------------------------------
1. Update payload\ to match the current backend\/static\/run.py/
   requirements.txt/README.md/CHANGELOG.md in the project root (there's
   no automatic sync -- copy the changed files across by hand).
2. Run build.ps1 (`powershell -File build.ps1`). Requires the ps2exe
   module; it will offer to install it via PowerShellGet/NuGet if it's
   not already present.

Files in this folder
---------------------
  setup.exe             The deliverable -- ship just this file.
  payload\               App source bundled into setup.exe. Keep in sync
                          with the project root when it changes.
  setup_template.ps1     Installer source/logic. Edit this, not the
                          generated files below, then run build.ps1.
  build.ps1              Rebuilds setup_standalone.ps1 + setup.exe from
                          payload\ + setup_template.ps1.
  setup_standalone.ps1   Generated (setup_template.ps1 with payload\
                          embedded as base64) -- don't hand-edit.
  payload.zip            Generated intermediate (payload\, zipped, before
                          base64 embedding) -- safe to delete, build.ps1
                          recreates it.
