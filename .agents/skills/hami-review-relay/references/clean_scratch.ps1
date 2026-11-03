# clean_scratch.ps1 <dir> [<dir> ...]
# Deletes reviewer scratch directories that live under %TEMP%\q, removing every
# link inside them as a link so a link target is never entered.
# Every <dir> must resolve strictly inside %TEMP%\q through real directories; when
# one does not, it prints REFUSED for it and exits 2 before anything is deleted.
# For each <dir> it lists the tree's reparse points: <dir>\.venv and any reparse
# point inside <dir>\bt (pytest's basetemp) are expected and removed as links with
# cmd rmdir (del for a file link); any other reparse point is printed as ANOMALY
# and that <dir> is left as it is (exit 1). Only a tree that rescans with zero
# reparse points reaches the recursive delete. Each <dir> ends on one line:
# DELETED, SKIPPED (not found) or REFUSED, with the reason in parentheses; the exit
# code is the highest across all <dir>s. A deleted or missing <dir> has its lines
# removed from %TEMP%\q\LEDGER.txt, the list run_tests.sh appends each scratch to.

$ErrorActionPreference = 'Stop'

function Say([string]$line) { [Console]::Out.WriteLine($line) }

function Resolve-Dir([string]$path) {
    $full = [IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($path)).TrimEnd('\')
    if (Test-Path -LiteralPath $full) { $full = (Get-Item -LiteralPath $full -Force).FullName.TrimEnd('\') }
    $full
}

function Test-Inside([string]$path, [string]$parent) {
    $path.StartsWith($parent + '\', [StringComparison]::OrdinalIgnoreCase)
}

function Test-Link($item) { [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint) }

function Get-Links([string]$dir) {
    @(Get-ChildItem -LiteralPath $dir -Recurse -Force -Attributes ReparsePoint)
}

function Remove-Link($item) {
    if ($item.PSIsContainer) { cmd.exe /d /c rmdir $item.FullName } else { cmd.exe /d /c del /f /q /a $item.FullName }
}

# The single recursive delete in this script: it rescans first and runs only on
# a tree that holds no reparse point.
function Remove-Tree([string]$dir) {
    $left = Get-Links $dir
    if ($left.Count -gt 0) {
        foreach ($l in $left) { Say "LEFT $($l.FullName)" }
        return "$($left.Count) reparse points remain after link removal"
    }
    Remove-Item -LiteralPath $dir -Recurse -Force
    ''
}

function Remove-LedgerLines([string]$ledger, [string]$dir) {
    if (-not (Test-Path -LiteralPath $ledger)) { return }
    $keep = @([IO.File]::ReadAllLines($ledger) | Where-Object {
        $first = ($_ -split "`t")[0].Trim()
        if (-not $first) { return $true }
        $p = [IO.Path]::GetFullPath($first).TrimEnd('\')
        -not ($p -ieq $dir -or (Test-Inside $p $dir))
    })
    $text = if ($keep.Count) { ($keep -join "`n") + "`n" } else { '' }
    [IO.File]::WriteAllText($ledger, $text, (New-Object Text.UTF8Encoding $false))
}

if ($args.Count -eq 0) { Say 'usage: clean_scratch.ps1 <dir> [<dir> ...]'; exit 2 }
$root = Resolve-Dir (Join-Path $env:TEMP 'q')
$ledger = Join-Path $root 'LEDGER.txt'

$dirs = @()
$outside = $false
foreach ($a in $args) {
    $d = Resolve-Dir ([string]$a)
    $why = ''
    if (-not (Test-Inside $d $root)) {
        $why = "outside $root"
    } else {
        $up = Split-Path -Parent $d
        while ($up -and (Test-Inside $up $root)) {
            if ((Test-Path -LiteralPath $up) -and (Test-Link (Get-Item -LiteralPath $up -Force))) { $why = "under the link $up"; break }
            $up = Split-Path -Parent $up
        }
    }
    if ($why) { Say "REFUSED $d ($why)"; $outside = $true } else { $dirs += $d }
}
if ($outside) { exit 2 }

$code = 0
foreach ($d in $dirs) {
    try {
        if (-not (Test-Path -LiteralPath $d)) {
            Say "SKIPPED $d (not found)"
            Remove-LedgerLines $ledger $d
            continue
        }
        $item = Get-Item -LiteralPath $d -Force
        if (-not $item.PSIsContainer) { Say "REFUSED $d (not a directory)"; $code = [Math]::Max($code, 1); continue }
        if (Test-Link $item) { Say "REFUSED $d (is itself a reparse point)"; $code = [Math]::Max($code, 1); continue }
        $venv = Join-Path $d '.venv'
        $bt = Join-Path $d 'bt'
        $expected = @()
        $anomalies = @()
        foreach ($l in Get-Links $d) {
            $p = $l.FullName.TrimEnd('\')
            if ($p -ieq $venv -or (Test-Inside $p $bt)) { $expected += $l } else { $anomalies += $p }
        }
        if ($anomalies.Count -gt 0) {
            foreach ($p in $anomalies) { Say "ANOMALY $p" }
            Say "REFUSED $d ($($anomalies.Count) unexpected reparse points)"
            $code = [Math]::Max($code, 1)
            continue
        }
        foreach ($l in $expected) { Remove-Link $l }
        $why = Remove-Tree $d
        if ($why) { Say "REFUSED $d ($why)"; $code = [Math]::Max($code, 1); continue }
        Say "DELETED $d"
        Remove-LedgerLines $ledger $d
    } catch {
        Say "REFUSED $d (error: $($_.Exception.Message))"
        $code = [Math]::Max($code, 1)
    }
}
exit $code
