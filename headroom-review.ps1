$ErrorActionPreference = 'Stop'
$taskPython = $env:AGENTOPS_PYTHON
if (-not $taskPython) {
    $taskLocalPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $taskLocalPython) { $taskPython = $taskLocalPython }
    else { $taskPython = 'python' }
}
Push-Location -LiteralPath $PSScriptRoot
try { & $taskPython -m agentops @args; $taskExitCode = $LASTEXITCODE }
finally { Pop-Location }
exit $taskExitCode
