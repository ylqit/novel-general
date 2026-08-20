# longform-novel-engine v0.6.0 实施与发布 Checklist

v0.6.0 将起点男频设为正式主合同，并增加不可绕过的人工可视化深审。它是一次不兼容的审稿协议升级：只接受 `human_story_review_v3` 和 `market_evidence_registry_v2`，旧项目不自动迁移。开发验证只使用当前源码环境；实现、测试和发布过程中均不得自动覆盖全局 CLI 或 Skill。

## 实施约束

- [x] 维护对象仅限 `longform-novel-engine/`，不以工作区其他目录作为能力证据。
- [x] 实施在当前项目工作区顺序执行，不创建 worktree，不使用 superpowers skill。
- [x] 正式策略固定为 `qidian_male` 主合同，`fanqie_free` 只产生 P2 非阻断观察。
- [x] `literary_evidence_ready` 继续只表示独立文学证据，不由作者人工接受替代。
- [x] 不自动安装或覆盖全局 CLI、Codex Skill、Claude Skill。

## 市场证据与质量合同

- [x] `market_evidence_registry_v2` 的每项证据声明 claim、来源类型、发布者、核验日期和适用范围。
- [x] 删除失效的单本起点荣誉页依赖，补充可核验阅文/起点官方资料、跨题材公开观察和番茄开篇/连载/数据反馈资料。
- [x] 平台经验全部保持 P2，不推断推荐算法，不覆盖跨平台 `chapter_contract_v3`。
- [x] 默认 `qidian_male` 主合同与 `fanqie_free` P2 兼容观察有回归测试。

## 人工深审 v3

- [x] `human_story_review_v3` 是唯一当前人工审稿协议，并绑定不可变 `review_bundle_sha256`。
- [x] 十项深审维度全部结构化校验，accept 必须十项全过。
- [x] accept 包含关键转折、人物选择/情绪、读者收益三类精确正文 span。
- [x] repair 至少包含一个结构化修改批注；redirect 明确回到章节方向或改纲。
- [x] 批注支持 P0/P1/P2 与 `preserve / expand_scene / compress / clarify / reorder / rewrite / replace_carrier`。
- [x] 任一候选、合同、承诺账本、因果模拟或 review bundle hash 漂移都会使旧决定失效。
- [x] repair/redirect 失败与故障注入保持工件和 SQLite 可回滚。

## 章节方向人工选择

- [x] 章节方向 Markdown 固定提供 2–3 个稳定 option ID。
- [x] `chapter_direction_selection_v1` 绑定方向文档 hash、选择、人工调整和重复载体理由。
- [x] 设计批准和语义编译同时消费 Markdown 与选择 sidecar；hash 漂移必须拒绝。

## 审稿咨询任务

- [x] 新增 `human_review_advisor` 与 `human_review_consult`，继续复用 `design_document_v1`。
- [x] 咨询输入只包含当前候选、Story Brief、冻结 review bundle、选中 span 和同候选历史咨询。
- [x] `consult-task → consult-validate → consult-record` 只写非 canonical 工单状态。
- [x] 咨询建议只能人工转换为批注，不能直接修改正文、批准章节或写入 final。
- [x] 同候选 hash 复用章节咨询会话；候选变化后旧咨询全部标记 stale。

## 本地可视化审稿台

- [x] `longform-engine review serve project.yaml --chapter N --port 8765 [--no-open]` 可启动三栏审稿台。
- [x] 服务只监听 `127.0.0.1`，使用一次性 token、Host/Origin 校验、严格 CSP、HTML 转义和预期 hash。
- [x] 左栏展示 Story Brief、合同和承诺；中栏展示正文、圈选批注和修复 diff；右栏展示独立 finding、平台观察、十项表单和咨询。
- [x] 网页/API 无路径穿越、XSS、CSRF、越权写 canonical 或并发丢失更新。
- [x] 人工改稿仅写入已验证 repair plan 对应的完整 repair 候选，以 `agent=human` 提交后重跑全部门禁和审稿屏障。

## 质量状态与验收

- [x] `longform-engine quality status project.yaml --json` 分别报告 `protocol_ready`、`author_acceptance_ready`、`literary_evidence_ready`。
- [x] `author_acceptance_ready` 仅在所有定稿章节均有绑定当前五类 hash 的 v3 accept 且无 P0/P1 时为 true。
- [x] 未完成完整人工深审的章节不能 finalize；所有正式章节均可从 accept 回读正文证据。
- [x] 人工亲改不能绕过 repair 预算或独立审稿，候选变更后完整复审。
- [x] 没有独立盲评证据时 `literary_evidence_ready=false`。

## 文档、版本与资源

- [x] 源码、包元数据、默认配置、资源清单和发布表面统一为 v0.6.0；发布提交的公开安装固定到不可变 v0.6.0 tag。
- [x] README、架构、存储、配置、门禁、流水线和 Skill 镜像说明同步新协议。
- [x] 不提交正文、API key、完整 Prompt 日志、模型、SQLite、缓存或构建目录。

## 本地验证证据

- [x] 市场证据、十项深审、三类 span、五类 hash 漂移、repair/redirect 回滚定向测试通过。
- [x] 咨询越权/stale、方向 sidecar、网页路径穿越/XSS/CSRF、项目锁/并发测试通过。
- [x] 人工改稿后全量复审、起点主合同、番茄 P2 非阻断测试通过。
- [x] 单进程完整测试套件、Skill/资源验证、`git diff --check` 通过。

2026-08-20 本地证据（全部退出码 0）：

- `python -m pytest -q -p no:xdist --tb=short`：`374 passed in 1621.72s`。随后类型收紧仅重写等价的 `expected_payoffs` narrowing，并以 v3 bundle/方向 sidecar 两项集成用例复核：`2 passed in 15.37s`。
- `python -m ruff check src tests scripts`：通过；`python -m mypy src/longform_engine/vector_backends.py src/longform_engine/chapter_contract.py src/longform_engine/storage/recovery.py`：通过。
- `scripts/validate_skills.py`、`sync_skill_references.py --check`、`build_resource_manifest.py --check`、`check_markdown_links.py`、`check_agent_data_pipeline_readiness.py`、`release_surface_guards.py`、`git diff --check`：通过；链接检查为 173 个文档、28 个本地链接，readiness 为 12/12 且 `literary_evidence_ready=false`。
- `release check --repository . --channel rc --json`：17 pass、2 个预期 warning、0 failure；warning 只说明全局已安装包仍为旧版且工作区尚未提交，未执行全局安装或覆盖。
- `python -m build` 生成 v0.6.0 wheel/sdist；`audit_wheel.py` 审计 172 项、`audit_sdist.py` 审计 257 项，均通过。构建目录保持 Git 忽略状态，不作为源码提交内容。

## 远程发布证据

- [ ] Pull Request CI 全平台通过。
- [ ] master CI 通过后创建不可变 annotated tag `v0.6.0`。
- [ ] GitHub Release workflow 完成 wheel、sdist、审计、远程 tag 校验与 `SHA256SUMS`。
- [ ] Release assets 可下载；完成前不得宣告 v0.6.0 已公开发布。
