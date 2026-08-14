# v0.3.2 Operational Productization Checklist

状态约定：`[ ]` 未开始，`[~]` 已实现但证据不完整，`[x]` 已实现且验收证据完整。

本清单是 `v0.3.2` 的发布准入事实源。功能代码、隔离测试、真实生产和文学验收分开记录。2026-08-14 的发布决策将准入门收敛为五章工程 smoke；20 章生产与文学验收延期，不阻断协议与运维稳定版发布。现有 `v0.3.1` tag、Release 和失败运行不得覆盖或改写。

## Phase 0 - Baseline And Boundaries

- [x] 记录实现起点 commit、tag、dirty worktree 和本机 Engine/Skill 版本分裂。
- [x] 确认 `v0.3.0`、`v0.3.1` 失败运行只读保留，不用修复脚本篡改历史证据。
- [x] 确认发布前不得创建 `v0.3.2` tag 或 GitHub Release。
- [x] 确认真实小说正文、模型、SQLite、RAG 和 runtime 不进入 Git。
- [x] 确认不恢复脚本内 LLM，不自动批准 apply/finalize，不使用 `--force` 绕过 P0/P1。

## Phase 1 - Distribution Version Consistency

- [x] `doctor_v1` 同时报告 pyproject、module、distribution metadata、CLI 和已安装 Skill 版本。
- [x] 任一可比较版本不一致时 `doctor.ok=false`，并只给出一条可执行重装命令。
- [x] 源码树与 wheel 安装都能定位发布版本事实，不依赖用户当前工作目录。
- [x] 本地 RC wheel/sdist 版本统一为 `0.3.2`，资源 manifest 与构建审计通过。
- [x] 临时 `PIPX_HOME`、`PIPX_BIN_DIR`、用户目录和新 shell 安装 smoke 通过。
- [x] 任意新目录可运行 `open-book --interactive -> production next -> 执行唯一 next command -> agent-task brief`；`production next` 保持只读。

## Phase 2 - Transaction And SQLite Lifecycle

- [x] `ApplyTransaction` 写入 `canonical_write_transaction_report_v2`。
- [x] 报告可观察 `pending -> applied|rolled_back`，并记录 `cleanup_complete`。
- [x] 文件事务只快照实际 touched paths，不递归复制 `70_runtime/db`。
- [x] SQLite 使用原生事务完成单库写入。
- [x] 跨文件事务使用 SQLite `backup()` 分页备份作为 rollback participant。
- [x] 成功 commit 或成功 rollback 后立即清理文件快照和 DB 备份。
- [x] 仅 restore 失败时保留诊断副本，并在报告中标明。
- [x] doctor/artifacts status 报告 pending transaction、旧成功快照和可回收字节。
- [x] 受保护清理不会删除 rollback 失败证据。
- [x] transaction crash、rollback、cleanup 和两个 SQLite DB 故障恢复测试通过。

## Phase 3 - Shared Semantic Model Cache

- [x] Windows、macOS、Linux 默认缓存路径符合平台约定。
- [x] `models cache-status [--json]` 输出稳定 schema 和真实占用。
- [x] `models migrate project.yaml --to-shared --dry-run [--json]` 纯只读。
- [x] `models migrate project.yaml --to-shared --yes [--json]` 先复制或复用一致 profile、校验、写引用，再删除 legacy 副本。
- [x] 下载使用锁、staging、repo revision、文件清单和原子发布。
- [x] 项目只保存 `semantic_model_cache_ref_v1`，不复制 GB 级模型。
- [x] 显式绝对 `models_dir` 保持兼容；相对 `70_runtime/models` 标记为 legacy。
- [x] shared install、并发锁、损坏拒绝、引用、复用和迁移测试通过。
- [x] 隔离新项目只解析共享缓存或显式测试 profile；真实 SAO 的 `bge-m3`/no-fallback 证据留在 Phase 8。

## Phase 4 - Legacy Migration And Artifact Diagnostics

- [x] `legacy status` 输出 `legacy_migration_status_v1`。
- [x] status 报告 final/gate/ledger/closure 连续范围、blockers、orphan、快照和模型占用。
- [x] `legacy backfill` 每次只创建最早缺失章的 `chapter_semantic --backfill` 任务。
- [x] backfill 不自动 apply，不把摘要当事实，不污染 canonical。
- [x] `legacy compact --dry-run` 一次性验证整批且保持只读。
- [x] `legacy compact` 可自动创建带 migration 元数据的 closure，无需用户手工制造 closure。
- [x] legacy compact 幂等；任一章失败时整批不创建 closure、不删除 loose 文件。
- [x] `artifacts compact --dry-run` 报告 eligible、blockers、候选/唯一内容/可回收字节和 active buffer。
- [x] 实际 compact 遇到 blocker 时拒绝写入。
- [x] `artifacts verify` 区分 `ok|pending_close|migration_required|invalid`。
- [x] 缺 ledger、缺 gate、断章、重复执行、失败零污染和 migration fixture 测试通过。

## Phase 5 - Task Projection And Event Rotation

- [x] 写入 `agent_task_index_v2`，继续读取 v1。
- [x] index 只长期保留项目级、活动章、最近两章任务和终态聚合计数。
- [x] 写入 `agent_task_event_v2`，继续读取旧事件。
- [x] 已归档章节的完整 manifest 和事件进入对应 v3 审计包。
- [x] 活动 `events.jsonl` 不保留已归档章节事件。
- [x] 项目事件达到 5 MB 或 10,000 行时轮转 gzip，并生成 SHA-256 manifest。
- [x] `artifacts verify` 校验任务投影、事件段和审计包引用。
- [x] `production next` 只依赖活动投影，不扫描历史 ZIP。
- [x] 500 章 fixture 下 index 小于 1 MB、events 小于 5 MB，next 查询不线性退化。

## Phase 6 - Functional Completion Gate

- [x] 完整 pytest 通过：`416 passed`，含节奏双校验、归档 benchmark、真实规模 fanfiction design context 与 Humanizer semantic adapter fixture。
- [x] `python scripts/validate_skills.py` 通过。
- [x] `python scripts/release_surface_guards.py` 通过。
- [x] resource manifest、wheel audit 和 sdist audit 通过。
- [x] 临时 pipx 新终端完成 version、doctor、Skill install/status/update/uninstall/reinstall 和首个工作单 smoke。
- [x] Phase 1-5 全部为 `[x]`。
- [x] 本门完成前未删除旧 15 章项目，也未运行真实 SAO 20 章链路。

## Phase 7 - Authorized Legacy Project Deletion

目标目录固定为 `novels/benchmark-codex-original-phase6-current-v1`。

- [x] resolved path 位于本仓库 `novels/` 下且 basename 完全匹配。
- [x] 删除前恰好存在 15 个 final，数量不符立即停止。
- [x] 落盘不含正文的删除审计：文件数、字节数、配置 hash、15 个 final SHA-256 和 UTC 时间。
- [x] 不创建正文备份，不影响其他 benchmark 或 SAO 项目。
- [x] 删除后验证目标目录不存在。
- [x] Legacy 功能使用隔离 fixture 验收，不对该已删除项目补写 closure。

## Phase 8 - v0.3.2 RC Five-Chapter Engineering Smoke

项目：`novels/sao-aincrad-return-route-v032`；发布准入 Run ID：`codex-sao-aincrad-5-v032-smoke-v1`。原 20 章 Run ID 保持 incomplete，留待发布后继续。

- [x] 使用全新 v0.3.2 RC pipx 环境创建项目，不复用 v0.3.0/v0.3.1 失败 runtime。
- [x] 200 章、10 卷纲要连续且通过 validate/apply。
- [x] 第 1 章完成全链路后才继续，第 1-5 章均有真实 final、semantic ledger 和 closure。
- [x] 第 5 章 gate、payoff、pacing、四角色 editorial 与 final 绑定同一正文 SHA-256。
- [x] 节奏结果依次通过 Agent-first result validate、领域 validate 和显式 apply；重复 hash 保持幂等。
- [x] 第 5 章执行人物表现、反 AI 味、读者质量和同人 canon 四个独立编辑角色，aggregate 无 P0/P1。
- [x] P0/P1、canonical 污染和未解决 `need-human` 均为零。
- [x] chapter semantic 原子物化 graph、角色、伏笔、TCS、RAG 和 SQLite。
- [x] 第 1-3 章形成 3 个 v3 审计包，活动区仅保留第 4-5 章；`artifacts verify` 为 `ok`。
- [x] 成功事务残留快照为零，项目模型仅引用用户级共享缓存。
- [x] task index 为 79,645 bytes，活动 events 为 65,498 bytes / 142 行，均低于上限。
- [x] 真实 `bge-m3` 生成 24 条向量、0 stale、未使用 fallback；受控查询首命中第 5 章。
- [x] `benchmark validate/report` 为 complete/accepted，未填写文学评分；正文 source Merkle root 已记录。
- [x] `production next` 在第 5 章关闭后正确指向第 6 章。
- [x] `V032-P1-015` 与后续编辑上下文事故保留在 issue log，修复后完成闭环；未伪造“运行期间从未发生事故”。

非阻断限制：当前 CPU 环境中 `bge-m3` 首次五章向量查询约需 4.6 分钟。该证据证明真实模型链路可用，不代表已完成 500 章性能验收。

## Phase 9 - Human Literary Acceptance

- [ ] 用户已验收人物声音可辨识度。
- [ ] 用户已验收同人角色 OOC 与分歧因果。
- [ ] 用户已验收场景化叙事、节奏和 AI 味。
- [x] 未完成本阶段时不得标记 literary acceptance、不得声称文学质量领先；但不阻断 `v0.3.2` 协议与运维稳定版发布。

## Phase 10 - Immutable Release

- [x] 版本、README、安装文档、资源 manifest 和 release notes 统一为 `0.3.2`。
- [x] Git 只包含预期源码、测试、配置、文档和 workflow，不包含小说正文、模型、DB、RAG 或 runtime。
- [x] 最终 wheel/sdist 构建与审计通过：wheel 143 entries，sdist 272 entries。
- [x] 从最终 wheel 完成全新临时 pipx `[semantic]`、双 Skill、doctor 和新目录首个工作单 smoke。
- [x] 用户明确批准以五章工程 smoke 作为本次发布门槛。
- [ ] 创建 release commit 和不可变 `v0.3.2` tag。
- [ ] 推送 master/tag 并创建 GitHub Release，不移动 `v0.3.1`。
- [ ] 从 GitHub tag 重装本机 pipx，卸载旧 Anaconda editable 包并原子更新两个 Skill。
- [ ] `Get-Command`、pip metadata、module、CLI、doctor 和 Skill 全部报告 `0.3.2/current`。
