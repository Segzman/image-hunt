# Image Hunt installer for Windows (per-user, no admin). Installs in place: put this folder where it should live first.
#   double-click install.cmd           install
#   install.cmd -Uninstall             remove shortcuts + imagehunt:// link (keeps this folder and saved searches)
# AI helpers need ag, which is macOS/Linux only: on Windows search runs scripts-only (no AI), or install inside WSL.
param([switch]$Uninstall)
# No 'Stop': Windows PowerShell 5.1 turns any native stderr (pip notices) into a terminating error. Exit codes are checked.
$Dir = $PSScriptRoot
$Key = 'HKCU:\Software\Classes\imagehunt'
$Links = @("$([Environment]::GetFolderPath('Programs'))\Image Hunt.lnk", "$([Environment]::GetFolderPath('Desktop'))\Image Hunt.lnk")

if ($Uninstall) {
    Remove-Item $Key -Recurse -Force -ErrorAction SilentlyContinue
    $Links | ForEach-Object { Remove-Item $_ -Force -ErrorAction SilentlyContinue }
    Write-Host "Removed shortcuts and the imagehunt:// link. $Dir (and saved\) left alone."
    exit 0
}

# 1. Python 3.9+ and Pillow in a private venv
$Py = $null
foreach ($c in @(@('py', '-3'), @('python'))) {
    try {
        & $c[0] $c[1..9] -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>$null
        if ($LASTEXITCODE -eq 0) { $Py = $c; break }
    } catch {}
}
if (-not $Py) { throw 'Need Python 3.9+: install from https://www.python.org/downloads/ (tick "Add to PATH"), then rerun.' }
$VPy = Join-Path $Dir '.venv\Scripts\python.exe'
$VPyw = Join-Path $Dir '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $VPy)) {
    Write-Host '> Creating .venv'
    & $Py[0] $Py[1..9] -m venv (Join-Path $Dir '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Creating the venv failed.' }
}
& $VPy -c 'import PIL' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '> Installing Pillow'
    & $VPy -m pip install -q --upgrade pip pillow
    if ($LASTEXITCODE -ne 0) { throw 'Installing Pillow failed.' }
}

# 2. imagehunt:// link -> launch.py (bookmark it: opening it starts Image Hunt), plus Start menu + Desktop shortcuts
$Launch = Join-Path $Dir 'launch.py'
$Icon = Join-Path $Dir 'assets\icon.ico'
New-Item "$Key\shell\open\command" -Force | Out-Null
Set-ItemProperty $Key '(default)' 'URL:Image Hunt'
Set-ItemProperty $Key 'URL Protocol' ''
New-Item "$Key\DefaultIcon" -Force | Out-Null
Set-ItemProperty "$Key\DefaultIcon" '(default)' "`"$Icon`""
Set-ItemProperty "$Key\shell\open\command" '(default)' "`"$VPyw`" `"$Launch`" `"%1`""

$Shell = New-Object -ComObject WScript.Shell
foreach ($l in $Links) {
    $s = $Shell.CreateShortcut($l)
    $s.TargetPath = $VPyw
    $s.Arguments = "`"$Launch`""
    $s.WorkingDirectory = $Dir
    $s.IconLocation = $Icon
    $s.Description = 'Image Hunt'
    $s.Save()
}

Write-Host '> Done. Starting Image Hunt...'
& $VPy $Launch
Write-Host @"

  Open it:      Image Hunt in the Start menu / on the Desktop
  Bookmark:     imagehunt://open   <- starts the server if it's off, then opens the page
                (or bookmark http://127.0.0.1:8666/ - only works while it's running)
  AI helpers:   off on Windows (ag is macOS/Linux only); install inside WSL for AI
  Uninstall:    install.cmd -Uninstall
"@
