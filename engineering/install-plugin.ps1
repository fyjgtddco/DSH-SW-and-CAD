# DSH 工程模式预设 — 一键安装脚本
#   1. 把 SW单行模式权限预设 patch 进【宿主层】profiles/web/cordis.patch.yml（唯一正确落点）
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

# 1. host-layer permission preset
Say "[1/3] Patching host-layer permission presets ..." Yellow
if (Test-Path $PatchFile) {
  $backup = Join-Path $env:USERPROFILE (".dsh\cordis.patch.yml.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
  Copy-Item $PatchFile $backup -Force
  $raw = Get-Content $PatchFile -Raw -Encoding UTF8
  # 【问题1 修复】判定标准从"是否有 sw-single-line"升级为"是否已有 permission 行"。
  # 原实现若 patch 里已有 permission 行（但没有 sw-single-line），会【再追加一条】
  # 重复的 permission 行 —— cordis 对同一 id 重复 insert 会报错或被后者整体替换，
  # 导致预设表被半截覆盖，进而出现"权限模式被某个值固定"的现象。
  # 现在统一走【就地合并】：找到已有 permission 块 → 若缺 sw-single-line 则注入；
  # 完全找不到 permission 行时才追加一个完整的块。
  $hasPermission = ($raw -match "(?m)^\s*-\s*id:\s*permission\s*$")
  $hasSwSingle   = ($raw -match "sw-single-line")
  if ($hasPermission -and $hasSwSingle) {
    Say "      permission preset already contains sw-single-line, skipping" Gray
  } elseif ($hasPermission -and -not $hasSwSingle) {
    # 就地注入 sw-single-line 到已有 presets: 块之下（保持缩进 6 空格）
    $inject = @"
      # ── SW单行模式（工程模式专用 · 用户手动切换）──────────────────────
      # SW单行模式 = 干 SW/DSH 的活：关沙盒 + 不弹审批，无脑执行
      sw-single-line:
        sandbox: danger-full-access
        approval: never
        name: SW单行模式
        description: 工程模式专用（SW活）：关闭沙盒，SolidWorks 与 DSH 相关文件直接执行不再询问
"@
    # 定位 presets: 行之后插入
    if ($raw -match "(?m)^(\s*)presets:\s*$") {
      $idx = $raw.IndexOf($Matches[0]) + $Matches[0].Length
      $nlch = if ($raw.Contains("`r`n")) { "`r`n" } else { "`n" }
      $new = $raw.Substring(0, $idx) + $nlch + $inject + $raw.Substring($idx)
      Set-Content -Path $PatchFile -Value $new -Encoding UTF8 -NoNewline
      Say "      injected sw-single-line into existing permission block (backup: $backup)" Green
    } else {
      Say "      [X] permission block found but no 'presets:' line; manual fix needed" Red
    }
  } else {
    $block = @"

# SW单行模式权限预设（工程模式专用）
# 宿主层/进程级配置：patch 整体替换 dsh-base 中 permission 行的 config，必须完整重述全部预设。
# 【问题1 修复】此处【不声明 defaultPreset】—— 由设置页(默认权限模式)决定新会话默认值，
#   写死会导致"无论什么场景，新建对话都是同一个权限模式"。
# 修改后必须重启 DSH 才生效。绝对不要写进 .agent-presets/<id>/agent.cordis.yml。
- id: permission
  name: '@deepseek-ai/dsh-permission-presets'
  config:
    presets:
      read-only:
        sandbox: read-only
        approval: ask
        name: 只读
        description: 只能读取，任何修改都需要审批
      workspace-write:
        sandbox: workspace-write
        approval: ask
        name: 工作区可写
        description: 可在工作区内写入，越界需要审批
      danger-full-access:
        sandbox: danger-full-access
        approval: never
        name: Full access
        description: 完全文件访问，不再弹出审批提示
      # 两档工作模式，用户手动切换：
      #   SW单行模式      = 干 SW/DSH 的活：关沙盒 + 不弹审批，无脑执行
      #   workspace-write = 干与 SW 无关的本地杂活：只在本工作区写，越界要审批
      sw-single-line:
        sandbox: danger-full-access
        approval: never
        name: SW单行模式
        description: 工程模式专用（SW活）：关闭沙盒，SolidWorks 与 DSH 相关文件直接执行不再询问
"@
    Add-Content -Path $PatchFile -Value $block -Encoding UTF8
    Say "      appended permission block with sw-single-line (backup: $backup)" Green
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
