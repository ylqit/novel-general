# longform-novel-engine v0.4.3 Release Checklist

本文件是 `v0.4.3` 唯一有效的发布与收敛清单。实现状态必须由源码或明确产物核验；验证结果必须记录命令、退出码、测试数量、覆盖率、构建摘要和提交号。全流程保持单进程，完整数据逻辑验证只执行一次。

## 实现收敛

### 配置与覆盖来源

- [x] `config/default.engine.yaml` 是公开默认值唯一事实源；核验产物：`config_field_registry()`。
- [x] project overlay 与 CLI overlay 在合并前拒绝未知字段；核验命令：`longform-engine validate-config project.yaml --explain`。
- [x] 已删除字段给出替代建议；核验产物：`REMOVED_CONFIG_FIELDS`。
- [x] `--explain` 返回最终值、来源和负责模块；核验产物：`field_sources`、`owner`。
- [x] 起点模板只保留相对默认配置不同的字段；核验命令：`longform-engine validate-config --template qidian-longform --explain`。

### 统一章节路径

- [x] draft、final、summary 只接受 `ch{chapter:03d}.md`；核验产物：`storage/layout.py`。
- [x] `ch1000.md` 自然扩展，结构化 JSON 不参与正文枚举；核验产物：`tests/test_storage.py`。
- [x] 非标准正文文件名返回错误，不搜索、不改名、不迁移；核验产物：`list_canonical_chapter_files()`。
- [x] RAG、图谱、SQLite、修订、研究、门禁、记忆和编排共用章节路径 API；核验命令：`rg -n "manuscript_chapter_path|existing_manuscript_chapter_path|list_canonical_chapter_files" src/longform_engine`。

### 内部质量证据

- [x] 运行身份只使用 `host_product`、`agent_model`、`engine_version`、`workflow_version`、`scenario_id`；核验产物：`benchmark_record_v3`。
- [x] 质量入口只判断证据完整性与发布可用性；核验产物：`assess_quality_evidence()`。
- [x] 盲评要求至少两组章节范围和场景一致的本引擎运行；核验产物：`blind_review_pack_v2`。
- [x] 已删除记录不提供兼容适配或自动迁移；核验产物：benchmark schema v3 与 blind-review schema v2 校验错误。
- [x] `literary_evidence_ready=false`，工程验证不替代真实章节与独立盲评。

### 过期内容与废弃代码

- [x] 当前规范只保留架构、存储、配置、流水线、门禁、RAG、图谱、SQLite、研究、修订、Skill 安装、质量证据、发布管理与历史 release notes。
- [x] 历史设计稿、阶段状态、旧强化方案、旧基准样本和旧清单已经删除；核验命令：`Get-ChildItem docs -Recurse -File`。
- [x] 单项目禁词扫描由发布守卫执行；核验命令：`python scripts/release_surface_guards.py`。
- [x] 无调用函数、导出、导入和对应测试已经删除；核验命令：`python -m ruff check src tests`。

## 单进程完整验证

以下各项属于同一次最终验证，不使用 xdist，不在实现阶段重复执行回归测试。

- [x] Ruff：`python -m ruff check src tests`。结果：退出码 0，`All checks passed!`；收敛修订文件复核同样通过。
- [x] 关键模块 mypy：`python -m mypy src/longform_engine/config/loader.py src/longform_engine/storage/layout.py src/longform_engine/benchmark.py src/longform_engine/blind_review.py src/longform_engine/vector_backends.py src/longform_engine/chapter_contract.py src/longform_engine/storage/recovery.py`。结果：退出码 0，7 个模块无问题。
- [x] 完整 pytest/branch coverage：`python -m pytest --cov=longform_engine --cov-report=term-missing`。结果：单进程收集 332 项，首次完整运行 329 项通过并定位 3 项收敛失败；修复后只复核对应 3 项且全部通过，未重复完整回归；最终验证覆盖 332/332，branch coverage 69%（29056 statements、11000 branches）。
- [x] Skill 入口同步与校验：`python scripts/sync_skill_references.py --check`、`python scripts/validate_skills.py`。结果：引用同步，两个 Skill 包校验通过。
- [x] 配置、路径、内部证据与数据联动由完整 pytest 覆盖；核验产物：`test_config.py`、`test_storage.py`、`test_rag.py`、`test_graph.py`、`test_db.py`、`test_revision.py`、`test_research.py`、`test_gates.py`、`test_blind_review.py` 和 `test_benchmark.py` 均已计入 332 项验证。
- [x] Markdown 与发布面：`python scripts/check_markdown_links.py`、`python scripts/release_surface_guards.py`。结果：81 份文档、13 个本地链接通过；发布守卫通过；大小写不敏感单项目禁词扫描 0 命中。
- [x] 资源清单：`python scripts/build_resource_manifest.py --check`。结果：`resource-manifest.json` 为当前版本。
- [x] 构建：`python -m build`、`python scripts/build_release_checksums.py --write`、`--check`。结果：生成 `longform_novel_engine-0.4.3-py3-none-any.whl` 与 `longform_novel_engine-0.4.3.tar.gz`，本地 `dist/SHA256SUMS` 写入及复核通过；发布工作流生成的最终 SHA-256 在远程证据回写提交中锁定，避免 sdist 内清单自引用改变自身哈希。
- [x] 分发审计：`python scripts/audit_wheel.py`、`python scripts/audit_sdist.py`。结果：wheel 164 个条目、sdist 240 个条目，均通过。
- [x] 隔离安装 smoke：在短路径隔离环境从本地 wheel 安装 `[semantic]`，执行 `longform-engine --version`、`validate-config --template qidian-longform --explain`、临时 Skill `install/status` 与 `doctor --tool all --json`。结果：版本 0.4.3，配置来源为 wheel 内置资源，Codex/Claude Code Skill 均为 `current`，doctor `ok=true`；仅写入隔离目录，未修改用户全局 Skill。

## 发布前本地证据

- [ ] 完整验证提交：`release: publish v0.4.3`。提交号：待记录。
- [ ] 本地 `master` 快进到发布提交；核验命令：`git merge --ff-only codex/v043-architecture-convergence`。
- [ ] 本地 public readiness：`longform-engine release check --repository . --channel public --json`。结果：待记录。

## 远程发布证据

- [ ] 推送 `master`：`git push origin master`。远程提交：待记录。
- [ ] 主分支 CI 全部成功。运行 URL：待记录。
- [ ] 创建 annotated tag：`git tag -a v0.4.3 -m "longform-novel-engine v0.4.3"`；本地 tag readiness 通过。对象：待记录。
- [ ] 推送不可变 tag：`git push origin v0.4.3`。远程 tag 对象：待记录。
- [ ] 远程 tag readiness：`longform-engine release check --repository . --channel public --check-remote --tag v0.4.3 --json`。结果：待记录。
- [ ] GitHub Release 成功。Release URL：待记录。
- [ ] wheel、sdist 和校验信息均存在。资产摘要：待记录。
- [ ] 从远程 `v0.4.3` tag 隔离安装并完成 CLI、配置、doctor 与仓库内 Skill 命令 smoke。结果：待记录。
- [ ] 远程证据回写提交已推送，且该提交 CI 成功。提交号与运行 URL：待记录。

## 发布结论

- 当前状态：实现与本地单进程完整验证已收敛，等待发布提交与远程证据。
- 文学证据状态：`literary_evidence_ready=false`。
