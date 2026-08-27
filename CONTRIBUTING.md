# Contributing

感谢你愿意参与 KeyMouse Macro Recorder。项目优先接受能够复现、测试覆盖清晰、且保持 Windows 原生轻量路线的改动。

## 开发环境

- Windows 10/11
- Python 3.10 或更高版本
- Tkinter（通常随 Python 一起安装）
- PyInstaller 仅在需要构建 exe 时使用

创建环境并安装开发依赖：

```powershell
python -m venv .venv_user
.\\.venv_user\\Scripts\\python.exe -m pip install -r requirements-dev.txt
```

## 验证

提交前运行：

```powershell
.\\.venv_user\\Scripts\\python.exe -m unittest discover -s tests -v
.\\.venv_user\\Scripts\\python.exe -m py_compile macro_recorder.py tests\\test_macro_format.py
```

需要验证打包时运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\\build_exe.ps1
```

## 提交建议

- 一个 Pull Request 尽量只解决一个问题，并说明用户可见行为变化。
- 核心事件模型、时间轴计算、文件校验和输入释放逻辑应配套无窗口单元测试。
- 不要提交 `AGENTS.md`、`.venv_user/`、`build/`、`dist/` 或个人录制的宏文件。
- 涉及回放输入的改动，请说明停止、暂停、异常和不同 DPI/多显示器场景下的行为。
- UI 改动请附启动检查或截图说明，并确认没有改变宏 JSON schema，除非这是明确的版本变更。

## Issue 与 Pull Request

提交 Issue 时请包含 Windows 版本、Python/发布版信息、复现步骤和相关日志。请先搜索已有 Issue；安全问题请按 [SECURITY.md](SECURITY.md) 处理。
