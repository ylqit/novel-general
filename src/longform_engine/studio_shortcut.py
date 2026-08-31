"""Install the Windows desktop entry for the local Workspace Studio."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable


SHORTCUT_NAME = "小说创作工作台.lnk"


@dataclass(frozen=True)
class InstalledStudioShortcut:
    path: Path
    target: Path
    arguments: str


def install_windows_studio_shortcut(
    workspace: Path,
    *,
    desktop: Path | None = None,
    python_executable: Path | None = None,
    powershell_executable: str | None = None,
    platform_name: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> InstalledStudioShortcut:
    """Create a desktop shortcut that launches one loopback Workspace Studio instance."""

    platform = platform_name or os.name
    if platform != "nt":
        raise ValueError("studio shortcut-install 目前仅支持 Windows")
    if not workspace.is_absolute():
        raise ValueError("工作区必须是明确的绝对目录")
    workspace = workspace.resolve()
    desktop_path: Path | None = None
    if desktop is not None:
        if not desktop.is_absolute():
            raise ValueError("桌面目录必须是明确的绝对目录")
        desktop_path = desktop.resolve()
        desktop_path.mkdir(parents=True, exist_ok=True)

    python_path = (python_executable or Path(sys.executable)).resolve()
    pythonw_path = python_path.with_name("pythonw.exe")
    if not pythonw_path.is_file():
        raise ValueError(f"找不到无控制台 Python 启动器：{pythonw_path}")
    powershell = powershell_executable or shutil.which("powershell.exe")
    if not powershell:
        raise ValueError("找不到 powershell.exe，无法创建 Windows 桌面快捷方式")

    arguments = (
        '-m longform_engine.cli studio serve --workspace "'
        + str(workspace)
        + '" --create-workspace'
    )
    script = "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)",
            "$OutputEncoding = [Console]::OutputEncoding",
            "$desktop = $env:LONGFORM_STUDIO_SHORTCUT_DESKTOP",
            "if ([string]::IsNullOrWhiteSpace($desktop)) {",
            "  $desktop = [Environment]::GetFolderPath('Desktop')",
            "}",
            "if ([string]::IsNullOrWhiteSpace($desktop)) {",
            "  throw 'Windows Desktop directory is unavailable'",
            "}",
            "$shortcutPath = Join-Path $desktop $env:LONGFORM_STUDIO_SHORTCUT_NAME",
            "$shell = New-Object -ComObject WScript.Shell",
            "$shortcut = $shell.CreateShortcut($shortcutPath)",
            "$shortcut.TargetPath = $env:LONGFORM_STUDIO_SHORTCUT_TARGET",
            "$shortcut.Arguments = $env:LONGFORM_STUDIO_SHORTCUT_ARGUMENTS",
            "$shortcut.WorkingDirectory = $env:LONGFORM_STUDIO_SHORTCUT_WORKSPACE",
            "$shortcut.Description = $env:LONGFORM_STUDIO_SHORTCUT_DESCRIPTION",
            "$shortcut.IconLocation = $env:LONGFORM_STUDIO_SHORTCUT_TARGET + ',0'",
            "$shortcut.Save()",
            "Write-Output $shortcutPath",
        )
    )
    command = [
        powershell,
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-Command",
        script,
    ]
    environment = os.environ.copy()
    environment.update(
        {
            "LONGFORM_STUDIO_SHORTCUT_DESKTOP": (
                str(desktop_path) if desktop_path is not None else ""
            ),
            "LONGFORM_STUDIO_SHORTCUT_NAME": SHORTCUT_NAME,
            "LONGFORM_STUDIO_SHORTCUT_TARGET": str(pythonw_path),
            "LONGFORM_STUDIO_SHORTCUT_ARGUMENTS": arguments,
            "LONGFORM_STUDIO_SHORTCUT_WORKSPACE": str(workspace),
            "LONGFORM_STUDIO_SHORTCUT_DESCRIPTION": "本地小说创作工作台",
        }
    )
    completed = runner(
        command,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "PowerShell 未返回错误详情"
        raise ValueError(f"创建桌面快捷方式失败：{detail}")
    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not output_lines:
        raise ValueError("创建桌面快捷方式失败：PowerShell 未返回快捷方式路径")
    shortcut_path = Path(output_lines[-1]).resolve()
    if shortcut_path.name != SHORTCUT_NAME or not shortcut_path.is_file():
        raise ValueError(f"创建桌面快捷方式失败：未找到 {shortcut_path}")
    return InstalledStudioShortcut(
        path=shortcut_path,
        target=pythonw_path,
        arguments=arguments,
    )


__all__ = ["InstalledStudioShortcut", "install_windows_studio_shortcut"]
