<#
.SYNOPSIS
    Build and publish BadMatch Docker images to Docker Hub.
.DESCRIPTION
    PowerShell equivalent of publish.sh.

    每次发布推**两个**标签：
      :server-<版本> / :client-<版本>   —— 可回溯到某次提交
      :server-latest / :client-latest   —— docker-compose.prod.yml 里实际写的标签
    只推 `<版本>` 的话，生产 `docker compose pull` 永远拉不到新版。版本号参数省略时
    就是 `latest`，这时只推一次、不重复上传。

    推送后从**仓库回读**每个标签的 digest 并打印：push 的进度行和 digest 行不一定在
    同一个输出流上，解析输出容易拿到空值，回读才是"仓库里到底是什么"的真凭实据。
.EXAMPLE
    .\publish.ps1            # 只更新 latest
    .\publish.ps1 ba01a06    # 推 ba01a06 + latest
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

function Get-RemoteDigest {
    param([string]$Ref)
    # 从仓库回读 digest；读不到不算发布失败（镜像已经在仓库里了），但要明确打出来
    $digest = & docker buildx imagetools inspect $Ref --format '{{.Manifest.Digest}}' 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $digest) {
        return '(读取 digest 失败，请手动 docker buildx imagetools inspect 核对)'
    }
    return ($digest | Select-Object -First 1).ToString().Trim()
}

# 版本号本身就是 latest 时只推一次，避免把同一份镜像推两遍
$tags = if ($Version -eq 'latest') { @('latest') } else { @($Version, 'latest') }

# 工作区有未提交改动时，镜像内容 ≠ 版本号所指的提交。只提醒、不阻止：本地调试也要能发。
$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) {
    $dirty = & git -C $PSScriptRoot status --porcelain
    if ($LASTEXITCODE -eq 0 -and $dirty) {
        Write-Warning "工作区有未提交改动，镜像内容与版本号 $Version 并不对应（建议先提交再发布）"
    }
}

Write-Host "=== Building BadMatch v$Version ==="
Invoke-Docker compose build

Write-Host "=== Tagging images ==="
foreach ($tag in $tags) {
    Invoke-Docker tag 'badmatch-server' "${Repo}:server-${tag}"
    Invoke-Docker tag 'badmatch-client' "${Repo}:client-${tag}"
}

Write-Host "=== Pushing to Docker Hub ==="
$published = @()
foreach ($tag in $tags) {
    foreach ($name in 'server', 'client') {
        $ref = "${Repo}:${name}-${tag}"
        Invoke-Docker push $ref
        $published += [pscustomobject]@{ Ref = $ref; Digest = Get-RemoteDigest $ref }
    }
}

Write-Host '=== Done ==='
Write-Host 'Images published:'
foreach ($p in $published) {
    Write-Host "  $($p.Ref)"
    Write-Host "    $($p.Digest)"
}
Write-Host ''
Write-Host '生产上更新：'
Write-Host '  docker compose -f docker-compose.prod.yml pull'
Write-Host '  docker compose -f docker-compose.prod.yml up -d'
