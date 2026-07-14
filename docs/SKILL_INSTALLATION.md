# Codex And Claude Code Skill Installation

This guide verifies how to use the bundled `longform-novel-engine` skills from Codex / Claude Code without changing the engine's safety model.

Default assumptions:

- Install the Python package in a project-local `.venv`.
- Install the engine first, then install the Codex / Claude Code skill packages.
- Use the installed console command: `longform-engine ...`.
- Keep `python -m longform_engine.cli ...` only as a development fallback from a source checkout.
- Ordinary Agent-Skill writing does not require an OpenAI, Anthropic, or other provider API key.
- Codex / Claude Code may draft prose only under `50_workbench/agent_drafts/`.
- Only CLI commands may submit, gate, finalize, index, update graph/memory, or sync SQLite.

## 1. Install The Engine First

From the `longform-novel-engine` repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e .
longform-engine validate-config --template qidian-longform
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e .
longform-engine validate-config --template qidian-longform
```

Semantic RAG extras are optional:

```powershell
pip install -e ".[semantic]"
longform-engine models verify project.yaml
```

## 2. Install Skills Globally

Use copy mode when you want a stable installed snapshot. Use junction or symlink mode when you are actively developing this repository and want Codex / Claude Code to read live changes.

After copying or linking, start a new Codex / Claude Code session if the current session does not refresh skill discovery.

### 2.1 Recommended Windows PowerShell Installer

From the `longform-novel-engine` repository root:

```powershell
.\scripts\install-agent-skills.ps1 -Tool all -Mode copy
```

For live development links instead of copied snapshots:

```powershell
.\scripts\install-agent-skills.ps1 -Tool all -Mode junction -Force
```

The installer copies or links only `longform-novel-codex/`, `longform-novel-claude/`, and `shared/`. It does not copy novel projects, manuscripts, runtime databases, model caches, `.env` files, or API keys.

### 2.2 Windows PowerShell Copy

From the `longform-novel-engine` repository root:

```powershell
cd D:\soft\code\OpenGit\novel-generator\longform-novel-engine
$codexSkillRoot = Join-Path $env:USERPROFILE ".codex\skills"
$claudeSkillRoot = Join-Path $env:USERPROFILE ".claude\skills"
$codexSkill = Join-Path $codexSkillRoot "longform-novel-codex"
$claudeSkill = Join-Path $claudeSkillRoot "longform-novel-claude"
$codexShared = Join-Path $codexSkillRoot "shared"
$claudeShared = Join-Path $claudeSkillRoot "shared"
New-Item -ItemType Directory -Force $codexSkill | Out-Null
New-Item -ItemType Directory -Force $claudeSkill | Out-Null
New-Item -ItemType Directory -Force $codexShared | Out-Null
New-Item -ItemType Directory -Force $claudeShared | Out-Null
Copy-Item -Recurse -Force .\longform-novel-codex\* $codexSkill
Copy-Item -Recurse -Force .\longform-novel-claude\* $claudeSkill
Copy-Item -Recurse -Force .\shared\* $codexShared
Copy-Item -Recurse -Force .\shared\* $claudeShared
```

### 2.3 Windows PowerShell Junction

Use this only when the target skill paths do not already exist or after you have intentionally removed the old installed copies.

```powershell
cd D:\soft\code\OpenGit\novel-generator\longform-novel-engine
$codexSkillRoot = Join-Path $env:USERPROFILE ".codex\skills"
$claudeSkillRoot = Join-Path $env:USERPROFILE ".claude\skills"
New-Item -ItemType Directory -Force $codexSkillRoot | Out-Null
New-Item -ItemType Directory -Force $claudeSkillRoot | Out-Null
New-Item -ItemType Junction -Path (Join-Path $codexSkillRoot "shared") -Target "$PWD\shared"
New-Item -ItemType Junction -Path (Join-Path $claudeSkillRoot "shared") -Target "$PWD\shared"
New-Item -ItemType Junction -Path (Join-Path $codexSkillRoot "longform-novel-codex") -Target "$PWD\longform-novel-codex"
New-Item -ItemType Junction -Path (Join-Path $claudeSkillRoot "longform-novel-claude") -Target "$PWD\longform-novel-claude"
```

### 2.4 Recommended macOS / Linux Bash Installer

From the `longform-novel-engine` repository root:

```bash
bash scripts/install-agent-skills.sh --tool all --mode copy
```

For live development links instead of copied snapshots:

```bash
bash scripts/install-agent-skills.sh --tool all --mode symlink --force
```

The installer copies or links only `longform-novel-codex/`, `longform-novel-claude/`, and `shared/`. It does not copy novel projects, manuscripts, runtime databases, model caches, `.env` files, or API keys.

### 2.5 macOS / Linux Copy

From the `longform-novel-engine` repository root:

```bash
mkdir -p "$HOME/.codex/skills/longform-novel-codex" "$HOME/.codex/skills/shared"
mkdir -p "$HOME/.claude/skills/longform-novel-claude" "$HOME/.claude/skills/shared"
cp -R ./longform-novel-codex/. "$HOME/.codex/skills/longform-novel-codex/"
cp -R ./longform-novel-claude/. "$HOME/.claude/skills/longform-novel-claude/"
cp -R ./shared/. "$HOME/.codex/skills/shared/"
cp -R ./shared/. "$HOME/.claude/skills/shared/"
```

### 2.6 macOS / Linux Symlink

Use this only when the target skill paths do not already exist or are symlinks you intend to replace.

```bash
mkdir -p "$HOME/.codex/skills" "$HOME/.claude/skills"
ln -s "$PWD/shared" "$HOME/.codex/skills/shared"
ln -s "$PWD/shared" "$HOME/.claude/skills/shared"
ln -s "$PWD/longform-novel-codex" "$HOME/.codex/skills/longform-novel-codex"
ln -s "$PWD/longform-novel-claude" "$HOME/.claude/skills/longform-novel-claude"
```

## 3. Use Skills From The Repository

Codex can use the repository skill directly by reading:

```text
longform-novel-codex/SKILL.md
```

Claude Code can use the repository skill directly by reading:

```text
longform-novel-claude/SKILL.md
```

In repository mode and installed-skill mode, run engine commands from the `longform-novel-engine` root after activating `.venv`:

```powershell
longform-engine open-book --interactive
longform-engine production next novels/my-book/project.yaml
longform-engine agent-task brief novels/my-book/project.yaml chapter_write:ch001:v1
longform-engine continue-write novels/my-book/project.yaml --chapter 1
```

In the user-facing skill flow, present this as `/工程开书`. It creates the project through the interactive wizard when `project.yaml` is missing, then runs open-book initialization. Use `init-project` only as an advanced CLI-only path when the user explicitly wants to create a directory and config without opening the book.

The productized Agent App flow should first inspect the safe next action, then render the work order:

```powershell
longform-engine production next novels/my-book/project.yaml
longform-engine agent-task brief novels/my-book/project.yaml chapter_write:ch001:v1
```

The Agent should then read only the work order and the declared input files, including:

```text
novels/my-book/50_workbench/writing_tasks/ch001.md
```

Codex writes only:

```text
novels/my-book/50_workbench/agent_drafts/ch001.codex.md
```

ClaudeCode writes only:

```text
novels/my-book/50_workbench/agent_drafts/ch001.claude.md
```

Drafts become managed project state only through:

```powershell
longform-engine draft submit novels/my-book/project.yaml --chapter 1 --file novels/my-book/50_workbench/agent_drafts/ch001.codex.md --agent codex
longform-engine chapter finalize novels/my-book/project.yaml --chapter 1 --approved-by human
```

ClaudeCode uses the same flow with `ch001.claude.md` and `--agent claude`.

## 4. Output Lanes And Hard Boundaries

Codex writes only:

```text
novels/my-book/50_workbench/agent_drafts/chNNN.codex.md
```

Claude Code writes only:

```text
novels/my-book/50_workbench/agent_drafts/chNNN.claude.md
```

Agent products must not directly write:

```text
40_manuscript/final/
60_rag/
30_state/story_graph.json
30_state/tcs/
70_runtime/db/
```

Drafts become managed project state only through CLI submit/gate/finalize commands.

Codex submit example:

```powershell
longform-engine draft submit novels/my-book/project.yaml --chapter 1 --file novels/my-book/50_workbench/agent_drafts/ch001.codex.md --agent codex
longform-engine chapter finalize novels/my-book/project.yaml --chapter 1 --approved-by human
```

Claude Code submit example:

```powershell
longform-engine draft submit novels/my-book/project.yaml --chapter 1 --file novels/my-book/50_workbench/agent_drafts/ch001.claude.md --agent claude
longform-engine chapter finalize novels/my-book/project.yaml --chapter 1 --approved-by human
```

## 5. Development Fallback

Use this fallback only when the editable package has not been installed yet:

```powershell
cd D:\soft\code\OpenGit\novel-generator\longform-novel-engine
$env:PYTHONPATH=".;.\src"
python -m longform_engine.cli validate-config --template qidian-longform
```

The public skill examples should still prefer `longform-engine ...` so that Codex / ClaudeCode users see the same commands they will use after installation.

Productized workflow details and checklist:

```text
docs/AGENT_APP_WORKFLOW_PRODUCTIZATION.md
docs/AGENT_APP_WORKFLOW_PRODUCTIZATION_CHECKLIST.md
```

## 6. Validate Skill Packages

Run:

```powershell
python scripts/validate_skills.py
```

Expected result:

```text
OK: skill packages validated
```
