Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$micromamba = Join-Path $PSScriptRoot 'tools/micromamba/Library/bin/micromamba.exe'
$envPrefix = Join-Path $PSScriptRoot 'tools/grib-env'
$script = Join-Path $PSScriptRoot 'tools/gridded_generator.py'
$mambaRoot = Join-Path $PSScriptRoot 'tools/mamba-root'

if (-not (Test-Path $micromamba)) {
    throw "micromamba not found: $micromamba"
}

if (-not (Test-Path $envPrefix)) {
    throw "grib environment not found: $envPrefix"
}

$env:MAMBA_ROOT_PREFIX = $mambaRoot
foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'GIT_HTTP_PROXY', 'GIT_HTTPS_PROXY')) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}

& $micromamba run -p $envPrefix python $script @args
