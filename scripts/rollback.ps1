#Requires -Version 7.0
<#
.SYNOPSIS
    PyKnora 版本回滚：把 api / worker / web 三个服务切回指定的镜像 tag。

.DESCRIPTION
    只回滚**应用层**。数据库迁移是"只追加"的（AGENTS.md I4），没有 down 脚本，
    所以回滚的正确顺序是「先确认旧版本代码能读新库，再切镜像」，而不是把库一起退回去。

    脚本做四件事：
      1. 预检：compose 定义是否可用、目标 tag 的镜像是否存在、当前 tag 是什么；
      2. 记录：当前镜像 ID / 容器 ID / .env 的哈希写到 .tmp_ref/rollback-<ts>.json
         （回滚失败时这是唯一能证明"之前是什么"的东西）；
      3. 切换：PYKNORA_IMAGE_TAG=<tag> docker compose up -d --no-deps --force-recreate；
      4. 验证：等 healthy，探 /health 与 /openapi.json，打印结果后退出（非零=回滚未成功）。

    不做的事：不动数据库卷、不跑 down -v、不改迁移、不删备份。

.PARAMETER ToImageTag
    目标镜像 tag，例如 v1.2.0 或 sha-abc1234。必须是已经构建/拉取到本机的 tag。

.PARAMETER Services
    要回滚的服务，默认 api,worker,web。只回滚 web 时传 web。

.PARAMETER DryRun
    只打印将要执行的命令与当前状态，不做任何变更。

.EXAMPLE
    pwsh -File scripts/rollback.ps1 -ToImageTag v1.2.0

.EXAMPLE
    pwsh -File scripts/rollback.ps1 -ToImageTag v1.2.0 -Services web -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ToImageTag,
    [string[]]$Services = @('api', 'worker', 'web'),
    [string]$ImagePrefix = 'pyknora',
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
$exitCode = 0
try {
    Write-Host "== PyKnora 回滚 → tag=$ToImageTag services=$($Services -join ',') dryRun=$($DryRun.IsPresent)" -ForegroundColor Cyan

    # ---------------------------------------------------------------- 0. 目标状态可解析
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw "docker compose config 失败：编排定义本身有问题，先修它再谈回滚" }

    # ---------------------------------------------------------------- 1. 预检：镜像在不在
    # worker 与 api 共用同一个镜像（见 docker-compose.yml），所以不能简单拼 "$prefix-$svc"。
    $imageFor = @{ api = "$ImagePrefix-api"; worker = "$ImagePrefix-api"; web = "$ImagePrefix-web" }
    $missing = @()
    foreach ($svc in $Services) {
        if (-not $imageFor.ContainsKey($svc)) { throw "未知服务：$svc（支持 api / worker / web）" }
        $img = $imageFor[$svc] + ":$ToImageTag"
        docker image inspect $img *> $null
        if ($LASTEXITCODE -ne 0) { $missing += $img }
    }
    $missing = @($missing | Select-Object -Unique)
    if ($missing.Count -gt 0) {
        throw "目标镜像不存在：$($missing -join ', ')。先 docker pull / docker build 出这些 tag 再回滚；本脚本不替你下载。"
    }

    # ---------------------------------------------------------------- 2. 记录当前现场
    $snapshotDir = Join-Path $repoRoot '.tmp_ref'
    New-Item -ItemType Directory -Force -Path $snapshotDir | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $snapshotPath = Join-Path $snapshotDir "rollback-$stamp.json"

    $snapshot = [ordered]@{
        started_at  = (Get-Date).ToString('o')
        target_tag  = $ToImageTag
        services    = $Services
        containers  = @()
        images      = @()
        env_sha256  = $null
    }
    foreach ($svc in $Services) {
        $cid = (docker compose ps -q $svc 2>$null | Select-Object -First 1)
        if ($cid) {
            $imgId = (docker inspect --format '{{.Image}}' $cid 2>$null)
            $imgRef = (docker inspect --format '{{.Config.Image}}' $cid 2>$null)
            $snapshot.containers += [ordered]@{ service = $svc; container_id = $cid.Trim(); image_id = $imgId.Trim(); image_ref = $imgRef.Trim() }
        } else {
            $snapshot.containers += [ordered]@{ service = $svc; container_id = $null; image_id = $null; image_ref = $null }
        }
    }
    $snapshot.images = @(docker image ls --format '{{.Repository}}:{{.Tag}} {{.ID}}' | Where-Object { $_ -match "^$([regex]::Escape($ImagePrefix))-" })
    if (Test-Path (Join-Path $repoRoot '.env')) {
        $snapshot.env_sha256 = (Get-FileHash (Join-Path $repoRoot '.env') -Algorithm SHA256).Hash
    }
    $snapshot | ConvertTo-Json -Depth 6 | Set-Content -Path $snapshotPath -Encoding utf8
    Write-Host "现场已记录：$snapshotPath" -ForegroundColor DarkGray

    # ---------------------------------------------------------------- 3. 切换
    $env:PYKNORA_IMAGE_TAG = $ToImageTag
    $svcArg = $Services -join ' '
    $cmd = "docker compose up -d --no-deps --force-recreate $svcArg"
    Write-Host "将执行：$cmd   (PYKNORA_IMAGE_TAG=$ToImageTag)" -ForegroundColor Yellow

    if ($DryRun) {
        Write-Host "DryRun：未做任何变更。加 -ToImageTag 去掉 -DryRun 才会真的切。" -ForegroundColor Yellow
        return
    }

    Invoke-Expression $cmd
    if ($LASTEXITCODE -ne 0) { throw "docker compose up 失败，回滚未完成；现场在 $snapshotPath" }

    # ---------------------------------------------------------------- 4. 验证
    Write-Host "等待容器进入 healthy（最多 120s）..." -ForegroundColor Cyan
    $deadline = (Get-Date).AddSeconds(120)
    $allHealthy = $false
    while ((Get-Date) -lt $deadline) {
        $states = @(docker compose ps --format '{{.Service}} {{.Health}}' 2>$null | Where-Object { $_ -match ($Services -join '|') })
        $bad = @($states | Where-Object { $_ -notmatch 'healthy' })
        if ($bad.Count -eq 0 -and $states.Count -ge $Services.Count) { $allHealthy = $true; break }
        Start-Sleep -Seconds 3
    }

    $apiPort = if ($env:PYKNORA_API_PORT) { $env:PYKNORA_API_PORT } else { '8010' }
    $healthOk = $false
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$apiPort/health" -TimeoutSec 5 -UseBasicParsing
        $healthOk = $resp.StatusCode -eq 200
        Write-Host "/health → $($resp.StatusCode) $($resp.Content)" -ForegroundColor Green
    } catch {
        Write-Host "/health 探活失败：$($_.Exception.Message)" -ForegroundColor Red
    }

    $openapiOk = $false
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$apiPort/openapi.json" -TimeoutSec 5 -UseBasicParsing
        $openapiOk = $resp.StatusCode -eq 200
    } catch { }

    Write-Host ""
    Write-Host "== 结果" -ForegroundColor Cyan
    Write-Host "  containers healthy : $allHealthy"
    Write-Host "  /health 200        : $healthOk"
    Write-Host "  /openapi.json 200  : $openapiOk"
    Write-Host ""
    Write-Host "别忘了三件事（脚本不替你判断）：" -ForegroundColor Yellow
    Write-Host "  1) 数据库迁移只向前：本次若已跑过新迁移，旧代码必须能读新库（先验证再切）"
    Write-Host "  2) 索引版本指针：SELECT id, status, activated_at FROM index_builds ORDER BY activated_at DESC LIMIT 5;"
    Write-Host '     需要「回到旧索引」时改的是 documents.active_index_version_id，不是删 build'
    Write-Host "  3) .env 里被改动过的上游地址/模型：快照里有 env_sha256，与当前不一致就先把旧值还原"

    if (-not ($allHealthy -and $healthOk)) { $exitCode = 1 }
} catch {
    Write-Host "回滚中止：$($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
} finally {
    Pop-Location
}
exit $exitCode
