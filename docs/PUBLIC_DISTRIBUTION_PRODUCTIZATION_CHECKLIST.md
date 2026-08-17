# Public Distribution And Intelligent Workflow Checklist

本文档是 `ylqit/novel-general` 从源码工程升级为可公开安装、可诊断、可维护 Skill 生命周期，并补齐项目级智能工作流的验收标准。

唯一公开发布源：`https://github.com/ylqit/novel-general`，仓库根目录即 engine 根目录，默认分支为 `master`。

当前源码包为 `v0.4.0`。该版本发布字数主导、滚动纲要与混合题材协议；两组真实五章盲评作为发布后质量证据继续延期。文学质量领先声明仍受同模型对照和独立盲评证据约束。

## 状态说明

- `[ ]` 尚未实现或尚未验证。
- `[~]` 已有实现基础，但仍缺真实环境、外部发布或质量证据。
- `[x]` 已实现，并有可重复执行的本地或 CI 验证方式。

## 1. Repository And License

- [x] 包版本升级为 `0.3.0`，运行时 `--version` 与包元数据一致。
- [x] 根目录存在 MIT `LICENSE`。
- [x] `pyproject.toml` 包含 README、许可证、真实仓库 URL、Issues URL 和 Python 版本元数据。
- [x] README、Skill 和安装提示只使用 `https://github.com/ylqit/novel-general`，不存在 `<owner>` 等占位地址。
- [x] `git ls-remote` 已确认公开仓库存在 `master` 和公开 `v0.3.0` tag。
- [x] `v0.3.0` tag 与 GitHub Release 只在用户明确确认和本地验收通过后创建。
- [x] 本地 `master` 已以不覆盖工作树的方式接回 `origin/master` 基线，`origin` 固定为公开仓库，变更可按正常 diff 审查。
- [x] `v0.3.0` 实现已审查、提交并推送到公开仓库 `master`。
- [x] `v0.3.0` annotated tag 和 GitHub Release 已公开发布。

## 2. Wheel Resources

- [x] 构建后 wheel 包含 `config/default.engine.yaml`。
- [x] wheel 包含 `templates/qidian-longform/` 全部模板资源。
- [x] wheel 包含 `longform-novel-codex/` 和 `longform-novel-claude/`。
- [x] wheel 内两个 Skill 均包含自己的 `references/`，运行时不依赖仓库外部相对路径。
- [x] wheel 包含资源哈希 manifest，并可校验配置、模板、Skill 和 references。
- [x] sdist 与 wheel 都不是只有 Python 模块的空资源包。
- [x] sdist 包含 README 所链接的 docs、AGENTS、CI/Release workflow、Skills、测试与源码，不产生断链源码包。
- [x] `audit_wheel.py` 与 `audit_sdist.py` 都支持从 `dist/` 自动发现唯一产物并检查必需内容。
- [x] 资源 manifest 的生成与 `--check` 漂移检测通过。

## 3. Installed Runtime

- [x] 源码运行优先读取仓库根资源。
- [x] wheel/pipx 安装使用 `importlib.resources` 定位资源。
- [x] 不再依赖 `Path(__file__).parents[3]` 推断仓库根。
- [x] 全新临时 pipx 环境可执行 `longform-engine --version`。
- [x] 已安装 wheel 可执行 `longform-engine validate-config --template qidian-longform`。
- [x] 已安装 wheel 可创建项目并生成首个 `production next` 结果。

## 4. Skill Lifecycle

- [x] `skills install --tool codex|claude-code|all` 使用安全 copy 安装内置 Skill。
- [x] `skills status --tool ...` 比较安装版本、文件哈希、安装元数据和引用完整性。
- [x] `skills update --tool ...` 只更新确认属于本项目的 Skill。
- [x] `skills uninstall --tool ... --yes` 只删除带合法安装元数据的目标目录。
- [x] 安装、更新使用同目录 staging、完整校验、原子替换和失败恢复。
- [x] `--force` 可迁移目标位置的 legacy Skill，但不会删除无法确认归属的全局 `shared/`。
- [x] 拒绝空路径、home、磁盘根、Skill root、仓库根和越界目标。
- [x] 安装内容只来自 wheel 内置 Skill，不包含小说正文、`.env`、API key、runtime DB、模型缓存或运行产物。
- [x] lifecycle 命令的 `--json` 输出满足稳定的 `skill_install_status_v1`。

## 5. Doctor And Semantic

- [x] `doctor --tool ... [--project ...]` 检查 CLI、资源、Skill、配置、Semantic 依赖和模型缓存。
- [x] `doctor --json` 输出满足稳定的 `doctor_v1`。
- [x] doctor 不自动下载模型。
- [x] 模型未就绪时报告可执行的 `longform-engine models install --download` 下一步。
- [x] README 默认安装命令安装 `longform-novel-engine[semantic]` 完整依赖。
- [x] 普通 Agent-Skill 工作流不要求 OpenAI、Anthropic 或 provider API key。

## 6. Self-Contained Skills

- [x] 两个 `SKILL.md` 各自不超过 500 词。
- [x] description 前 250 字符包含平台、中文长篇、`/工程下一步` 与 `production next` 触发信息。
- [x] Codex 与 Claude Code 平台触发描述互斥，不互相冒充。
- [x] Skill 内不存在 `../shared` 引用。
- [x] Skill 内不存在 `D:\\soft\\...`、`.venv` 激活命令或用户机器绝对路径。
- [x] 两个 Skill 默认流程都是 `production next -> agent-task brief -> Agent output -> validate -> explicit apply/finalize`。
- [x] references 同步脚本支持 `--check`，漂移时非零退出。
- [x] `validate_skills.py` 校验词数、引用、自包含、绝对路径、真实仓库 URL、资源哈希和跨 Skill 冲突。

## 7. README Experience

- [x] README 只有一个公开安装区，不新增 `README.zh-CN.md`。
- [x] PowerShell 和 Bash 都以 pipx + Git tag URL 为默认安装方式。
- [x] README 命令能让当前 shell 找到 `PIPX_BIN_DIR`。
- [x] Agent 对话安装 prompt 使用真实仓库 URL，不要求手动 clone、临时目录或 editable install。
- [x] README 提供安装、doctor、升级、卸载和重启 Skill discovery 的命令。
- [x] README 提供 `/工程下一步`、`/工程工单` 和首个工作单示例。
- [x] README 明确 final/RAG/graph/TCS/SQLite 以及 Bible/outline/research canon 的硬边界。
- [x] 开发者 clone/venv/junction/symlink 说明位于 README 后半部分。
- [x] 10 章证据完成前，README 不宣称文学质量优于 `novel-skill`。

## 8. AgentTaskManifest v4

- [x] v1 manifest 继续可读取，并规范化为统一内部表示。
- [x] v2 支持 `scope.kind = project|chapter|range` 及对应参数。
- [x] v2 包含 `canonical_targets` 和 `requires_human_apply`。
- [x] 新增 `no bible direct`、`no outline direct`、`no research canon direct` 硬边界。
- [x] `book_design` 具有 task、strict validate、explicit apply、failure command 和工作单角色说明。
- [x] `outline_design` 具有 task、strict validate、explicit apply、failure command 和工作单角色说明。
- [x] `outline_revision` 具有 range scope、impact/stale 标记和事务 apply。
- [x] `research_synthesis` 校验引用后才能显式 apply 到 research canon。
- [x] `style_analysis` 将语义档案与统计指纹合并，不覆盖未声明字段。
- [x] `adaptation_analysis` 只保存结构与技法，不保存或复制样章正文。
- [x] 开书和改纲 apply 默认要求人工确认。
- [x] 六类任务的 invalid output、越界路径和 apply 失败均不污染 canonical state。
- [x] `production next` 能按优先级暴露六类项目级或范围级任务。

## 9. Quality Evidence

- [x] `benchmark init` 创建 `70_runtime/benchmarks/<run_id>/` 运行记录，不调用 LLM。
- [x] `benchmark validate` 校验章节数、Agent 产品和固定质量指标。
- [x] `benchmark report` 汇总门禁失败率、返修次数、need-human 次数、P0/canonical 污染、上下文文件数和字符数。
- [x] `benchmark record` 通过 CLI 原子记录六项 1-10 分、gate、repair、need-human、上下文规模、三名评审和短问题标签，无需手工编辑 JSON。
- [x] `benchmark compare` 只正式比较相同 `scenario-id`、相同章节数且已完成的两组或多组运行。
- [x] provisional 对比必须显式使用 `--allow-incomplete`，不能冒充完成的质量证据。
- [x] benchmark 初始化记录 Agent 模型与宿主版本标签，支持复现实验环境。
- [x] `docs/QUALITY_BENCHMARK_RUNBOOK.md` 固化盲评、公平性、5 章 smoke、10 章 quality 与三方 baseline 流程。
- [x] 指标包含连贯性、角色一致性、伏笔控制、节奏、读者收益和 AI 味。
- [x] Codex 真实原创 5 章生产 smoke 已按当前 mandatory `book_ideation` 协议完成：五章 PASS/final、章节 1/3 语义审查、零 P0、零残留 canonical 污染，且 `benchmark acceptance_passed=true`。证据见 `docs/benchmarks/PHASE6_EXECUTION_STATUS.md`。
- [x] Codex 从 tagged GitHub release 执行 pipx 安装、Skill install/status 与 doctor 的公开安装复现已通过。
- [~] Codex App 重启后的新任务 Skill discovery 与再次五章生产仍保留为宿主侧验收，不把安装 smoke 冒充生产重放。
- [~] Claude Code 真实 5 章安装/生产 smoke 尚未运行；当前只完成工具链 smoke。
- [~] 同一设定的 Codex 与 Claude Code 真实 10 章对比尚未运行。
- [~] `novel-skill` baseline 本轮未运行；等待同一设定 10 章实验时一并记录。
- [x] benchmark schema 拒绝正文字段，目录也被 `.gitignore` 排除。

## 10. Release Guards

- [x] `release_surface_guards.py` 继续阻止 `src/` 内脚本 LLM/provider 调用。
- [x] 默认配置不含 OpenAI/provider 模型占位和 hidden API key 要求。
- [x] `api_provider` 不作为公开可选运行模式，未实现能力不会晚失败。
- [x] invalid Agent output、feedback digest 和 benchmark 记录不污染 final/RAG/graph/TCS/SQLite。
- [x] release guard 固定检查 benchmark 无正文/canonical 引用，并阻止 readiness 执行 Git commit/push/tag/reset 等变更命令。
- [x] 项目级任务不得由 Agent 直接写 Bible、outline 或 research canon。
- [x] 现有 no-pollution E2E 全部通过。

## 10A. novel-skill Superiority Evidence

- [x] 详细工程、效率与文学证据门槛已独立落盘到 `docs/NOVEL_SKILL_SUPERIORITY_CHECKLIST.md`。
- [x] 项目就绪门、上下文预算、持久化向量候选、双层语义门禁和自动 claim gate 已有测试证据。
- [~] 当前协议 Codex 原创 5 章已通过零污染 acceptance；Claude Code 5 章、同模型 10 章 novel-skill baseline 和 500 章 production-model RAG 测量仍待执行。
- [x] 上述真实证据完成前，公开 README 不宣称文学质量已经全面超越。

## 11. CI And Release

- [x] GitHub Actions 的 Windows/Ubuntu + Python 3.10/3.11/3.12 矩阵已远程运行通过。
- [x] CI 构建 wheel/sdist 并审计必需资源的远程运行已通过。
- [x] CI 的安装后 CLI、模板、Skill 和 doctor smoke 已通过。
- [x] CI 的 pytest、`validate_skills.py` 和 `release_surface_guards.py` 已通过。
- [x] CI 与 Release workflow 不再硬编码 wheel 版本文件名，后续升级版本不会因旧文件名直接失败。
- [x] CI 与 Release workflow 同时审计 wheel 和 sdist，不只验证 Python 模块是否可导入。
- [x] `release check` 输出稳定的 `release_readiness_v1`，检查版本/tag、Git、origin、README、资源、Skill 与发布防线，但不执行发布动作。
- [x] Release workflow 可从 `v*` tag 构建并附加 wheel/sdist。
- [x] macOS 3.11 pipx wheel、Skill install/status/doctor/uninstall smoke 已在 CI 通过。
- [x] GitHub tag/release 推送只在本地验收通过并得到明确确认后执行。

## 12. Definition Of Done

- [x] README 的真实 `v0.3.0` tag pipx 命令已在全新隔离环境完成远程复制安装验收。
- [x] 发布 readiness 现在能机器识别本地无 commit、dirty worktree、缺少 origin 和 tag/version 不一致等阻断条件。
- [~] Skill 已安装到临时 Codex/Claude Code roots；真实宿主重启与 discovery 待人工验收。
- [x] `longform-engine doctor --tool all` 给出明确、可执行的诊断结果。
- [x] 用户无需 provider API key 即可开书、查看 `production next` 并生成首个工作单。
- [x] 每个 Agent 任务都有明确输入、允许输出、schema、validate、apply、failure command 和硬边界。
- [x] 本地完整测试、wheel smoke、安装生命周期 smoke 和 no-pollution 验收通过。
- [x] Codex 源码树已按当前协议完成 5 章 smoke，且第 6 章 next action 正确。
- [~] 远程 Release 已完成；Claude Code、同人和正式 10 章盲评仍未完成，也没有用代码测试替代文学证据。

## 13. v0.3.1 Protocol Hotfix

- [x] 新增 `docs/V0_3_1_PROTOCOL_HOTFIX_CHECKLIST.md`，单独记录语义上下文、manifest 注册、候选生命周期和章节阶段验收。
- [x] 包版本与运行时版本已统一提升并公开发布为 `0.3.1`。
- [x] v0.3.0 SAO 失败运行和两项 P1 issue 保持原样，未原地修改。
- [x] Codex/Claude Code Skill 内容与 v0.3.0 完全一致。
- [x] 本地协议与兼容测试、完整 pytest、构建审计及隔离 wheel/pipx 安装 smoke 已通过。
- [ ] 全新 v0.3.1 SAO 项目完成第 1 章全闭环与 5 章 smoke。
- [x] 获得明确发布确认后，annotated `v0.3.1` tag、GitHub Release、wheel 与 sdist 已公开发布。

## 14. v0.3.2 Protocol And Operations Release

- [x] Agent-first Prompt 角色、结果规范化、数据链路 readiness、事务清理、共享模型缓存和 v3 审计包完成实现。
- [x] 节奏审稿控制面与领域校验绑定同一当前任务、结果路径、SHA-256、诊断文件和生命周期。
- [x] 真实 Codex SAO 第 1-5 章均有 final、semantic ledger 和 closure，`production next` 指向第 6 章。
- [x] 五章 benchmark complete/accepted，P0 与 canonical 污染为零，未填写文学评分。
- [x] 真实共享 `bge-m3` 建立 24 条向量并完成无 fallback 查询；CPU 冷启动性能限制已披露。
- [x] 20 章生产和人工文学验收明确延期，不作为本次发布阻断项。
- [x] README 与 Release notes 不声称文学质量优于 `novel-skill`，也不保证旧项目数据兼容。
- [ ] `v0.3.2` 最终构建、全新 pipx smoke、主分支 CI、annotated tag、Release 资产和远程 tag 重装完成。

## 本轮本地验收记录

- `python -m pytest -q`：318 passed。
- `python scripts/sync_skill_references.py --check`：passed。
- `python scripts/build_resource_manifest.py --check`：passed。
- `python scripts/validate_skills.py`：passed。
- `python scripts/release_surface_guards.py`：passed。
- `python -m build`：从最终源码成功构建 `longform_novel_engine-0.3.1.tar.gz` 与 wheel。
- `python scripts/audit_wheel.py`：100 entries，必需配置、模板、Skills、references 与资源 manifest 齐全。
- `python scripts/audit_sdist.py`：192 entries，文档、workflow、Skills、源码与测试齐全。
- v0.3.1 全新隔离 pipx/Python 3.12 `[semantic]` 安装：`--version=0.3.1`、模板校验、双 Skill install/status、`doctor_v1.ok=true` 与首个严格 `book_ideation` 工作单通过。
- v0.3.0 SAO 项目只读兼容检查：`production next` 返回 `agent_task_contract_invalid -> semantic_review` 与重新生成命令；201 个文件前后 SHA-256 完全一致。
- v0.3.1 `release check --tag v0.3.1 --check-remote`：17 pass、0 warning、3 expected failures（dirty worktree、本地无 tag、远程无 tag），未执行任何发布动作。
- v0.3.1 发布后 readiness：20 pass、0 warning、0 failure；Release workflow 与主分支 CI 均为 success。
- GitHub Release：`https://github.com/ylqit/novel-general/releases/tag/v0.3.1`。
- v0.3.1 wheel SHA-256：`e52ed4eccb3ff4753c2bdf97f1b1d1ca8d6c8c9ab0dc01443948dd9646dfe538`；sdist SHA-256：`837fed90de589fed93eda8f8930f05cff68749c2cbfb55448a69bff421f03d45`。
- 真实 v0.3.1 tag 的隔离 pipx `[semantic]` 安装、双 Skill install/status、doctor 与模板校验通过。
- 全新隔离 pipx `[semantic]` 安装：`--version=0.3.0`、模板校验、Skill install/status/update/uninstall、`doctor_v1` 和首个 `book_ideation` 工作单通过。
- 安装态模型验证：`bge-m3` embedding/reranker 均可加载，`fallback_active=false`；项目 doctor 为 green。
- `codex-longform-phase6-smoke-5-current-v1`：5 个 final、5 个 PASS gate、1 次候选返修、1/3 章强制语义审查、平均上下文 7 个文件 / 12,608 字符、P0 为 0、残留 canonical 污染为 0。
- 每章技术记录包含 work order、manifest、reviewed draft 与 gate SHA-256；五章 final 来源 Merkle root 已落盘。
- 用户 Codex Skill 已更新为 `0.3.0`；宿主 App 重启后的 discovery 仍需人工确认。
- `longform-engine release check --repository . --tag v0.3.0 --check-remote --json`：发布前 17 pass、0 warning、0 failure。
- GitHub Actions 主分支矩阵 8 个 job 全部通过；Release workflow 恢复运行成功并发布 wheel/sdist。
- GitHub Release：`https://github.com/ylqit/novel-general/releases/tag/v0.3.0`。
- Release 资产：wheel SHA-256 `e71266e0ef0d9411109ee0b157faefd128f0e119b4ee7ba77893445eb40de7df`；sdist SHA-256 `3b2635a49c1027bf16a636f7c9f972e0e19d72cfeb0da5cea4b1fcdf2d0d62ff`。
- 真实 Git tag pipx `[semantic]` 安装、Codex/Claude Code Skill 生命周期和 doctor 已在隔离目录复现通过。
- 尚未执行 Claude Code 5/10 章生产、同人 smoke、10 章对照或独立盲评；macOS 已完成 CI 主机 smoke，尚无 Claude Code 图形宿主人工验收。

## 推荐验证命令

```powershell
python scripts/sync_skill_references.py --check
python scripts/build_resource_manifest.py --check
python scripts/validate_skills.py
python scripts/release_surface_guards.py
python -m pytest
python -m build
python scripts/audit_wheel.py
python scripts/audit_sdist.py
longform-engine --version
longform-engine skills status --tool all --json
longform-engine doctor --tool all --json
longform-engine release check --repository . --check-remote --json
```
