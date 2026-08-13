# v0.3.1 Agent 协议与章节状态机热修复 Checklist

本文档用于验收 v0.3.1 热修复。它不修改 AgentTaskManifest v2，不修改 Codex/Claude Code Skill 内容，也不把已失败的 v0.3.0 SAO 运行伪装成成功运行。

状态约定：`[ ]` 未完成，`[~]` 已实现但仍缺最终证据，`[x]` 已实现并有可重复验证。

## 1. v0.3.0 Failure Evidence

- [x] `novels/sao-aincrad-return-route-v030` 保持 incomplete，未原地修补 manifest、任务索引或正文。
- [x] `70_runtime/validation/v0.3.0_issues.jsonl` 保留 semantic review 9 输入/7 上限的 P1 记录。
- [x] 同一 issue log 保留旧 `chapter_write invalid` 抢占当前语义审稿阶段的 P1 记录。
- [x] v0.3.1 回归使用临时项目或全新隔离项目，不复用已发生状态分裂的 runtime。

## 2. Semantic Review Context

- [x] 原创与同人 `semantic_review` 均使用 `semantic_review_task.md + current draft + semantic_review_context.json`。
- [x] 常规任务实际输入为 3 个，严格上限仍为 7 个。
- [x] manifest 总输入字符上限为 18K，context packet 自身上限为 8K。
- [x] context packet 包含章节合同、TCS、当前 anchor、参与角色、关系、同人 continuity/divergence/voice/canon 投影。
- [x] 每个 canonical 来源记录路径、SHA-256、选择原因和分区裁剪报告。
- [x] `allowed_canonical_refs` 只用于限定 Agent 引用；CLI validate 会回读真实 canonical 文件核验。
- [x] 核心章节合同、当前状态、人物或同人上下文有内容却无法装入预算时，任务生成明确失败，不静默遗漏。

## 3. Manifest Registration

- [x] `write_manifest()` 在写 manifest、更新 index 和记录 lifecycle event 之前执行 strict validation。
- [x] strict validation 检查实际输入数量、文件存在性、普通文件类型、UTF-8 可读性和实际总字符数。
- [x] 无效 manifest 不写入 `agent_task_index.json`，不生成 create event。
- [x] 注册失败后遗留的非 canonical 工作单由 `artifacts status` 标为 orphan。
- [x] `production next` 再次严格校验旧 manifest，并返回 `agent_task_contract_invalid` 与 manifest 的失败命令。

## 4. Current Candidate Lifecycle

- [x] `chNNN.submission.json` v2 记录 candidate task/type/revision/source path/source hash/status 和 replaced task ids。
- [x] 提交路径必须唯一归属于一个活动正文候选任务；零匹配或多匹配均停止，不猜测归属。
- [x] repair、Humanizer 或 content-expand 成为当前候选后，旧正文候选统一进入 `superseded`。
- [x] 仅等待 semantic review 时当前候选保持 `submitted`，不会提前标记为 `validated`。
- [x] semantic pass 后当前候选进入 `validated`；P0/P1 或其他门禁失败后进入 `invalid`。
- [x] 缺少新增字段的 v0.3.0 submission 可按 source path 唯一推导；apply 后补齐当前候选投影。
- [x] blocking semantic review 的下一步是重新生成 repair candidate，不回跳到已 superseded 的 chapter write。

## 5. Stage-Derived Production Next

- [x] `derive_chapter_stage()` 按 closure、final、semantic ledger、gate、submission 和当前候选推导章节阶段。
- [x] 不存在的 `gate_result.json` 不再被空字典误判为 `repair_pending`。
- [x] `novel_state` 只作投影，不能覆盖 gate/submission/final 等权威事实。
- [x] `production next` 先解析章节阶段，再在该阶段允许的任务类型内选择动作。
- [x] `chapter_write invalid + repair submitted + semantic awaiting` 必须指向 `semantic_review`。
- [x] `production next` 为只读操作，调用前后项目文件哈希一致。

## 6. Atomic Apply And No Pollution

- [x] semantic apply 在同一事务中同步 gate、semantic task、当前候选、submission、chapter meta 与 novel state。
- [x] semantic apply 内部重跑 gate 时关闭 SQLite 同步。
- [x] pass/fail semantic apply 均不修改 final、RAG、story graph、TCS 或 SQLite。
- [x] invalid Agent output 继续不能进入 canonical state。
- [x] chapter finalize、graph/memory/semantic apply 的显式边界保持不变。

## 7. Compatibility And Context Cleanup

- [x] AgentTaskManifest schema 保持 v2，v1 继续规范化读取。
- [x] chapter direction 使用单章有界 context，不再把完整 200/500 章计划直传 Agent。
- [x] Humanizer、content-expand、editorial 和 character memory 不再声明缺失目录/文件或重复整份上下文。
- [x] 写作 brief 将已嵌入的 task JSON、character packet 和 fanfiction canon 标为 excluded duplicates。
- [x] Codex Skill tree hash 保持 `94784b55da6264ff2bd26edfca7180e3732d6b9f0506e2192705208a04d1673a`。
- [x] Claude Code Skill tree hash 保持 `d908d8a575977dffe7bd9d27b3591a7854d266fe4305ce375c647eb85ef2f681`。
- [x] 两个 Skill 目录相对 `v0.3.0` 无内容差异。

## 8. Automated Verification

- [x] v0.3.1 事故链专项与相关协议测试：17 passed。
- [x] 首轮完整 pytest 暴露 26 个严格契约兼容问题，未通过放宽校验掩盖。
- [x] 所有曾失败模块回归：141 passed。
- [x] 最终 `python -m pytest -q` 通过：`318 passed`。
- [x] `python scripts/sync_skill_references.py --check` 通过。
- [x] `python scripts/build_resource_manifest.py --check` 通过。
- [x] `python scripts/validate_skills.py` 通过。
- [x] `python scripts/release_surface_guards.py` 通过。
- [x] wheel/sdist 构建与审计通过：wheel 100 个条目，sdist 192 个条目。
- [x] 全新隔离 pipx/Python 3.12 安装可执行版本、资源、Skill lifecycle、doctor 和首个 `book_ideation` 严格工作单 smoke。
- [x] `release check --tag v0.3.1 --check-remote` 正确保持 BLOCKED：17 pass，失败项仅为 dirty worktree、尚无本地 tag、尚无远程 tag。

## 9. Runtime Replay And Release

- [ ] 使用本地 v0.3.1 wheel 创建全新 SAO 项目，不复用 v0.3.0 runtime。
- [ ] 第 1 章完成 write/repair/semantic/payoff/editorial/finalize/semantic bundle/close 全闭环。
- [ ] 新项目连续 5 章 smoke 通过。
- [ ] 新项目恢复 20 章生产并输出验证报告。
- [x] 获得用户明确发布确认后，已创建并推送 annotated `v0.3.1` tag，并由 Release workflow 创建 GitHub Release。
- [x] 发布后已从真实 tag 完成远程 pipx、Skill install/status、doctor、模板校验与新终端 smoke。

## Definition Of Done

- [x] 代码级协议缺陷已经修复并有组合回归；完整测试、构建审计与隔离安装均已通过。
- [ ] 只有第 1 章全闭环和 5 章 smoke 均通过后，才恢复 20 章 SAO 生产。
- [x] 已获得用户明确发布确认，并将验收通过的 release candidate 发布为公开 v0.3.1。
