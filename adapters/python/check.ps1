[CmdletBinding()]
param(
    [switch]$Projects,
    [switch]$Tests
)

$ErrorActionPreference = "Stop"
$pythonRoot = $PSScriptRoot
$workspaceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$env:UV_CACHE_DIR = Join-Path $workspaceRoot ".tooling\uv-cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $workspaceRoot ".tooling\python"
Push-Location $pythonRoot
try {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv is required. Install it from https://docs.astral.sh/uv/ and rerun this command."
    }

    $validateArgs = @("run", "--locked", "python", "scripts/validate_workspace.py")
    if ($Projects) {
        $validateArgs += "--projects"
    }
    & uv @validateArgs
    if ($LASTEXITCODE -ne 0) {
        throw "workspace validation failed with exit code $LASTEXITCODE"
    }

    if ($Tests) {
        & uv run --locked pytest --basetemp .tooling/pytest-tmp
        if ($LASTEXITCODE -ne 0) {
            throw "workspace tooling tests failed with exit code $LASTEXITCODE"
        }
    }
}
finally {
    Pop-Location
}
