# Public Release Runbook

公开源固定为 `https://github.com/ylqit/novel-general`，默认分支为 `master`。本流程不会由 CLI 自动 commit、push、tag 或创建 Release。

## 1. Readiness

在仓库根目录运行：

```powershell
longform-engine release check --repository . --check-remote
python -m pytest
python -m build
python scripts/audit_wheel.py
python scripts/audit_sdist.py
```

`release_readiness_v1.ok` 必须为 `true`。检查内容包括版本、README 安装 tag、MIT、Git commit、干净工作区、`origin`、CI/Release workflow、Skill references、资源 manifest 和 release guards。

## 2. 初始仓库接入

当前目录如果没有 commit 或 `origin`，必须先人工审查全部文件，再执行标准 Git 接入。不要在 readiness 工具中自动完成这些动作。

目标 remote：

```text
https://github.com/ylqit/novel-general.git
```

推送 `master` 后，等待 GitHub Actions 的 Windows、Ubuntu、macOS 和 Semantic jobs 全部通过，再进入 tag 阶段。

## 3. Tag 与 Release

版本 `0.3.1` 对应且只对应 tag `v0.3.1`。创建 tag 前再次运行：

```powershell
longform-engine release check --repository . --check-remote
```

得到明确发布确认后才创建并推送 tag。Release workflow 会重新运行测试、构建 wheel/sdist、审计两个分发包，并校验 `GITHUB_REF_NAME` 与包版本一致，然后附加 `dist/*.whl` 和 `dist/*.tar.gz`。

## 4. 发布后 Smoke

在新的终端按 README 执行 Git tag URL 的 pipx 安装，然后验证：

```powershell
longform-engine --version
longform-engine skills install --tool all
longform-engine skills status --tool all --json
longform-engine doctor --tool all --json
longform-engine validate-config --template qidian-longform
```

最后在真实 Codex 与 Claude Code 会话中重启 Skill discovery，分别运行 `/工程下一步` 和 `/工程工单`。只有这些步骤完成后，checklist 中的远程安装、宿主 discovery 与 5 章 smoke 才能从 `[~]` 改为 `[x]`。
