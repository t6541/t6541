$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$bundledPython = "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$python312 = if (Get-Command py -ErrorAction SilentlyContinue) { (& py -3.12 -c "import sys; print(sys.executable)").Trim() } else { "" }
$python = if ($python312 -and (Test-Path $python312)) { $python312 } elseif (Test-Path $bundledPython) { $bundledPython } else { throw "Python 3.12 not found" }
Push-Location $projectRoot
try {
    $dependencyPaths = @((Join-Path $projectRoot "src"))
    foreach ($candidate in @(".deps", ".build-deps")) {
        $path = Join-Path $projectRoot $candidate
        if (Test-Path $path) { $dependencyPaths = @($path) + $dependencyPaths; break }
    }
    $env:PYTHONPATH = ($dependencyPaths -join ";")
    & $python -m PyInstaller --noconfirm --clean quantbot_v096.spec
} finally {
    Pop-Location
}
