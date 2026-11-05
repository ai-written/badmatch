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
    param(
        [string]$Ref,
        [int]$Attempts = 3,
        [int]$DelaySeconds = 3
    )

    # 从仓库回读 digest 只是「核对用」：读不到**不代表发布失败**（镜像已经在仓库里，
    # push 自己也会回一行 digest）。所以重试几次，仍失败就返回占位文案、绝不 throw
    # —— 真实踩过：push 全部成功，只因这一步网络抖动脚本以 1 退出，看起来像发布失败。
    for ($i = 1; $i -le $Attempts; $i++) {
        $digest = & docker buildx imagetools inspect $Ref --format '{{.Manifest.Digest}}' 2>$null
        if ($LASTEXITCODE -eq 0 -and $digest) {
            return ($digest | Select-Object -First 1).ToString().Trim()
        }
        if ($i -lt $Attempts) { Start-Sleep -Seconds $DelaySeconds }
    }
    return '(读取 digest 失败，请手动 docker buildx imagetools inspect 核对)'
}

# 推镜像会偶发网络抖动（实测见过 HEAD blobs 直接 EOF、push 退 1）。
# 重试很便宜：已经上传过的层会命中 "Layer already exists"，不会重传。
function Invoke-DockerRetry {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [int]$Attempts = 3,
        [int]$DelaySeconds = 5
    )

    for ($i = 1; $i -le $Attempts; $i++) {
        & docker @Arguments
        $code = $LASTEXITCODE
        if ($code -eq 0) { return }
        if ($i -lt $Attempts) {
            Write-Warning "docker $($Arguments -join ' ') 第 $i 次失败（exit $code），${DelaySeconds}s 后重试"
            Start-Sleep -Seconds $DelaySeconds
        } else {
            throw "docker $($Arguments -join ' ') exited with code $code（已重试 $Attempts 次）"
        }
    }
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
        Write-Host "  -> $ref"
        Invoke-DockerRetry -Arguments @('push', $ref)
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

# 明确以 0 退出：digest 回读失败会把 $LASTEXITCODE 留成 1，不重置的话
# 会出现「四个标签都推成功了、脚本却报失败」的假警报（踩过）。
# 真正的失败（build/push）在 Invoke-Docker / Invoke-DockerRetry 里 throw，仍以 1 退出。
exit 0
