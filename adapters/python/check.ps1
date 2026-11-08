[CmdletBinding()]
param(
    [switch]$Projects,
    [switch]$Tests
)

# The location rule of docs/ATC_HOME.md § Resolving the locations, in shell: uv needs these values before any
# Python runs. atc_testkit/locations.py is the same rule; tests/test_check_ps1.py compares the two.

$ErrorActionPreference = "Stop"
$pythonRoot = $PSScriptRoot
$onWindows = $env:OS -eq "Windows_NT"
$nameBound = 40
$unsuitable = "ATC_TMP_UNSUITABLE"

function Stop-Unsuitable([string]$Detail) {
    [Console]::Error.WriteLine("$unsuitable $Detail")
    exit 9
}

function Get-NotARealDirectory([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) { return $null }
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { return "is a link: $Path" }
    if (-not $item.PSIsContainer) { return "is not a directory: $Path" }
    return $null
}

function Use-FinalPathType {
    if (-not ("AtcFinalPath" -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class AtcFinalPath {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern uint GetFinalPathNameByHandleW(IntPtr handle, StringBuilder path, uint size, uint flags);
    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr handle);
    public static bool Opens(string path) {
        IntPtr handle = CreateFileW(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero);
        if (handle == new IntPtr(-1)) return false;
        CloseHandle(handle);
        return true;
    }
    public static string Of(string path) {
        IntPtr handle = CreateFileW(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero);
        if (handle == new IntPtr(-1)) return path;
        try {
            StringBuilder text = new StringBuilder(32768);
            return GetFinalPathNameByHandleW(handle, text, 32768, 0) == 0 ? path : text.ToString();
        } finally {
            CloseHandle(handle);
        }
    }
}
'@
    }
}

# Whether the path opens with every link on it followed.
function Test-Opens([string]$Path) {
    if (-not $onWindows) {
        & realpath -e -- $Path *> $null
        return $LASTEXITCODE -eq 0
    }
    Use-FinalPathType
    return [AtcFinalPath]::Opens($Path)
}

function Get-FinalPath([string]$Path) {
    if (-not $onWindows) { return (& realpath -- $Path) }
    Use-FinalPathType
    $final = [AtcFinalPath]::Of($Path)
    if ($final.StartsWith("\\?\UNC\")) { return "\\" + $final.Substring(8) }
    if ($final.StartsWith("\\?\")) { return $final.Substring(4) }
    return $final
}

# The absolute form with links resolved and 8.3 names expanded; the part that does not exist yet is kept as written.
function Get-LongName([string]$Path) {
    $existing = [System.IO.Path]::GetFullPath($Path).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
    $rest = @()
    while (-not (Test-Path -LiteralPath $existing)) {
        $parent = [System.IO.Path]::GetDirectoryName($existing)
        if ([string]::IsNullOrEmpty($parent)) { break }
        $rest = @([System.IO.Path]::GetFileName($existing)) + $rest
        $existing = $parent
    }
    $long = Get-FinalPath $existing
    foreach ($part in $rest) { $long = [System.IO.Path]::Combine($long, $part) }
    return $long
}

function Test-Below([string]$Path, [string]$Root) {
    $separator = [System.IO.Path]::DirectorySeparatorChar
    $path_, $root_ = $Path.TrimEnd($separator), $Root.TrimEnd($separator)
    if ($onWindows) { return ($path_ -ieq $root_) -or $path_.StartsWith("$root_$separator", [StringComparison]::OrdinalIgnoreCase) }
    return ($path_ -ceq $root_) -or $path_.StartsWith("$root_$separator", [StringComparison]::Ordinal)
}

# The nearest existing ancestor of the path when it is a link whose target is missing.
function Get-DanglingAncestor([string]$Path) {
    $current = [System.IO.Path]::GetFullPath($Path).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
    while (-not [string]::IsNullOrEmpty($current)) {
        $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
        if ($null -ne $item) {
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -and -not (Test-Opens $current)) {
                return $current
            }
            return $null
        }
        $current = [System.IO.Path]::GetDirectoryName($current)
    }
    return $null
}

function Get-BrokenProperty([string]$Root) {
    $notReal = Get-NotARealDirectory $Root
    if ($notReal) { return $notReal }
    $dangling = Get-DanglingAncestor $Root
    if ($dangling) { return "has an ancestor that is a link to a missing target: $dangling" }
    $long = Get-LongName $Root
    $parts = $long -split '[\\/]'
    $insideAppData = $parts | Where-Object { $_ -ieq "appdata" }
    foreach ($value in @($env:APPDATA, $env:LOCALAPPDATA)) {
        if ($value -and (Test-Below $long $value)) { $insideAppData = $true }
    }
    if ($insideAppData) { return "lies inside AppData: $long" }
    $ancestor = $long
    while (-not [string]::IsNullOrEmpty($ancestor)) {
        if (Test-Path -LiteralPath ([System.IO.Path]::Combine($ancestor, ".git"))) {
            return "has an ancestor that holds .git: $ancestor"
        }
        $ancestor = [System.IO.Path]::GetDirectoryName($ancestor)
    }
    if ($long.Length -gt $nameBound) { return "its long name has $($long.Length) characters, limit ${nameBound}: $long" }
    return $null
}

function Test-Absolute([string]$Path) {
    if ($onWindows) { return $Path -match '^([A-Za-z]:[\\/]|[\\/]{2})' }
    return $Path.StartsWith("/")
}

function Enter-TemporaryDirectory([string]$AtcHome) {
    if ($env:CI) { return }
    $value = $env:ATC_TMP
    if ($value -and -not (Test-Absolute $value)) { Stop-Unsuitable "ATC_TMP is not absolute: $value" }
    $root = if ($value) { $value } else { Join-Path $AtcHome "tmp" }
    $broken = Get-BrokenProperty $root
    if ($broken) { Stop-Unsuitable $broken }
    $long = Get-LongName $root
    $systmp = Join-Path $long "systmp"
    foreach ($directory in @($systmp, (Join-Path $long "candidates"))) {
        $notReal = Get-NotARealDirectory $directory
        if ($notReal) { Stop-Unsuitable $notReal }
    }
    try {
        New-Item -ItemType Directory -Force -Path $systmp | Out-Null
    } catch {
        Stop-Unsuitable "cannot be created: ${long}: $($_.Exception.Message)"
    }
    $env:TEMP = $systmp
    $env:TMP = $systmp
    $env:TMPDIR = $systmp
    $env:ATC_TMP = $long
}

$userHome = if ($onWindows) { $env:USERPROFILE } else { $env:HOME }
if (-not $userHome) { $userHome = $HOME }
$atcHome = Join-Path $userHome ".atc"
Enter-TemporaryDirectory $atcHome
if (-not $env:UV_CACHE_DIR) { $env:UV_CACHE_DIR = Join-Path $atcHome (Join-Path "cache" "uv") }
if (-not $env:UV_PYTHON_INSTALL_DIR) { $env:UV_PYTHON_INSTALL_DIR = Join-Path $atcHome (Join-Path "cache" "python") }

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
        & uv run --locked pytest
        if ($LASTEXITCODE -ne 0) {
            throw "workspace tooling tests failed with exit code $LASTEXITCODE"
        }
    }
}
finally {
    Pop-Location
}
