$ErrorActionPreference = "Stop"

$LocalPython = Join-Path $PSScriptRoot ".venv_user\Scripts\python.exe"

if (Test-Path $LocalPython) {
    $Python = $LocalPython
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = "python"
} else {
    throw "Python not found. Install Python 3.10+ and add python to PATH, or create .venv_user in the project root."
}

$PyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "key_mouse_marco_weaver",
    "key_mouse_marco_weaver.py"
)

& $Python -m PyInstaller @PyInstallerArgs

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code: $LASTEXITCODE"
}

Write-Host "Built: dist\key_mouse_marco_weaver.exe"
