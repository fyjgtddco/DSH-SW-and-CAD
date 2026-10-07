# DSH 工程模式预设 — 一键安装脚本
#   1. 【问题2 修复】不再注入自定义权限预设（SW单行模式已移除），
#      改为清理历史残留，权限只保留 DSH 标准三档
#   2. 把 dsh-engineering-ui 插件复制进 profile 的 node_modules
#   3. 把插件登记进 profile 的 package.json（dependencies + dsh.profile.bundles）
# 完成后必须【重启 DSH Web】才生效。

$ErrorActionPreference = "Stop"

$PresetRoot = Split-Path $MyInvocation.MyCommand.Path
$PluginSrc  = Join-Path $PresetRoot "plugins\dsh-engineering-ui"
$DshHome    = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE ".dsh" }
$WebProfile = Join-Path $DshHome "profiles\web"
$WebModules = Join-Path $WebProfile "node_modules"
$PatchFile  = Join-Path $WebProfile "cordis.patch.yml"
$ProfilePkg = Join-Path $WebProfile "package.json"
$PluginDst  = Join-Path $WebModules "dsh-engineering-ui"

function Say($msg, $color) { Write-Host $msg -ForegroundColor $color }

Say "================================================" Cyan
Say "  DSH Engineering Preset - Installer" Cyan
Say "================================================" Cyan

if (-not (Test-Path $WebProfile)) {
  Say "[X] Web profile not found: $WebProfile" Red
  Say "    Start DSH Web once so the profile is created, then rerun." Yellow
  exit 1
}
Say "[OK] Web profile: $WebProfile" Green

# 1. host-layer permission preset —— 【问题2 修复】不再注入 SW单行模式
Say "[1/3] Checking host-layer permission presets ..." Yellow
if (Test-Path $PatchFile) {
  $backup = Join-Path $env:USERPROFILE (".dsh\cordis.patch.yml.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
  Copy-Item $PatchFile $backup -Force
  $raw = Get-Content $PatchFile -Raw -Encoding UTF8
  # 【问题2 修复】SW单行模式权限档已被整体移除。
  #   原先的"注入 sw-single-line"逻辑一并删除 —— 权限只保留 DSH 标准三档
  #   （read-only / workspace-write / danger-full-access）。
  #   若历史 patch 里仍残留 sw-single-line，这里顺手清掉，避免它继续出现在权限菜单。
  if ($raw -match "sw-single-line") {
    $cleaned = [regex]::Replace($raw, "(?m)^[ \t]*sw-single-line:\r?\n(?:[ \t]{6,}.*\r?\n)*", "")
    $cleaned = [regex]::Replace($cleaned, "(?m)^[ \t]*#[ ^\r\n]*SW单行模式[^\r\n]*\r?\n", "")
    if ($cleaned -ne $raw) {
      Set-Content -Path $PatchFile -Value $cleaned -Encoding UTF8 -NoNewline
      Say "      removed residual sw-single-line preset (backup: $backup)" Green
    } else {
      Say "      sw-single-line found but could not be removed automatically; please edit manually" Yellow
    }
  } else {
    Say "      no custom permission preset needed (standard three tiers only)" Gray
  }
} else {
  Say "      [X] cordis.patch.yml not found" Red
}

# 2. copy plugin
Say "[2/3] Installing dsh-engineering-ui plugin ..." Yellow
if (-not (Test-Path $PluginSrc)) {
  Say "      [X] plugin source missing: $PluginSrc" Red
  exit 1
}
if (Test-Path $PluginDst) { Remove-Item $PluginDst -Recurse -Force }
New-Item -ItemType Directory -Path $PluginDst -Force | Out-Null
Copy-Item (Join-Path $PluginSrc "*") -Destination $PluginDst -Recurse -Force
Say "      copied to $PluginDst" Green

# 3. register in profile
Say "[3/3] Registering plugin in profile package.json ..." Yellow
$pkg = Get-Content $ProfilePkg -Raw -Encoding UTF8 | ConvertFrom-Json
$deps = [ordered]@{}
foreach ($p in $pkg.dependencies.PSObject.Properties) { $deps[$p.Name] = $p.Value }
$deps["dsh-engineering-ui"] = "file:./node_modules/dsh-engineering-ui"
$bundles = @()
foreach ($b in $pkg.dsh.profile.bundles) { $bundles += $b }
if ($bundles -notcontains "dsh-engineering-ui") {
  $idx = [array]::IndexOf($bundles, "dsh-web-app")
  if ($idx -lt 0) { $bundles += "dsh-engineering-ui" }
  else {
    $before = @($bundles[0..$idx])
    $after = if ($idx + 1 -lt $bundles.Count) { @($bundles[($idx + 1)..($bundles.Count - 1)]) } else { @() }
    $bundles = $before + @("dsh-engineering-ui") + $after
  }
}
$out = [ordered]@{}
$out["name"] = $pkg.name
$out["private"] = $true
if ($pkg.pnpm) { $out["pnpm"] = $pkg.pnpm }
$out["dependencies"] = $deps
$out["dsh"] = [ordered]@{ profile = [ordered]@{ bundles = $bundles } }
Set-Content -Path $ProfilePkg -Value ($out | ConvertTo-Json -Depth 12) -Encoding UTF8
Say "      registered (dependencies + dsh.profile.bundles)" Green

Say "================================================" Cyan
Say "  Done. RESTART DSH Web to apply." Green
Say "================================================" Cyan
