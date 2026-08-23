# Public Release Runbook

公开源固定为 `https://github.com/ylqit/novel-general`，默认分支为 `master`。发布工具只负责诊断，不会自动 commit、push、tag、创建 Release 或覆盖全局 Skill。v0.10.0 的历史发布例外保留在其清单；v0.11.0 按当前发布纪律执行。

## 1. 完成实现

发布前先完成源码、配置、文档、Skill 引用和资源清单的全部修改。资源内容稳定后执行：

```powershell
python scripts/sync_skill_references.py --write
python scripts/build_resource_manifest.py --write
```

这一阶段不运行回归测试。必须保持单进程，不使用 xdist 或并行 Agent worker。

## 2. 一次单进程定向本地验证

所有实现和资源同步结束后，运行覆盖当前版本改动面的单进程测试与验证；禁止 xdist：

```powershell
python -m ruff check src tests
python -m mypy --follow-imports=skip src/longform_engine/vector_backends.py src/longform_engine/chapter_contract.py src/longform_engine/story_brief.py src/longform_engine/storage/recovery.py src/longform_engine/human_author_revision.py src/longform_engine/human_story_review.py src/longform_engine/human_review_consultation.py src/longform_engine/author_voice.py src/longform_engine/publication.py
python -m pytest -q -p no:xdist
python scripts/validate_skills.py
python scripts/sync_skill_references.py --check
python scripts/build_resource_manifest.py --check
python scripts/check_markdown_links.py
python scripts/check_agent_data_pipeline_readiness.py
python scripts/release_surface_guards.py
longform-engine release check --repository . --channel rc --json
```

常规发布应构建并审计候选分发包，且不在实现阶段修改用户全局 Skill。v0.11.0 不额外执行安装 smoke、doctor 或语义 smoke；正式 tag 仍要求本节单进程验证和主分支 CI 成功。

## 3. 发布提交与主分支

审查 `git diff` 后提交：

```powershell
git add --all
git commit -m "release: publish v<VERSION>"
git switch master
# 若候选位于独立分支，再执行：git merge --ff-only <reviewed-release-branch>
longform-engine release check --repository . --channel public --json
git push origin master
```

发布应等待 `master` 的 GitHub Actions 成功；失败时停止，不创建 tag。

## 4. 不可变 Tag 与 GitHub Release

主分支 CI 成功后执行：

```powershell
git tag -a v<VERSION> -m "longform-novel-engine v<VERSION>"
longform-engine release check --repository . --channel public --tag v<VERSION> --json
git push origin v<VERSION>
longform-engine release check --repository . --channel public --check-remote --tag v<VERSION> --json
```

Release workflow 从 tag 构建 wheel/sdist、生成 `SHA256SUMS` 并上传三个制品。它不运行测试、Skill/Markdown 守卫、分发审计或安装 smoke。tag 不得移动或覆盖；发布后缺陷使用新的补丁版本。

## 5. Release 证据与本机更新

等待 tag 的制品发布任务成功，并确认 GitHub Release 包含 wheel、sdist 与 `SHA256SUMS`。Actions run、不可变 tag 和 Release assets 是发布事实；不追加发布后证据提交，也不移动 tag。

只有用户显式授权时才更新全局 CLI 与 Skill。v0.11.0 使用现有 pipx 的 Python 3.12 环境从不可变 tag 强制安装 `[semantic]`，再执行 `longform-engine skills update --tool codex`。本次授权不运行额外 doctor、semantic smoke 或临时安装验证；命令成功只表示更新操作完成。
