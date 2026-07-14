# Novel-Skill 能力补齐 Checklist

本文档用于把 `novel-skill` 已经成熟的“小说生产现场能力”分阶段补进 `longform-novel-engine`。它不是把旧脚本原样搬迁，而是把能力拆成可验收的 CLI、落盘产物、门禁规则和测试契约。

## 工程边界

在实现以下任何能力前，必须先满足这些边界：

- 默认生产路径仍是 `writing.mode = agent_skill`，不要求用户提供额外 OpenAI、Anthropic 或其他 provider API key。
- Agent 正文草稿只允许写入 `50_workbench/agent_drafts/`。
- 扩写候选、润色候选、修复候选、审稿输出和研究 inbox 都只能作为 workbench 产物存在。
- 所有候选正文必须经过 `draft submit -> gate-check -> chapter finalize`，不得直接进入 final。
- 只有 `chapter finalize` 可以写入 `40_manuscript/final/` 并刷新 RAG、story graph、memory 或 SQLite。
- 失败稿、短章修复稿、humanizer 候选稿、审稿稿和 research inbox 不得污染 `60_rag/`、`30_state/story_graph.json` 或 `70_runtime/db/`。
- 新增命令必须保留 `longform` 的中文工程指令作为主入口，不把 `novel-skill` 旧命令名暴露为主要用户协议。

## Status Legend

- `[ ]` 未开始。
- `[~]` 已有雏形，但未达到验收标准。
- `[x]` 已实现，并有测试或可重复验收步骤覆盖。

## Implementation Checklist

### [x] 1. 自动写书调度

目标：把 `novel-skill` 的 one-click / continue-write 实用调度能力迁入 `longform`，但只负责调度和状态推进，不绕过 Agent 草稿边界。

需要新增或扩展：

- [x] CLI 工作流：`auto-write plan`、`auto-write run`、`auto-write progress`、`auto-write report`。
- [x] 状态文件：`70_runtime/auto_write_state.json`。
- [x] 状态字段：目标字数、目标章节、当前章节、连续失败次数、最近失败原因、暂停原因、下一步建议命令、最后一次成功 final 章节。
- [x] 调度行为：默认只调用 `continue-write`、生成 Agent 草稿任务、等待 `draft submit` 和 `chapter finalize`。
- [x] 阻断规则：上一章未 final、上一章 gate 失败、RAG/outline stale、锁文件存在、人工暂停标记存在时必须暂停。

验收标准：

- [x] 断点恢复后能从 `auto_write_state.json` 找到下一步。
- [x] 门禁失败时自动暂停，并写明可执行修复命令。
- [x] 上一章未 final 时不能生成下一章正式任务。
- [x] `auto-write progress/report` 对非工程用户可读。
- [x] 测试覆盖 resume、pause-on-gate-failure、previous-not-final blocker 和 report artifact。

实现记录：

- Orchestration：`auto_write_plan`、`auto_write_run`、`auto_write_progress`、`auto_write_report`。
- CLI：`longform-engine auto-write plan/run/progress/report project.yaml`。
- 状态：`70_runtime/auto_write_state.json`。
- 报告：`70_runtime/run_reports/auto_write_plan.json`、`auto_write_run_*.json`、`auto_write_report.md`。
- 边界：`auto-write run` 在 `agent_skill` 模式下只生成 `continue-write` 任务包并暂停等待 Agent 草稿；不会写 final、RAG、story graph、memory 或 SQLite final rows。

### [x] 2. 动态草稿与 Beat 扩写

目标：把现有章节任务包升级为真正能指导 Codex / ClaudeCode 写作的“可写作 brief”，接近 `novel-skill` 的动态草稿生成体验。

需要新增或扩展：

- [x] 增强 `continue-write` 或新增 `draft-task` 子流程。
- [x] 写作 brief 字段：阶段、章节职责、节奏档位、场景入口、情绪推进、人物关系变化、章末钩子、禁止揭露、不得解决事项。
- [x] Beat 扩写要求：每个 Beat 的场景目标、冲突点、信息释放量、可扩写方向和禁止重复点。
- [x] 约束注入：RAG 摘要、story graph facts、TCS 时间状态、角色记忆、outline anchors、当前风格档案、事件矩阵约束。
- [x] 可选 `template_dry_run` 仅用于测试或演示，生成正文后仍必须走 gate。

验收标准：

- [x] 任务包同时包含阶段策略、Beat 扩写要求和禁区说明。
- [x] Codex 读取任务包即可知道本章“写什么、不能写什么、最后钩子是什么”。
- [x] 任务包引用 RAG、图谱、TCS、角色记忆、事件矩阵和风格档案。
- [x] dry-run 产物不能绕过 `draft submit`。

实现记录：

- `continue-write` 任务包新增 `writing_brief`、`beat_expansion_requirements`、`constraint_packet`。
- `writing_brief` 包含 serial stage、stage strategy、chapter duty、pacing tier、scene entry、chapter hook、forbidden reveals、do-not-resolve、must-preserve-suspense。
- Beat Sheet 每个 Beat 增加 scene goal、conflict point、information release、scene/dialogue/psychology/action/transition 扩写要求、avoid repetition、forbidden reveals、preserved suspense。
- `constraint_packet` 统一注入 RAG、story graph facts、TCS、Character Memory/character state、outline anchor、event matrix、current style profile、research canon 和 forbidden zones。
- Markdown 写作任务同步显示 Writable Brief、Beat Expansion Requirements 和 Constraint Packet。

### [x] 3. 内容扩写引擎

目标：在短章、场景不足、对白单薄或转场生硬时生成可执行扩写任务，而不是直接改 final。

需要新增或扩展：

- [x] CLI 工作流：`creative expand-task`、`creative expand-check`。
- [x] 扩写类型：场景扩写、对白补强、心理深化、动作细化、过渡平滑。
- [x] 任务输入：gate 失败报告、短章统计、repair plan、writing task / writable brief 快照。
- [x] 候选落盘：`50_workbench/repair_candidates/` 或 `50_workbench/agent_drafts/`。
- [x] 检查输出：扩写是否解决短章、是否留在 workbench 候选区、是否具备五类扩写证据、是否残留 P0/P1 Humanizer 问题。

验收标准：

- [x] 短章失败后能生成扩写任务和候选写作说明。
- [x] 扩写候选通过 `creative expand-check` 后，才允许走 `draft submit --overwrite`。
- [x] 扩写候选不得直接写入 final、RAG、graph、memory 或 SQLite。
- [x] 测试覆盖 scene/dialogue/psychology/action/transition 五类任务。

实现记录：

- Creative pipeline：新增 `expand_task`、`expand_check`、`ExpandTaskResult`、`ExpandCheckResult`。
- CLI：新增 `longform-engine creative expand-task project.yaml --chapter N` 与 `longform-engine creative expand-check project.yaml --chapter N --file ...`。
- 产物：`50_workbench/repair_candidates/chNNN.expand_task.md`、`chNNN.expanded_candidate.md`、`chNNN.expand_check.json`、`chNNN.expand_check.md`。
- 边界：`expand-check` 对候选路径做白名单校验，只接受 `50_workbench/repair_candidates/` 或 `50_workbench/agent_drafts/`；通过时只给出 `draft submit --overwrite` 下一步命令，不写 final/RAG/graph/memory/SQLite。
- 测试：覆盖短章 gate 失败后的扩写任务、五类扩写证据、CLI 调用、no-pollution 断言和 Windows `sys.executable` 子进程路径。

### [x] 4. 样章风格提取与风格库

目标：让 `longform` 拥有可复用的样章风格档案，供写作任务包和 gate 共同使用。

需要新增或扩展：

- [x] 样章风格提取命令：`creative style-extract`。
- [x] 风格指标：句长分布、段落长度、对白比例、标点密度、视角、常用短语、动作偏好、节奏标签、叙述密度。
- [x] 项目内风格库：`10_bible/style_profiles/`。
- [x] 当前生效风格：`10_bible/style_profiles/current_style_profile.json`。
- [x] 可选跨项目风格库输入：`--library`，并记录样章来源、项目名、时间戳和 source hash。
- [x] gate 检查明显风格漂移，输出 `style_drift` P2/P1，并在 `style_review.md` 和 `repair_plan.md` 中给出修复方向。

验收标准：

- [x] 样章输入能生成结构化风格档案。
- [x] `continue-write` 任务包能引用当前生效风格。
- [x] gate 能检测明显风格漂移，并给出可修复建议。
- [x] 风格档案记录来源，不混淆不同项目样章。

实现记录：

- Creative pipeline：新增 `style_extract`、`StyleExtractResult`、样章风格指纹提取器和风格库索引写入。
- CLI：新增 `longform-engine creative style-extract project.yaml --file sample.md --name NAME --source-project SOURCE`；支持多 `--file` 和可选 `--library`。
- 产物：`10_bible/style_profiles/{name}.sample_profile.json`、`10_bible/style_profiles/style_library.json`、`10_bible/style_profiles/current_style_profile.json`。
- 指标：句长/段长统计、对白比例、标点密度、POV、常用短语、动作偏好、节奏标签、叙述密度。
- 写作注入：`continue-write` 的 `style_context` / `constraint_packet.style_profile` 读取当前样章 profile 的 `profile.fingerprint`。
- Gate：`gate-check` 对当前样章 profile 做 `style_drift` 判断，P2 可人工确认，P1 阻断；`style_review.md` 写入 Active Style Baseline 和 Drift Issues。
- 测试：覆盖样章提取、CLI JSON、当前风格注入、明显风格漂移、来源记录。

### [x] 5. 中文 Humanizer 深化

目标：把现有 AI 痕迹检查升级为更适合中文网文的两遍式润色与检测任务。

需要新增或扩展：

- [x] 中文 AI 痕迹库：高频词、弱化副词、套话动作、意义膨胀、总结腔、等长句、模板三连、TODO/占位符。
- [x] `humanize-task`：输出两遍式中文润色指令。
- [x] 第一遍：删除空泛解释、压缩总结腔、替换模板动作、降低副词堆叠。
- [x] 第二遍：补具体动作、补感官细节、调整句长节奏、增强对白差异。
- [x] `humanize-check`：输出问题分类、严重级别、证据片段和修复建议。

验收标准：

- [x] 文本包含“仿佛”“不禁”“意义深远”“嘴角微扬”“TODO”等模式时触发对应 P0/P1/P2 或 warning。
- [x] humanizer 只生成任务、报告或候选稿，不直接写 final。
- [x] gate 能引用 humanizer 报告，并把 P0/P1 纳入失败路径。
- [x] 测试覆盖高频词、弱化副词、套话动作、意义膨胀、总结腔、等长句和模板三连。

实现记录：

- Creative pipeline：`humanizer_rules()` 升级为中文两遍式；新增中文网文 AI 痕迹库、`humanizer_issue_summary`、分类证据片段和修复建议。
- 检测类别：TODO/占位符、意义膨胀、总结腔、套话动作、高频词、弱化副词、模板三连、等长句，同时保留原有英文通用 AI diction 和重复段落检测。
- `creative humanize-task`：任务文件写入“Pass 1: 中文 AI 痕迹清理”“Pass 2: 中文网文质感增强”和中文 issue catalog。
- `creative humanize-check`：JSON 和 Markdown 输出 `category`、`severity`、`evidence`、`suggestion`、`issue_summary`；通过后才给 `draft submit --overwrite`。
- Gate：`gate-check` 复用 `detect_humanizer_v2_issues`；P0/P1 进入失败路径，`humanize_report.md` 写入同一套 issues/warnings，`repair_plan.md` 指向 `creative humanize-task`。
- 边界：Humanizer 仍只写 `50_workbench/humanizer_tasks/` 与 `50_workbench/repair_candidates/`，不写 final/RAG/graph/memory/SQLite。
- 测试：覆盖中文类别触发、等长句、gate P1 阻断、CLI JSON 和 no-pollution。

### [x] 6. 事件矩阵与节奏追踪

目标：把章节级 pacing 从弱提示升级为可追踪的事件矩阵，避免连续同质冲突或长期缺少柔和事件。

需要新增或扩展：

- 正式事件类型池：`conflict_thrill`、`bond_deepening`、`faction_building`、`world_painting`、`tension_escalation`。
- 事件冷却：同类事件连续出现时给出 warning/P2/P1。
- 柔和事件要求：每 5 章至少出现一次 `bond_deepening`、`faction_building` 或 `world_painting`。
- 快档限制：连续快档章节上限、卷内快档配额、高潮前缓冲章要求。
- 状态产物：事件矩阵、卷内节奏统计、最近 5 章节奏摘要。
- `plan-chapter` 和 `gate-check` 同时读取事件矩阵，前者生成约束，后者校验落稿。

验收标准：

- 连续冲突或连续快档会被 warning 或阻断。
- 快档超出卷内配额时 gate 失败。
- 柔和事件长期缺失时，下一章任务包生成明确写作约束。
- 测试覆盖 event cooldown、soft event gap、fast quota、plan/gate consistency。

实现记录：
- Planning：正式事件类型池为 `conflict_thrill`、`bond_deepening`、`faction_building`、`world_painting`、`tension_escalation`。
- State：`30_state/event_matrix.json` 记录 schema version、cooldown、latest recommendation、recent 5 chapter summary、soft-event window 和 volume fast usage；`30_state/pacing_history.json` 记录定稿事件类型与快档配额使用。
- `plan-chapter`：章节卡和 Markdown 写入推荐事件、冷却阻断事件、柔和事件要求、快档配额状态和矩阵约束。
- `continue-write`：在 `constraint_packet.event_matrix` 注入 recommended/blocked、constraints、soft-event requirement、recent summary、fast quota 和正式类型池。
- `gate-check`：`pacing_review` 用同一事件矩阵校验草稿检测到的事件类型；快档 cooldown/streak/quota 产生 P1，柔和事件缺口产生 warning/constraint。
- Tests：新增 soft-event gap planning、event cooldown + fast quota gate failure、soft-event gap gate warning、writing-task constraint packet consistency 覆盖。

### [x] 7. 反向刹车升级

目标：把“不要提前解决核心矛盾”从隐性经验升级为显式报告与 gate 项。

需要新增或扩展：

- 显式报告项：章末悬念、核心矛盾提前解决、禁揭露、A/B/C 剧情加速配额、主线信息释放量。
- `continue-write` 任务包必须包含“本章不得解决什么”和“必须保留什么悬念”。
- outline anchors 增加或标准化：`forbidden_reveals`、`resolution_markers`、`requires_tail_suspense`、`allowed_reveal_level`。
- gate 检查非终局章节是否完整揭露核心秘密、是否关闭本卷主悬念、是否缺少章末钩子。

验收标准：

- 非终局章节出现核心秘密完整揭露时 gate 产生 P1。
- 缺少章末钩子时产生 warning 或 P2。
- 写作任务包明确列出 forbidden reveals 和 retained suspense。
- 测试覆盖 premature resolution、forbidden reveal、missing tail hook、A/B/C quota。

实现记录：
- Chapter Card：`plan-chapter` 标准化写入 `reverse_brake`、`forbidden_reveals`、`resolution_markers`、`requires_tail_suspense`、`allowed_reveal_level` 和 `must_preserve_suspense`。
- Writing Task：`continue-write` 的 `writing_brief` 与 Markdown 增加 `Reverse Brake` 区块，明确 `this_chapter_must_not_solve` 与 `must_keep_suspense`。
- Constraint Packet：新增 `constraint_packet.reverse_brake`，让 Agent 写稿前可同时读取禁揭露、不得解决项、章末悬念和 A/B/C 配额。
- Gate：新增 `reverse_brake_report.md`，显式报告 forbidden reveals、core resolution markers、complete core secret reveal、A/B/C quota、mainline information release 和 tail suspense。
- Gate failures：非终局完整核心秘密揭露、禁揭露命中、提前解决、强制章末悬念缺失、A/B/C 超配额均进入 P1；普通章末悬念弱保持 warning。
- Tests：新增 complete core secret reveal、required tail suspense、A/B/C quota overflow，并扩展 writing task reverse-brake 注入断言。

### [x] 8. 创作协议增强

目标：把 `novel-skill` 的创作协议优势收敛进 `longform` 的中文工程命令，而不是新增一套平行旧协议。

需要新增或扩展：

- 在 `shared/creative_operator_protocol.md` 或新的 shared 文档中补充 `/工程续章` 写前引导。
- 写前引导包含：用户偏好、自动兜底、节奏预检、章末钩子声明、禁揭露确认、失败后修复路径。
- 五步闭环：生成任务包、Agent 写稿、提交草稿、门禁检查、定稿入库。
- 保留中文工程命令为主入口，例如 `/工程续章`、`/工程提交稿`、`/工程验稿`、`/工程定稿`。
- 不把 `novel-skill` 的旧命令名作为 README 或 skill 的主协议。

验收标准：

- skill 文档和 command protocol 包含续写前引导。
- 五步闭环在 README、skill 或 shared protocol 中可追溯。
- 节奏预检和章末钩子声明有可执行命令或产物。
- 文档测试确保中文工程指令优先。
实现记录：
- `shared/creative_operator_protocol.md` 新增 `/工程续章` Pre-Write Guide，覆盖用户偏好、自动兜底、节奏预检、章末钩子声明、禁揭露确认和失败修复路径。
- `shared/command_protocol.md` 新增 `/工程续章` 写前引导，保持中文工程命令为主入口，不引入旧命令名作为用户主协议。
- `shared/workflow_mapping.md` 新增 Five-Step Chapter Loop：任务包 -> Agent 草稿 -> `draft submit` -> `gate-check` -> `chapter finalize` 或修复/放行/分支/回滚。
- `longform-novel-codex/SKILL.md` 和 `longform-novel-claude/SKILL.md` 明确要求写稿前执行 Pre-Write Guide，并确认 Event Matrix、Reverse Brake、Humanizer v2 等任务包约束。
- `scripts/validate_skills.py` 与 `tests/test_skills.py` 增加协议关键词校验，覆盖 pre-write guide、pacing precheck、tail hook、forbidden reveal confirmation 和 five-step closed loop。

### [x] 9. 跨 Agent 审核与编辑团队

目标：把 `novel-skill` 的多角色编辑团队变成 `longform` 的可落盘审核任务和风险记录；宿主不支持真实多 Agent 时也能工作。

需要新增或扩展：

- 扩展命令：`editorial review`、`editorial batch-review`、`editorial status`、`editorial need-human`。
- 角色任务：策划主编、写作特工、反 AI 编辑、连载核实官、总编辑。
- 记录字段：P0/P1/P2、审核轮次、未解决问题、conditional pass、连续 conditional pass 次数、need-human 原因。
- 单章审核产物：多角色任务文件、统一问题清单、修复建议、是否允许继续。
- 批量审核产物：10 章节奏报告、逻辑一致性报告、AI 味报告、读者承诺兑现报告。
- 默认只生成任务和记录结果，不要求 Codex App 或 ClaudeCode 必须支持真实多 Agent。

验收标准：

- 单章审核能生成多角色任务文件。
- 批量 10 章体检能生成节奏/逻辑/AI 味报告。
- 连续 conditional pass 或 P1 未解决时触发 `need-human`。
- 审稿输出不进入 final、RAG、graph、memory 或 SQLite。

实现记录：

- `src/longform_engine/editorial/pipeline.py` 升级为 `schema_version=2` 审稿产物：单章 review 记录 `severity_counts`、`review_round`、`unresolved_items`、`conditional_pass_streak`、`need_human_reasons`。
- 默认编辑团队落盘为五个任务文件：`planning_chief_editor`（策划主编）、`writing_agent`（写作特工）、`anti_ai_editor`（反 AI 编辑）、`serial_verifier`（连载核实官）、`executive_editor`（总编辑）。
- `editorial batch-review` 生成 `batch_reports/` 下的 pacing、logic、AI taste 三类健康报告，并在 batch JSON 中记录 `health_report_files` 与跨章节 findings。
- `editorial status` 汇总 P0/P1/P2、审核轮次、连续 conditional pass；P0/P1 未解决或 conditional pass 连续超阈值时给出 `need_human_reasons`。
- `editorial need-human --chapter N --reason "reason"` 写入人工审阅请求文件；审稿、批审、人工升级均只写 `50_workbench/editorial_reviews/`，不推进 final/RAG/graph/memory/TCS/SQLite。
- `shared/creative_operator_protocol.md`、`shared/command_protocol.md`、`shared/workflow_mapping.md` 已同步编辑团队与批审/人工升级契约。
- `tests/test_engine_capability_baseline.py` 和 `tests/test_cli.py` 覆盖单章多角色任务、10 章批审健康报告、连续 conditional pass 触发 need-human、CLI 人工升级和 no-pollution。

## Test Plan

- [x] 新增或扩展 CLI 测试：自动写书、扩写任务、风格提取、Humanizer、事件矩阵、反向刹车、编辑团队。
- [x] 新增端到端测试：`open-book -> continue-write -> Codex draft -> submit -> gate -> finalize -> next chapter` 仍不污染失败稿。
- [x] 新增 no-pollution 测试：扩写候选、humanizer 候选、审稿输出、research inbox 不进入 final/RAG/graph/SQLite。
- [x] 新增 Windows 兼容检查：所有测试使用 `sys.executable` 或 console command，不硬编码 `python3`。
- [x] 每次阶段完成后运行 `python -B -m pytest -q`，并保持既有测试通过。

测试落点：

- CLI 能力：`tests/test_cli.py` 覆盖 `auto-write plan/run/progress/report`、`creative expand-task/expand-check`、`creative style-extract`、`creative humanize-check`、`editorial review/need-human`，`tests/test_orchestration.py` 与 `tests/test_gates.py` 覆盖事件矩阵和反向刹车。
- 端到端：`tests/test_e2e_agent_skill.py` 覆盖 `open-book -> continue-write -> Codex draft -> draft submit -> gate -> chapter finalize`，`tests/test_engine_capability_baseline.py` 覆盖失败稿 rollback/rebuild 后不污染下一阶段。
- no-pollution：扩写候选、Humanizer 候选、审稿输出、research inbox 均有 final/RAG/graph/SQLite 边界断言。
- Windows 兼容：CLI 子进程测试统一使用 `sys.executable`；`tests/test_capability_gap_test_plan.py` 检查 `tests/` 与 `scripts/` 中不硬编码 `python3`。
- 当前最终验证：`python -B -m pytest -q` 通过。

## Definition of Done

每项能力完成时，必须同时满足：

- CLI 或中文工程指令有明确入口。
- 所有新增产物有稳定路径和 schema。
- README、AGENTS、shared protocol 或 skill 文档至少有一处可追溯说明。
- gate 失败路径能生成可执行修复建议。
- workbench-only 产物不会污染 final、RAG、story graph、memory 或 SQLite。
- 有单元测试或端到端测试覆盖成功路径、失败路径和 Windows 调用方式。

## Assumptions

- `novel-skill` 脚本只作为行为参考，不直接复制其运行时目录和文件名契约。
- 新能力优先生成 workbench 任务、候选稿和报告。
- 只有 `chapter finalize` 能写正式正文和刷新长期记忆。
- 自动化能力应该先可暂停、可恢复、可审计，再追求全自动连续产出。
- 本 checklist 是后续补齐能力的验收路线图；新增相关能力时应同步更新本文件状态。
