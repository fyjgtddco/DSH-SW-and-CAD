# DSH_SW 工具目录

本目录包含 DSH_SW 桥接脚本，用于通过 Python 驱动 SolidWorks。

## 快速开始

1. 安装依赖：`pip install pywin32 mss Pillow`
2. 确保 SolidWorks 已安装并启动过一次
3. 自检：`python sw_bridge.py doctor`
4. 使用：`python sw_bridge.py run <script.py>`

## 常用命令

| 命令 | 说明 |
|------|------|
| `python sw_bridge.py doctor` | 环境自检 |
| `python sw_bridge.py status` | 连接状态 |
| `python sw_bridge.py run <script.py>` | 执行建模脚本 |
| `python sw_bridge.py show` | 窗口前台+截图 |
| `python sw_bridge.py save <path.sldprt>` | 另存为 |

## AI 调用方式

AI 通过 DSH 的 `pwsh` 工具调用：
```powershell
python "<包目录>\sw_bridge.py" run "<脚本路径>"
```

AI 生成的建模脚本需调用 swapi.py 的高层 API（见 solidworks-modeling.md）。