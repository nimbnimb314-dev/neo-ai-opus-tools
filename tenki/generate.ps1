param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$GeneratorArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$micromamba = Join-Path $PSScriptRoot 'tools/micromamba/Library/bin/micromamba.exe'
$envPrefix = Join-Path $PSScriptRoot 'tools/grib-env'
$script = Join-Path $PSScriptRoot 'tools/gridded_generator.py'
$mambaRoot = Join-Path $PSScriptRoot 'tools/mamba-root'
$summaryPath = Join-Path $PSScriptRoot 'data/run-summary.json'
$startedAt = Get-Date

if ($null -eq $GeneratorArgs) {
    $GeneratorArgs = @()
}

$LogDir = Join-Path $PSScriptRoot 'logs'

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$logPath = Join-Path $LogDir ("generate-{0}.log" -f $startedAt.ToString('yyyyMMdd-HHmmss'))

function Write-LogLine {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $line = "[{0}] {1}" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $Message
    $line | Tee-Object -FilePath $logPath -Append
}

foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'GIT_HTTP_PROXY', 'GIT_HTTPS_PROXY')) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}

$command = @()
$executionMode = ''
if ((Test-Path $micromamba) -and (Test-Path $envPrefix)) {
    $env:MAMBA_ROOT_PREFIX = $mambaRoot
    $command = @($micromamba, 'run', '-p', $envPrefix, 'python', $script)
    $executionMode = 'local micromamba environment'
}
else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        throw "Neither the local micromamba environment nor a 'python' command is available."
    }
    $command = @($pythonCommand.Source, $script)
    $executionMode = 'active python environment'
}

Write-LogLine "Starting generate.ps1"
Write-LogLine "Log file: $logPath"
Write-LogLine "Execution mode: $executionMode"
Write-LogLine "Generator args: $([string]::Join(' ', $GeneratorArgs))"

$exitCode = 0
$previousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    & $command[0] $command[1..($command.Length - 1)] @GeneratorArgs 2>&1 |
        ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $_.ToString()
            }
            else {
                "$_"
            }
        } |
        Tee-Object -FilePath $logPath -Append
    if ($null -ne $LASTEXITCODE) {
        $exitCode = $LASTEXITCODE
    }
}
catch {
    $exitCode = 1
    $_ | Out-String | Tee-Object -FilePath $logPath -Append
}
finally {
    $ErrorActionPreference = $previousErrorActionPreference
}

$duration = New-TimeSpan -Start $startedAt -End (Get-Date)
if ($exitCode -eq 0) {
    Write-LogLine "Finished successfully in $($duration.ToString())"
    if (Test-Path $summaryPath) {
        Write-LogLine "Run summary: $summaryPath"
    }
}
else {
    Write-LogLine "Finished with failure in $($duration.ToString()) (exitCode=$exitCode)"
}

exit $exitCode
