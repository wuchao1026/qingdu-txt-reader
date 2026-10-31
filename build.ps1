# Build 轻读.exe — delegates to build_app.py so the Chinese name survives the console.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = "python"
if ($env:MIMO_PYTHON) { $python = $env:MIMO_PYTHON }

& $python (Join-Path $root "build_app.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$exe = Get-ChildItem (Join-Path $root "dist") -Filter *.exe | Select-Object -First 1
if ($exe) {
  Write-Host "OK -> $($exe.FullName)"
  Write-Host "Size: $([math]::Round($exe.Length / 1MB, 2)) MB"
} else {
  Write-Host "exe not found"
  exit 1
}
