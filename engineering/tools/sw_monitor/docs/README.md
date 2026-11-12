# DSH SolidWorks 外部监控器

这是一个独立运行的本机服务。DeepSeek 接入后，服务分配 SW 使用权，结束对应的 DSH 等待任务，维护逐零件看板，并用现有 DSH 验签逻辑核实建模结果。

| 文件 | 用途 |
|---|---|
| monitor.py | HTTP 服务、排队和命令许可、逐零件状态、房间验收协调、SQLite 持久化 |
| dsh_evidence.py | 复用 DSH 三大防线及台账审核，读取 BOM，提供未来仿真验证接口 |
| host-adapter.mjs | DSH 宿主调用服务和注册可唤醒后台等待任务的参考适配器 |
| DeepSeek接入教程.md | 接入顺序、入口、数据、示例和切换检查 |
| 覆盖验收.md | 去重场景矩阵及 99% 的真实任务验收规则 |
| selfcheck.py、selfcheck-host.mjs | 独立本地验收，使用临时目录和模拟 DSH job 服务 |

## 启动独立服务

在桌面交付文件夹打开 PowerShell。运行需 Python 3.10 或以上，无需安装 Python 第三方包。参考宿主适配器需要 Node.js 20.3 或更新版本提供的 fetch 和 AbortSignal.any。

```powershell
# 把 D:\CAD交付 换成真实任务交付目录的上一级，目录应已存在
python -B .\monitor.py --data .\运行数据 --workspace-root 'D:\CAD交付'
```

首次启动生成 `运行数据\host.token` 与 SQLite 文件。默认监听 `127.0.0.1:18779`。停止前等 DSH 当前 SW 命令和队列收尾。持锁期间监控进程重启会隔离这把锁，需要按教程核实进程、文档和后台任务已清空后恢复。

上述启动方式可检查服务及协议。要使用真实的 DSH 证据核实，在同一个命令上增加 `--dsh-tools '<DSH 本次实际加载的 tools 绝对路径>'`，并让 DeepSeek 按教程完成宿主接入。示例路径均需以运行时为准。

## 本地检查

```powershell
$env:PYTHONUTF8 = '1'
python -B .\selfcheck.py
```

自检使用 Python 3.11+ 和 Node.js 20.3+。需要在实际 DSH 中接入后运行《覆盖验收》的真机场景，才能验收客户端唤醒、插件实际签名和 SolidWorks 独占行为。
