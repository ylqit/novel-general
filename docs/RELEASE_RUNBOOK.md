# Public Release Runbook

公开源固定为 `https://github.com/ylqit/novel-general`，默认分支为 `master`。发布工具只负责诊断，不会自动 commit、push、tag、创建 Release 或覆盖全局 Skill。v0.6.0 的仓库内门禁记录在 `V0_6_0_RELEASE_CHECKLIST.md`；发布提交切换稳定元数据后，仍须等待远程 Actions 与 Release assets 完成，才可宣告公开发布成功。

## 1. 完成实现

发布前先完成源码、配置、文档、Skill 引用和资源清单的全部修改。资源内容稳定后执行：

```powershell
python scripts/sync_skill_references.py --write
python scripts/build_resource_manifest.py --write
```

这一阶段不运行回归测试。必须保持单进程，不使用 xdist 或并行 Agent worker。

## 2. 一次单进程定向本地验证

所有实现和资源同步结束后，运行覆盖 v0.6.0 改动面的单进程测试与验证；禁止 xdist：

```powershell
python -m pytest -q -p no:xdist
python scripts/validate_skills.py
python scripts/sync_skill_references.py --check
python scripts/build_resource_manifest.py --check
python scripts/check_markdown_links.py
python scripts/check_agent_data_pipeline_readiness.py
python scripts/release_surface_guards.py
longform-engine release check --repository . --channel rc --json
```

本机可以构建并审计候选分发包，但不安装或修改用户全局 Skill。Pull Request CI 负责完整跨平台矩阵；tag Release workflow 负责重新测试、构建、wheel/sdist 审计和校验和。把实际命令、退出码和测试数写回清单。

## 3. 发布提交与主分支

审查 `git diff` 后提交：

```powershell
git add --all
git commit -m "release: publish v0.6.0"
git switch master
git merge --ff-only codex/v060-human-deep-review
longform-engine release check --repository . --channel public --json
git push origin master
```

等待 `master` 的 GitHub Actions 全部成功。任何失败都必须先在主分支修复并重新通过 CI，不能提前创建 tag。

## 4. 不可变 Tag 与 GitHub Release

主分支 CI 成功后执行：

```powershell
git tag -a v0.6.0 -m "longform-novel-engine v0.6.0"
longform-engine release check --repository . --channel public --tag v0.6.0 --json
git push origin v0.6.0
longform-engine release check --repository . --channel public --check-remote --tag v0.6.0 --json
```

Release workflow 必须从 tag 重新运行验证、构建 wheel/sdist、执行分发审计，并上传两个包及 `SHA256SUMS`。tag 不得移动或覆盖；发布后缺陷使用新的补丁版本。

## 5. 远程 Tag Smoke 与证据

从远程 tag 在新临时环境安装，执行：

```powershell
longform-engine --version
longform-engine validate-config --template qidian-longform --explain
longform-engine skills status --tool all --json
longform-engine doctor --tool all --json
```

Skill 状态和 doctor 使用临时目录环境变量，不写用户全局目录。确认 GitHub Release、wheel、sdist 与校验信息后，以 Actions run、不可变 tag 和 Release assets 作为权威证据；v0.6.0 不追加发布后证据提交。只有公开发布完成后，才由用户显式决定是否同步全局 CLI 与 Skill。
