<#
.SYNOPSIS
    Build and publish BadMatch Docker images to Docker Hub.
.DESCRIPTION
    PowerShell equivalent of publish.sh.
.EXAMPLE
    .\publish.ps1
    .\publish.ps1 1.2.0
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Version = 'latest'
)

$ErrorActionPreference = 'Stop'
$Repo = 'hsiangleev/badmatch'

function Invoke-Docker {
    param(
        [Parameter(Mandatory, ValueFromRemainingArguments)]
        [string[]]$Arguments
    )

    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') exited with code $LASTEXITCODE"
    }
}

Write-Host "=== Building BadMatch v$Version ==="
Invoke-Docker compose build

Write-Host "=== Tagging images ==="
Invoke-Docker tag 'badmatch-server' "${Repo}:server-${Version}"
Invoke-Docker tag 'badmatch-client' "${Repo}:client-${Version}"

Write-Host "=== Pushing to Docker Hub ==="
Invoke-Docker push "${Repo}:server-${Version}"
Invoke-Docker push "${Repo}:client-${Version}"

Write-Host "=== Done ==="
Write-Host 'Images published:'
Write-Host "  ${Repo}:server-${Version}"
Write-Host "  ${Repo}:client-${Version}"
