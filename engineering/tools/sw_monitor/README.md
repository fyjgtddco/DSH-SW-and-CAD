# DSH SolidWorks 外部监控器 · COM 门卫版

由外部服务为 DSH 分配 SW 使用权。门卫按 FIFO 排队，只允许队首提交受支持的建模操作。实际 COM 对象留在门卫启动的独立进程里。每件零件在保存、核对文件并确认关档后才提交到零件看板，随后交接锁。

- `monitor.py` 管队列、权限、状态和交接。
- `com_gate.py` 负责白名单建模、实例检查、保存、关档和超时回收。
- `host-adapter.mjs` 让 DSH 等待锁、建模结果和房间验收。
- `dsh_evidence.py` 接三大防线、台账、BOM 和未来仿真。
- `DeepSeek接入教程.md` 是接入手册，`覆盖验收.md` 是验收表。

## 两侧交付

根目录是监控器。子目录 `工程模式接入包` 是 DeepSeek 负责合入的工程模式文件和补丁。两边共用工程侧 tools 中的页面清单。正式启动前先按《DeepSeek接入教程》完成工程模式文件部署；现有旧 DSH 建模入口须由 DeepSeek 迁移到 COM 门卫后再切换。

新增接口包括 `page-status`、`audit` 和按当前修订核对脏页的恢复。工程侧提供四档摘要、磁盘检查点、宿主 trace 与离线重放。

## Windows 启动

使用安装了 pywin32 的 DSH Python。修改三个示例路径后，在桌面交付目录执行。宿主适配器需要 Node.js 20.3+。服务必须运行在可以启动和连接 SolidWorks 的 Windows 交互式用户会话。

```powershell
python -B .\monitor.py --data .\运行数据 --workspace-root 'D:\CAD交付' --dsh-tools 'C:\实际加载的DSH\tools' --solidworks-exe 'C:\实际安装目录\SLDWORKS.exe' --com-timeout 2700
```

默认本机监听 `127.0.0.1:18779`。首次启动生成宿主令牌和 SQLite 文件。开始前把手工 SolidWorks 文档正常保存并退出已有实例。由门卫启动并独占本次 SolidWorks，不能同时在该实例手工操作。未接入的 DSH 工具或同权限脚本仍可直接调用 Windows COM，接入时需封住这些入口，见教程。

默认 2700 秒只针对**提交后实际运行的作业**，包含冷启动。算坐标、模型思考和 FIFO 排队不计时。超时回收门卫持有的进程句柄，确认退出后放行下一票，下一作业按需启动新实例。超时件不会被验收。关档释放该零件占用的文档资源，SolidWorks 仍可能保留缓存，不能据此保证 Windows 显示的进程内存立即下降。

本版白名单支持基础零件特征和直齿/斜齿轮。装配、出图、任意宏、直接 COM 和其他未登记的操作暂不能通过这一版门卫。原来的工程验收代码仍随包交付。

## 本地自检

```powershell
$env:PYTHONUTF8 = '1'
python -B .\selfcheck.py
python -B .\selfcheck-com.py
```

自检使用 Python 3.11+、Node.js 20.3+。真实 DSH、真实 SW 保存关档、模态弹窗回收和覆盖率需按验收表验证。
