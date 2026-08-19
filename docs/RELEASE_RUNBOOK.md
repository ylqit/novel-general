# Public Release Runbook

公开源固定为 `https://github.com/ylqit/novel-general`，默认分支为 `master`。发布工具只负责诊断，不会自动 commit、push、tag 或创建 Release。版本级执行证据统一记录在 `V0_4_3_RELEASE_CHECKLIST.md`。

## 1. 完成实现

发布前先完成源码、配置、文档、Skill 引用和资源清单的全部修改。资源内容稳定后执行：

```powershell
python scripts/sync_skill_references.py --write
python scripts/build_resource_manifest.py --write
```

这一阶段不运行回归测试。必须保持单进程，不使用 xdist 或并行 Agent worker。

## 2. 一次完整本地验证

所有实现结束后，按清单顺序执行一次完整验证：

```powershell
python -m ruff check src tests
python -m mypy src/longform_engine/config/loader.py src/longform_engine/storage/layout.py src/longform_engine/benchmark.py src/longform_engine/blind_review.py src/longform_engine/vector_backends.py src/longform_engine/chapter_contract.py src/longform_engine/storage/recovery.py
python -m pytest --cov=longform_engine --cov-report=term-missing
python scripts/sync_skill_references.py --check
python scripts/validate_skills.py
python scripts/build_resource_manifest.py --check
python scripts/check_markdown_links.py
python scripts/check_agent_data_pipeline_readiness.py
python scripts/release_surface_guards.py
python -m build
python scripts/audit_wheel.py
python scripts/audit_sdist.py
python scripts/build_release_checksums.py --write
python scripts/build_release_checksums.py --check
```

随后在临时隔离环境安装本地 wheel，验证版本、配置解释、doctor 和仓库内 Skill 状态。不得安装或修改用户全局 Skill。把命令、退出码、测试数、覆盖率和构建摘要写回清单。

## 3. 发布提交与主分支

审查 `git diff` 后提交：

```powershell
git add --all
git commit -m "release: publish v0.4.3"
git switch master
git merge --ff-only codex/v043-architecture-convergence
longform-engine release check --repository . --channel public --json
git push origin master
```

等待 `master` 的 GitHub Actions 全部成功。任何失败都必须先在主分支修复并重新通过 CI，不能提前创建 tag。

## 4. 不可变 Tag 与 GitHub Release

主分支 CI 成功后执行：

```powershell
git tag -a v0.4.3 -m "longform-novel-engine v0.4.3"
longform-engine release check --repository . --channel public --tag v0.4.3 --json
git push origin v0.4.3
longform-engine release check --repository . --channel public --check-remote --tag v0.4.3 --json
```

Release workflow 必须从 tag 重新运行验证、构建 wheel/sdist、执行分发审计，并上传两个包及 `SHA256SUMS`。tag 不得移动或覆盖；发布后缺陷使用新的补丁版本。

## 5. 远程 Tag Smoke 与证据回写

从远程 tag 在新临时环境安装，执行：

```powershell
longform-engine --version
longform-engine validate-config --template qidian-longform --explain
longform-engine skills status --tool all --json
longform-engine doctor --tool all --json
```

Skill 状态和 doctor 使用临时目录环境变量，不写用户全局目录。确认 GitHub Release、wheel、sdist 与校验信息后，将 URL、提交号、tag 对象、资产 SHA-256 和 smoke 结果写回清单，单独提交并推送；最后等待该文档提交的 CI 成功。
