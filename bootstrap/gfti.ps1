#!/usr/bin/env pwsh
# gfti — the PowerShell entry point for tier C (Windows).
#
# The POSIX shim (bootstrap/gfti) cannot run natively on Windows, so this file is
# its twin: same job, same exit codes, same CLI. It resolves its own real path
# through any symlink/junction chain, finds a Python 3.11+, and hands over. It is
# deliberately thin — every behaviour lives in python/gfti/cli.py, and the A-6
# gate asserts the two shims render the same configuration.
#
#   pwsh -File bootstrap/gfti.ps1 doctor --json
#   pwsh -File bootstrap/gfti.ps1 tier
#
# Exit codes: 0 ok · 1 failed · 2 environment · 3 not implemented.

$ErrorActionPreference = 'Stop'

$path = $PSCommandPath
if (-not $path) { $path = $MyInvocation.MyCommand.Path }

# Follow symlinks and junctions. Get-Item .Target can be a string array; take the
# first entry and resolve a relative target against the link's own directory.
for ($i = 0; $i -lt 10; $i++) {
    $item = Get-Item -LiteralPath $path -Force
    if (-not $item.LinkType) { break }
    $target = $item.Target
    if ($target -is [array]) { $target = $target[0] }
    if (-not [System.IO.Path]::IsPathRooted($target)) {
        $target = Join-Path (Split-Path -Parent $path) $target
    }
    $path = $target
}

$repo = Split-Path -Parent (Split-Path -Parent $path)
$cli  = Join-Path $repo 'python/gfti/cli.py'

if (-not (Test-Path -LiteralPath $cli)) {
    [Console]::Error.WriteLine("gfti: cannot find $cli — is this a checkout?")
    exit 2
}

$exe    = $null
$prefix = @()
foreach ($candidate in @('python3', 'python')) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) { $exe = $found.Source; break }
}
if (-not $exe) {
    # The Windows py launcher is the one interpreter that is reliably on PATH.
    $found = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($found) { $exe = $found.Source; $prefix = @('-3') }
}
if (-not $exe) {
    [Console]::Error.WriteLine("gfti: no Python 3.11+ on PATH — install it, then re-run.")
    exit 2
}

& $exe @prefix $cli @args
exit $LASTEXITCODE
