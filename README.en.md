# KeyMouse Marco Weaver

<p align="right">
  <a href="README.md">简体中文</a> | <strong>English</strong>
</p>

![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D4.svg)
[![License](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)
[![Windows CI](https://github.com/Farisland1625/key_mouse_marco_weaver/actions/workflows/ci.yml/badge.svg)](https://github.com/Farisland1625/key_mouse_marco_weaver/actions/workflows/ci.yml)

A lightweight, editable, and composable keyboard and mouse macro recorder for Windows.

KeyMouse Marco Weaver does not treat recordings as an unchangeable black box. Every keystroke, mouse movement, click, and scroll action appears on a visual timeline. You can inspect events, edit parameters, reorder steps, insert new events, and combine multiple macro files in a chosen order with independent playback speeds and repeat counts to build a new automation workflow.

## Edit individual macro files: record, modify, or author them manually

- Select any event to edit its time, action type, and parameters.
- Insert keyboard press/release, mouse movement, mouse button press/release, and vertical or horizontal scroll events directly.
- Drag events directly to reorder them, or copy, delete, move them up or down, and verify them with single-step playback.
- Remove all mouse movement events with one click while preserving clicks, scrolling, and keyboard actions at their original screen coordinates.
- Undo and redo up to 20 recent edits with `Ctrl+Z` and `Ctrl+Y`.

## Compose multiple files into a complete workflow

Complex operations do not need to become one long, difficult-to-maintain macro. Save several short macros with focused responsibilities, then use **Multi-file composition** to generate a new standard macro file:

- Add the same macro or different macros in any order.
- Set an independent playback speed (`0.01x` to `20x`) and repeat count for each item.
- Drag macro files to reorder the composition, or move and remove items with the buttons while seeing the combined event count and duration immediately.
- Map mouse coordinates from source macros to the current virtual desktop when monitor layouts differ.

## High-precision recording and playback

- Low-level keyboard and mouse hooks run on the same message thread and use timestamps from the Windows hooks, reducing cross-thread ordering errors.
- Keyboard events preserve virtual-key codes, scan codes, and extended-key state; playback prefers scan codes when available.
- The virtual desktop bounds are saved at recording time, allowing mouse coordinates to be remapped when monitor arrangements or dimensions change.
- Playback supports speeds from 0.01x to 20x, a specific repeat count or continuous looping, and pause, resume, and stop controls.

## Lightweight deployment: Python standard library + Tkinter + Windows ctypes, approximately 11 MB as a packaged executable

## Interface preview

![Timeline editor and event properties in KeyMouse Marco Weaver](docs/images/key_mouse_marco_weaver-main-window.png)

## Quick start

### Run from source

Requires Windows 10/11 and Python 3.10 or later:

```powershell
git clone https://github.com/Farisland1625/key_mouse_marco_weaver.git
cd key_mouse_marco_weaver
python key_mouse_marco_weaver.py
```

Tkinter is normally included with Python for Windows. At runtime, the application uses only the Python standard library, Tkinter, and native Windows APIs, with no third-party runtime packages required.

### Use a release build

Download the standalone `key_mouse_marco_weaver.exe` from [GitHub Releases](https://github.com/Farisland1625/key_mouse_marco_weaver/releases/latest). Python is not required.

### Create your first editable macro

1. Press `F8` to start recording, then press `F8` again to stop.
2. Select an event on the timeline to inspect or edit its time, action, and parameters.
3. Use **Insert event** to add missing steps, or drag, copy, move, and delete existing steps.
4. Verify the selected event with **Single-step playback**, then click **Save** to write the macro to JSON.
5. Choose the playback speed and repeat mode, then press `F9` to start playback.
6. During playback, press `F9` to pause or resume and `F10` to stop recording or playback.

See [`examples/basic_click.json`](examples/basic_click.json) for a sanitized format example.

### Compose multiple macros

1. Record, edit, and save the short macros you want to reuse.
2. Click **Multi-file composition** in the top toolbar.
3. Add macro files in execution order; the same file can be added more than once.
4. Set the playback speed and repeat count for each item, then drag to reorder or use the move and remove buttons as needed.
5. Save the composition. The application creates a new schema 2 macro file and leaves every source file unchanged.

## Macro format and privacy

Macros use JSON with `schema_version: 2` and contain only `key` and `mouse` events. Each event's `t` value is an absolute time in seconds from the start of the macro:

- Keyboard events contain `vk`, `scan`, `action`, and `extended`.
- Mouse events contain `message` and, depending on the action, `x`, `y`, or `data`.
- Saving uses a unique temporary file, `flush` / `fsync`, and atomic replacement to reduce the risk of a partially written file after an interruption.
- Loading strictly validates the schema, timestamps, event types, coordinates, and codes instead of silently accepting corrupted data.

Macros may contain complete keyboard input, screen coordinates, and action timing. Do not record passwords, verification codes, tokens, private keys, or other sensitive information, and do not upload personal macros to Issues, Pull Requests, or public repositories.

The entire `macros/` directory is ignored by default. Only sanitized minimal examples belong in `examples/`. The application does not connect to the network or upload macro content by itself.

## Safety boundaries

- Playback controls the active mouse and keyboard. Switch to the intended target window first, and make sure the application and target run with the same permission level.
- `F8`, `F9`, and `F10` are global control keys. Avoid using them as ordinary keys within a macro workflow.
- Do not run macro files from unknown sources or modified executables supplied by others.
- The application does not hide while running and is not intended to bypass user control or system security mechanisms.
- The project supports Windows only and has only been verified on Windows.

See [`SECURITY.md`](SECURITY.md) for security boundaries and vulnerability reporting instructions.

## Build a standalone executable

```powershell
python -m venv .venv_user
.\.venv_user\Scripts\python.exe -m pip install -r requirements-dev.txt
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

The output is `dist\key_mouse_marco_weaver.exe`. The `build/`, `dist/`, and virtual environment directories are excluded by `.gitignore`. Distribute binaries through GitHub Releases instead of committing build artifacts to source history.

## Development and verification

```powershell
.\.venv_user\Scripts\python.exe -m unittest discover -s tests -v
.\.venv_user\Scripts\python.exe -m py_compile key_mouse_marco_weaver.py tests\test_macro_format.py
```

The current baseline contains 46 core logic tests and has been verified with a PyInstaller one-file build. GitHub Actions runs the tests and syntax checks on Python 3.10 and 3.12 for pushes and Pull Requests, and verifies the one-file build on Python 3.12.

Reproducible bug reports, accuracy cases, timeline editing suggestions, and UI improvements are welcome. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before contributing code, and report security issues privately as described in [`SECURITY.md`](SECURITY.md).

## Project structure

```text
key_mouse_marco_weaver.py # Windows GUI, recording, editing, playback, and JSON format
tests/                     # Headless core logic tests
examples/                  # Sanitized example macros
build_exe.ps1              # PyInstaller build script
key_mouse_marco_weaver.spec # PyInstaller configuration
requirements-dev.txt       # Development dependencies used only for builds
```

## License

This project is released under the [MIT License](LICENSE). Forks, Issues, and Pull Requests are welcome as we continue building a small, reliable, and genuinely editable Windows macro tool.
