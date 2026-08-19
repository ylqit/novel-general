# Codex And Claude Code Skill Installation

公开发行源：`https://github.com/ylqit/novel-general`。普通用户使用 pipx 安装 engine，再由 `longform-engine skills` 管理内置的自包含 Skill；无需手工复制 `shared/`，也无需 OpenAI、Anthropic 或 provider API key。

当前公开稳定版是 `v0.4.4`。安装命令固定到不可变 tag；该版本不提供旧项目协议自动迁移。升级 engine 后必须同步更新 Skill 并重新运行 doctor。

## Public Install

Windows PowerShell：

```powershell
py -3 -m pip install --user --upgrade pipx
py -3 -m pipx ensurepath
$env:PIPX_BIN_DIR = if ($env:PIPX_BIN_DIR) { $env:PIPX_BIN_DIR } else { Join-Path $env:USERPROFILE ".local\bin" }
$env:PATH = "$env:PIPX_BIN_DIR;$env:PATH"
py -3 -m pipx install --force 'longform-novel-engine[semantic] @ git+https://github.com/ylqit/novel-general.git@v0.4.4'
longform-engine skills install --tool all --force
longform-engine doctor --tool all
```

macOS / Linux：

```bash
python3 -m pip install --user --upgrade pipx
python3 -m pipx ensurepath
export PIPX_BIN_DIR="${PIPX_BIN_DIR:-$HOME/.local/bin}"
export PATH="$PIPX_BIN_DIR:$PATH"
python3 -m pipx install --force 'longform-novel-engine[semantic] @ git+https://github.com/ylqit/novel-general.git@v0.4.4'
longform-engine skills install --tool all --force
longform-engine doctor --tool all
```

`--tool` 可选 `codex`、`claude-code` 或 `all`。安装后重启宿主会话，刷新 Skill discovery。

## Skill Lifecycle

```powershell
longform-engine skills status --tool all
longform-engine skills update --tool all
longform-engine skills uninstall --tool all --yes
```

- `install` 使用同目录 staging、校验、原子替换和失败恢复。
- `status --json` 输出 `skill_install_status_v1`。
- `update` 只更新带合法 `.longform-install.json` 的本项目 Skill。
- `uninstall` 只删除带合法 ownership 元数据的目标。
- 安装器不会删除缺少本引擎 ownership 元数据的目录。
- 安装器不会复制小说正文、`.env`、API key、runtime DB、模型缓存或运行产物。

## Doctor

```powershell
longform-engine doctor --tool all
longform-engine doctor --tool all --project project.yaml
longform-engine doctor --tool all --project project.yaml --json
```

doctor 检查 engine 版本、wheel 资源哈希、Skill 状态、Semantic Python 依赖、项目配置和模型缓存。它不会自动下载模型；缺失时按输出执行：

```powershell
longform-engine models install project.yaml --profile bge-m3 --download
```

JSON 使用稳定的 `doctor_v1` schema。

## Shared Model Cache

Semantic 模型默认安装到操作系统的用户级缓存，同一台机器上的小说项目共用一份模型。项目只保存 `semantic_model_cache_ref_v1` 引用，不复制模型权重。

```powershell
longform-engine models cache-status --json
```

显式绝对 `models_dir` 视为用户管理路径；doctor 只核验，不移动或删除用户模型文件。

## First Production

```powershell
longform-engine validate-config --template qidian-longform
longform-engine open-book --interactive
longform-engine production next project.yaml
longform-engine agent-task brief project.yaml TASK_OR_PATH
```

Codex 正文草稿写入 `50_workbench/agent_drafts/chNNN.codex.md`；Claude Code 写入 `50_workbench/agent_drafts/chNNN.claude.md`。其他 Agent 输出必须使用 manifest 的 `allowed_output_paths`。

Agent 不得直接写：

```text
10_bible/
20_outline/
10_bible/research_canon.jsonl
30_state/story_graph.json
30_state/tcs/
40_manuscript/final/
60_rag/
70_runtime/db/
```

标准闭环是 `production next -> agent-task brief -> Agent output -> validate -> explicit apply/finalize`。

## Developer Checkout

只有开发 engine 或 Skill 时才需要 clone 与 editable install：

```powershell
git clone https://github.com/ylqit/novel-general.git
Set-Location novel-general
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[semantic]"
python scripts\sync_skill_references.py --check
python scripts\build_resource_manifest.py --check
python scripts\validate_skills.py
```

开发态实时链接仍可使用：

```powershell
.\scripts\install-agent-skills.ps1 -Tool all -Mode junction -Force
```

```bash
bash scripts/install-agent-skills.sh --tool all --mode symlink --force
```

公开安装始终使用 CLI 的安全 copy。开发脚本只应链接 `longform-novel-codex/` 与 `longform-novel-claude/`；两个目录内的 `references/` 由 `scripts/sync_skill_references.py` 同步。

## 管理文档

- `docs/ARCHITECTURE.md`
- `docs/STORAGE_MODEL.md`
- `docs/CONFIGURATION.md`
- `docs/RELEASE_RUNBOOK.md`
- `docs/V0_4_4_RELEASE_CHECKLIST.md`
