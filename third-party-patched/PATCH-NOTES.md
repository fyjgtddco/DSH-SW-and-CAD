# third-party-patched — 为 DSH 0.2.0 打过最小补丁的第三方插件

这些目录是从隔离区（`~/.workbuddy/dsh_backup/20260929-214541-full-removal/dot-dsh/profiles/desktop/node_modules/`）
拷出来、并**只改了 `package.json` 里 `peerDependencies` 的版本范围**的副本。
`restore-old-plugins.ps1` 安装时会**优先使用本目录**（日志里标 `[PATCHED]`）。

## 改了什么（逐条）

| 插件 | peer | 改前 | 改后 |
|---|---|---|---|
| `dshmarket` 1.58.0 | `@deepseek-ai/dsh-settings` | `^0.1.0-rc.7 \|\| ^0.1.1-rc.2 \|\| ^0.1.2-alpha.2` | 原值 `+ \|\| ^0.2.0-rc.2` |
| `dsh-codearts-auth` 0.1.0 | `@deepseek-ai/dsh-credentials` | `^0.1.6-alpha.2` | 原值 `+ \|\| ^0.2.0-rc.2` |
| `dsh-codearts-auth` 0.1.0 | `@deepseek-ai/dsh-commands` | `^0.1.6-alpha.2` | 原值 `+ \|\| ^0.2.0-rc.2` |
| `dsh-codearts-auth` 0.1.0 | `@deepseek-ai/dsh-llm` | `^0.1.6-alpha.2` | 原值 `+ \|\| ^0.2.0-rc.2` |

**除这 4 个字符串外，两个包的任何文件都未改动**（源码、`cordis.patch.yml`、client 打包产物全原样）。
原范围分支完整保留，所以它们在 0.1.x 上依旧照常加载 —— 只是多认了 0.2.x。

## 为什么这样就够了（有证据，不是猜）

1. **闸门读的是声明，不是代码。** `app-boot` 只检查 bundle 的 `peerDependencies` 里
   以 `@deepseek-ai/dsh` / `@deepseek-ai/dsh-` 开头的项，是否被「运行时版本」满足；
   不满足就跳过整个 bundle。
2. **把两个包**实际引用**的每一个 `@deepseek-ai/*` 与 0.2.0 的完整包清单做了比对**
   （清单来自 0.2.0 源码树含 `vendor/` 共 345 个名 + 真实组合树里的 188 个 specifier）：
   - `dshmarket`：32 个引用，**全部存在** ✅
   - `dsh-codearts-auth`：4 个引用，**全部存在** ✅
   - 裸依赖 `react` / `js-yaml` / `undici` / `jose` 也都能解析（后三个自带在各自 `node_modules`）。
   详见 `../plugin_patch_plan.md`。
3. **候选写法用 DSH 自带的那份 semver 实测过**：
   - `^0.2.0` → **不匹配** `0.2.0-rc.2`（预发布版永远排在同号正式版之下）
   - `^0.2.0-rc.2` → 匹配，且覆盖整个 `0.2.x` ✅ ← 采用这个
4. 安装后又对**已安装的 `package.json`**逐个复检，8 个插件全 `PASS`。

## 复现 / 重打

```powershell
# 重新生成带补丁的副本（幂等：已打过就只报 keep）
.\patch-incompatible.ps1                 # 默认运行时 0.2.0-rc.2

# 只检查、不写
node .\patch-peer-ranges.mjs <某个 package.json> 0.2.0-rc.2 --check
```

## 如果将来它们在运行时真的报错

闸门只是"声明级"检查，**不等于运行时一定没问题**。若重启 DSH 后某个插件报
`does not provide an export named ...` / `xxx is not a function` 之类：
- 把报错原文和目标文件发我，按 0.1.x→0.2.0 的具体差异做第二处最小修补
  （常见对应关系见仓库里的 `适配0.2.0说明.md` 与 skill `dsh-0.2-plugin-migration`）；
- 想临时停用某个插件：把它从 `~/.dsh/profiles/desktop/package.json` 的
  `dsh.profile.bundles` 里删掉即可（包留在磁盘上，随时加回来）。
