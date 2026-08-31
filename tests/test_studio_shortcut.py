from pathlib import Path
import os
import subprocess
import sys

import pytest

from longform_engine.studio_shortcut import install_windows_studio_shortcut


def test_windows_shortcut_targets_pythonw_workspace_studio_without_prompt_surface(tmp_path):
    workspace = (tmp_path / "Novel Projects").resolve()
    workspace.mkdir()
    desktop = (tmp_path / "Desktop").resolve()
    python = (tmp_path / "venv" / "Scripts" / "python.exe").resolve()
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    pythonw = python.with_name("pythonw.exe")
    pythonw.write_bytes(b"")
    calls: list[list[str]] = []
    call_options: list[dict[str, object]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        call_options.append(kwargs)
        shortcut = desktop / "小说创作工作台.lnk"
        shortcut.write_bytes(b"fake-link")
        return subprocess.CompletedProcess(command, 0, stdout=f"{shortcut}\n", stderr="")

    installed = install_windows_studio_shortcut(
        workspace,
        desktop=desktop,
        python_executable=python,
        powershell_executable="powershell.exe",
        platform_name="nt",
        runner=fake_run,
    )

    assert installed.path == desktop / "小说创作工作台.lnk"
    assert installed.target == pythonw
    assert installed.arguments == (
        '-m longform_engine.cli studio serve --workspace "'
        + str(workspace)
        + '" --create-workspace'
    )
    assert calls[0][0] == "powershell.exe"
    assert "-WindowStyle" in calls[0]
    assert "Hidden" in calls[0]
    environment = call_options[0]["env"]
    assert isinstance(environment, dict)
    assert environment["LONGFORM_STUDIO_SHORTCUT_ARGUMENTS"] == installed.arguments
    assert environment["LONGFORM_STUDIO_SHORTCUT_WORKSPACE"] == str(workspace)
    assert all("prompt" not in part.lower() for part in calls[0])


def test_shortcut_install_rejects_relative_workspace_and_non_windows(tmp_path):
    with pytest.raises(ValueError, match="绝对目录"):
        install_windows_studio_shortcut(
            Path("NovelProjects"),
            platform_name="nt",
            powershell_executable="powershell.exe",
        )
    with pytest.raises(ValueError, match="仅支持 Windows"):
        install_windows_studio_shortcut(tmp_path.resolve(), platform_name="posix")


@pytest.mark.skipif(os.name != "nt", reason="Windows WScript.Shell integration")
def test_windows_shortcut_is_created_by_real_wscript_shell(tmp_path):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pytest.skip("current Python environment has no pythonw.exe")

    workspace = (tmp_path / "Novel Projects").resolve()
    desktop = (tmp_path / "Desktop").resolve()
    installed = install_windows_studio_shortcut(workspace, desktop=desktop)

    assert installed.path.is_file()
    assert installed.path.name == "小说创作工作台.lnk"
    assert installed.target == pythonw.resolve()
    assert "studio serve --workspace" in installed.arguments
