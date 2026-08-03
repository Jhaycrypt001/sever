# Loads the repo .env into the current process, then runs the rest of the
# command line. The Rust backend reads .env itself (dotenvy); the Python
# processes do not, so they need this.
#
# Usage:
#   .\scripts\dev-env.ps1 uv run celery -A aiagent.celery_app worker --pool=solo
#
# Values are never echoed.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Command)

$envPath = Join-Path $PSScriptRoot '..\.env'
if (-not (Test-Path $envPath)) {
    Write-Error "No .env at $envPath. Copy .env.example and fill in KEEPERHUB_API_KEY."
    exit 1
}

# A variable already set in the shell wins over the file, which is how dotenvy
# behaves on the Rust side. That is what makes a one-off override work:
#   $env:AGENT_PROVIDERS='fake'; .\scripts\dev-env.ps1 uv run celery ...
Get-Content $envPath | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
        $i = $line.IndexOf('=')
        $name = $line.Substring(0, $i).Trim()
        $value = $line.Substring($i + 1).Trim()
        if ($name -and -not [Environment]::GetEnvironmentVariable($name, 'Process')) {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
}

# Running on the host rather than the compose network.
$env:REDIS_URL = 'redis://localhost:6379/0'
$env:AGENT_API_URL = 'http://127.0.0.1:8001'
$env:BACKEND_INTERNAL_URL = 'http://127.0.0.1:8000'

& $Command[0] @($Command[1..($Command.Length - 1)])
