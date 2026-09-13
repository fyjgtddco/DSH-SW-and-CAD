# dsH-engineering-sw-single-line

工程模式 SW 单行模式插件。

## 两个半
| 半 | 文件 | 作用 | 加载方式 |
|----|------|------|----------|
| Host | `index.js` | 工程预设激活时，自动把所有活跃会话的权限预设切到 `sw-single-line` | 由 `cordis.patch.yml` 加载 |
| Client | `client.js` | 非工程模式警告横幅 + 三栏布局 | 由 `package.json` 的 `dsh.client` 元数据**自动装配** |

## ⚠️ 安装三铁律（踩过的坑）

### 1. 目录名必须等于包名
```
node_modules/dsH-engineering-sw-single-line/   ← 目录名
package.json: { "name": "dsH-engineering-sw-single-line" }
```
loader 按**包名** `import()`，目录名不一致 → `ERR_MODULE_NOT_FOUND`。

### 2. cordis.patch.yml 只能 insert host 那一条
```yaml
- insert:
    - id: sw-single-line-mode
      name: 'dsH-engineering-sw-single-line'
```
**绝不能**再 insert `@dsH-engineering/sw-single-line-client`。
`client.js` 第一行就是 `window.__ModuleLoader__.load(...)`，没有 import/export，
宿主侧 import 它会在模块求值时访问 `window` → `window is not defined` 崩溃。
client 半的装配由 `dsh.client` 元数据驱动，不需要 patch。

### 3. profile 登记用包名，不用目录名
```json
{
  "dependencies": { "dsH-engineering-sw-single-line": "file:./node_modules/dsH-engineering-sw-single-line" },
  "dsh": { "profile": { "bundles": ["...", "dsH-engineering-sw-single-line"] } }
}
```

## 安装步骤
1. 复制本目录到 `~/.dsh/profiles/web/node_modules/`（**目录名 = 包名**）
2. 在 profile `package.json` 的 `dependencies` 与 `dsh.profile.bundles` 登记包名
3. 重启 DSH Web

## 权限预设
`sw-single-line` 预设由 profile 的 `cordis.patch.yml` 定义：
- sandbox: `danger-full-access`
- approval: `never`
- 名称：SW单行模式
