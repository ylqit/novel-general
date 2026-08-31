# Public Release Runbook

公开源固定为 `https://github.com/ylqit/novel-general`，默认分支为 `master`。发布工具只负责诊断，不会自动 commit、push、tag、创建 Release 或覆盖全局 Skill。发布分为默认的完整制品路径和用户逐次明确授权的 Tag-only 直接路径。

## 1. 完成实现

发布前先完成源码、配置、文档、Skill 引用和资源清单的全部修改。资源内容稳定后执行：

```powershell
python scripts/sync_skill_references.py --write
python scripts/build_resource_manifest.py --write
```

选择执行验证和构建时必须保持单进程，不使用 xdist 或并行 Agent worker。Tag-only 路径仍需同步版本、文档和资源清单，但可以不执行后续验证。

## 2. 完整制品路径的本地验证

默认的完整制品路径在所有实现和资源同步结束后，运行完整单进程维护检查；禁止 xdist：

```powershell
python -m ruff check src tests
python -m mypy --follow-imports=skip src/longform_engine/vector_backends.py src/longform_engine/chapter_contract.py src/longform_engine/story_brief.py src/longform_engine/storage/recovery.py src/longform_engine/human_author_revision.py src/longform_engine/human_story_review.py src/longform_engine/human_review_consultation.py src/longform_engine/author_voice.py src/longform_engine/publication.py src/longform_engine/semantic_protocols.py src/longform_engine/source_protocols.py src/longform_engine/source_processing.py src/longform_engine/fanfiction_sources.py src/longform_engine/source_materialization.py src/longform_engine/fanfiction_context.py src/longform_engine/local_web.py src/longform_engine/studio_server.py
python -m pytest -q -p no:xdist
python scripts/validate_skills.py
python scripts/sync_skill_references.py --check
python scripts/build_resource_manifest.py --check
python scripts/check_markdown_links.py
python scripts/check_agent_data_pipeline_readiness.py
python scripts/release_surface_guards.py
longform-engine release check --repository . --channel rc --json
```

## 3. 完整制品发布

审查 `git diff` 后提交：

```powershell
git add --all
git commit -m "release: publish v<VERSION>"
git switch master
# 若候选位于独立分支，再执行：git merge --ff-only <reviewed-release-branch>
longform-engine release check --repository . --channel public --json
git push origin master
```

完整制品发布应手工启动 CI workflow 并等待成功；失败时停止，不创建 tag。

## 4. 完整制品的不可变 Tag 与 GitHub Release

手工 CI 成功后执行：

```powershell
git tag -a v<VERSION> -m "longform-novel-engine v<VERSION>"
longform-engine release check --repository . --channel public --tag v<VERSION> --json
git push origin v<VERSION>
longform-engine release check --repository . --channel public --check-remote --tag v<VERSION> --json
```

Tag 推送不会自动构建制品。维护者需要完整 GitHub Release 时，手工启动 Release workflow；该 workflow 从指定 tag 构建 wheel/sdist、生成 `SHA256SUMS` 并上传三个制品。tag 不得移动或覆盖；发布后缺陷使用新的补丁版本。

## 5. 用户授权的 Tag-only 直接发布

用户逐次明确授权后，可以跳过本地验证、手工 CI、wheel/sdist 构建、分发审计、pipx smoke 和 GitHub Release：

```powershell
git add --all
git commit -m "release: publish v<VERSION>"
git switch master
git merge --ff-only <reviewed-release-branch>
git push origin master
git tag -a v<VERSION> -m "longform-novel-engine v<VERSION>"
git push origin v<VERSION>
```

该路径只发布不可变源码 tag，不产生 GitHub Release 页面、wheel、sdist 或 `SHA256SUMS`，也不表示兼容性、安装或语义依赖已经过本轮验证。发布记录和交接必须明确说明这些边界。

## 6. Release 证据与本机更新

完整制品路径以 Actions run、不可变 tag 和 Release assets 为发布事实；Tag-only 路径仅以远程不可变 tag 及其指向的 commit 为发布事实。两种路径都不追加发布后证据提交，也不移动 tag。

只有用户显式授权时才更新全局 CLI 与 Skill。从目标不可变 tag 安装 `[semantic]`，再执行 `longform-engine skills update --tool codex`。随后核对版本、资源 hash 与 doctor；不得更新用户未授权的其他宿主 Skill。
