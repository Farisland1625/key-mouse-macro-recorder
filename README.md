# KeyMouse Macro Recorder

![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D4.svg)
[![License](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)
[![Windows CI](https://github.com/Farisland1625/key-mouse-macro-recorder/actions/workflows/ci.yml/badge.svg)](https://github.com/Farisland1625/key-mouse-macro-recorder/actions/workflows/ci.yml)

一个轻量、可审阅、注重时序准确性的 Windows 键鼠宏录制与回放工具。

KeyMouse Macro Recorder 把一次操作保存为可编辑的 JSON 时间轴：你可以录制键盘、鼠标移动、按钮和滚轮，逐步修改事件，再按原始时间间隔回放。项目坚持 Python 标准库 + Tkinter + Windows `ctypes` 的小依赖路线，发布版 exe 约 11 MB。

> 项目目前是 Windows-only 的个人工具开源基线。它优先解决“录得准、看得懂、停得住”，而不是成为一个包含 OCR、图像识别和脚本引擎的通用 RPA 平台。

> English: A small, local-first Windows macro recorder with an editable JSON timeline, source-timestamped low-level hooks, and recovery-focused playback. The project is intentionally Windows-only and dependency-light.

## 为什么做这个项目

相较云端/在线自动化工具，它不要求账号或服务，录制内容留在本机；相较闭源桌面宏工具，源码、事件格式和校验逻辑都可以审阅；相较脚本型开源框架，它提供直接可用的桌面时间轴，不需要先编写脚本。

下面是产品定位对比，不是对特定项目的性能基准测试：

| 维度 | KeyMouse Macro Recorder | 常见闭源桌面宏工具 | 通用开源自动化框架 |
| --- | --- | --- | --- |
| 数据路径 | 本地 JSON，无联网服务 | 通常本地，但实现不可审阅 | 取决于项目和部署方式 |
| 可审计性 | MIT 许可，单文件源码，schema 2 | 只能使用发行版 | 源码可见，但常以脚本/API 为中心 |
| 输入层级 | Windows 低级 Hook、VK + 扫描码、源时间戳 | 实现和精度各不相同 | 常需要用户自行组合输入库 |
| 时序编辑 | 可视时间轴、绝对时间、间隔保留、撤销/重做 | 常见功能较少或不可编辑 | 通常要改脚本 |
| 回放恢复 | 暂停/继续、停止、释放重试、失败提示 | 行为依实现而异 | 需要调用方自行处理 |
| 多宏工作流 | 多文件按序编排、每项独立次数 | 不一定提供 | 往往需要额外代码 |
| 部署负担 | 源码运行只需 Python/Tkinter，exe 约 11 MB | 依赖安装器或常驻组件 | 依赖和配置通常更多 |

## 核心能力

- 高精度录制：键盘和鼠标 Hook 在同一消息线程中处理，并使用 Windows Hook 源时间戳，减少跨线程排序误差。
- 可靠键盘回放：保存虚拟键码、扫描码和扩展键状态；优先使用扫描码发送物理按键。
- 鼠标轨迹与多显示器：移动事件按约 8 ms / 2 px 采样；保存虚拟桌面边界，在不同 DPI 或屏幕布局下映射坐标并校验可达性。
- 可读时间轴：事件类型、动作、按键名称、坐标、滚轮方向和侧键编号都可直接查看和编辑。
- 可控回放：速度 0.01x-20x、指定次数或直到停止；F9 在播放中切换暂停/继续，F10 立即停止。
- 输入安全收尾：结束、停止或异常时，仍处于按下状态的键和鼠标按钮最多重试 3 次释放；失败会保留状态并提示用户。
- 轻量编辑：插入、复制、上移、下移、删除、删除全部鼠标移动、单步回放，以及 Ctrl+Z/Ctrl+Y（各保留最近 20 步）。
- 多文件编排：按顺序加入多个 schema 2 宏，为每项设置次数并生成新宏；源文件不会被覆盖。
- 稳定文件格式：严格校验有限时间、事件类型、编码和坐标；schema 2 JSON 采用临时文件 + flush/fsync + 原子替换保存。

## 快速开始

### 直接使用发布版

从 GitHub Releases 下载 `KeyMouseMacroRecorder.exe`，双击运行即可。发布版不要求安装 Python；正式 Release 会在仓库发布后提供。

### 从源码运行

Windows 10/11 + Python 3.10 或更高版本：

```powershell
python macro_recorder.py
```

Tkinter 通常随 Python 一起安装。程序只调用 Windows 原生 API，不需要额外的运行时包。

### 第一次录制

1. 按 `F8` 开始录制，再按一次 `F8` 停止。
2. 点击“保存”写入 JSON；示例格式见 [`examples/basic_click.json`](examples/basic_click.json)。
3. 载入宏后选择速度和重复模式，按 `F9` 回放。
4. 回放中按 `F9` 暂停/继续，按 `F10` 停止录制或回放。

常用编辑快捷键：`Ctrl+Z` 撤销，`Ctrl+Y` 重做。属性栏中的“时间（秒）”是时间轴绝对时间；相邻事件之间的间隔就是回放等待，不需要额外的等待事件。

## 构建 exe

运行时不依赖 PyInstaller。若要生成单文件 Windows 可执行程序：

```powershell
python -m venv .venv_user
.\.venv_user\Scripts\python.exe -m pip install -r requirements-dev.txt
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

输出为 `dist\KeyMouseMacroRecorder.exe`。`build/`、`dist/` 和虚拟环境已被 `.gitignore` 排除，建议通过 GitHub Releases 分发二进制，而不是把构建产物提交到源码历史。

## 文件格式与隐私

宏文件是 `schema_version: 2` 的 JSON，事件只包含 `key` 和 `mouse` 两类。每个事件的 `t` 是从宏开始计算的绝对秒数；鼠标事件还可能包含 `x`、`y`、`message` 和 `data`，键盘事件包含 `vk`、`scan`、`action` 和 `extended`。

宏可能包含完整的键盘输入、屏幕坐标和操作时序。不要录制密码、验证码、令牌、私钥或其它敏感信息，也不要把个人宏文件上传到 Issue、Pull Request 或公开仓库。仓库中的 `macros/` 默认被整体忽略；`examples/` 只放脱敏的最小示例。

## 安全边界与已知取舍

- 回放会接管当前鼠标和键盘；启动前切换到目标窗口，并确认程序与目标应用具有相同权限。
- 这是可见窗口的本地工具，不隐藏运行、不联网、不上传录制内容。
- 仅支持 Windows，不计划在首轮引入跨平台输入抽象。
- 不包含 OCR、图像/像素识别、条件分支、脚本解释器或云同步。
- `F8`、`F9`、`F10` 是保留的全局控制键；请避免把它们作为需要回放的普通控制流程。

## 开发与验证

```powershell
.\.venv_user\Scripts\python.exe -m unittest discover -s tests -v
.\.venv_user\Scripts\python.exe -m py_compile macro_recorder.py tests\test_macro_format.py
```

当前基线包含 41 项核心逻辑测试，并已验证 PyInstaller onefile 构建。欢迎通过 Issue 提交可复现的问题、精度案例和 UI 改进建议；提交代码前请阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md) 和 [`SECURITY.md`](SECURITY.md)。

## 项目结构

```text
macro_recorder.py          # Windows GUI、录制、编辑、回放和 JSON 格式
tests/                     # 无窗口核心逻辑测试
examples/                  # 脱敏示例宏
build_exe.ps1              # PyInstaller 构建脚本
KeyMouseMacroRecorder.spec # PyInstaller 配置
requirements-dev.txt       # 仅构建所需的开发依赖
```

## 许可证

本项目以 [MIT License](LICENSE) 发布。欢迎 Fork、提交 Issue 和 Pull Request，一起把一个小而可靠的 Windows 工具做得更好。
