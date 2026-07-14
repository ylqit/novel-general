# Agent Experience Orchestration Checklist

本文档用于后续验证 `longform-novel-engine` 的体验层编排是否真正补齐 novel-skill 的生产现场感，同时保持 no-key Agent 协作边界。

## 1. Status Legend

- `[ ]` 未开始。
- `[~]` 已有基础或局部能力，但未达到体验层验收标准。
- `[x]` 已实现，并有可重复验证方式。

## 2. Next Action Center

目标：统一输出当前项目最该执行的下一步动作。

- [x] 已有 `next_command` 分散在 task、gate、auto-write state 和 reports 中，并已由 Next Action Center 聚合。
- [x] 新增 `longform-engine production next project.yaml [--json]`。
- [x] 输出当前章节、阻断类型、等待对象和最高优先级 next action。
- [x] 输出 Agent 必读文件列表。
- [x] 输出 Agent 允许写入路径。
- [x] 输出 schema、validate、apply、failure command。
- [x] 输出 hard boundaries：no final、no rag、no graph direct、no sqlite direct。
- [x] 当没有阻断时，输出可生成下一章任务的命令。
- [x] 当存在 need-human 时，优先输出人工处理原因和命令。

推荐测试：

- `tests/test_production_experience.py`
- `tests/test_cli.py`

验收查询：

```powershell
rg -n "production next|Next Action Center|blocked_by|waiting_for|next_command" longform-novel-engine/src longform-novel-engine/tests longform-novel-engine/docs
```

## 3. Agent Work Order Renderer

目标：把任意 `AgentTaskManifest v1` 渲染成可交给宿主 Agent 的工作单。

- [x] 各 task markdown 已有局部任务说明，并已由统一 brief 渲染器聚合。
- [x] 新增 `longform-engine agent-task brief project.yaml TASK_OR_PATH [--json]`。
- [x] brief 必须显示 task id、task type、chapter、status。
- [x] brief 必须显示明确输入文件列表。
- [x] brief 必须显示明确允许写入路径。
- [x] brief 必须显示输出 schema。
- [x] brief 必须显示 validate/apply/failure command。
- [x] brief 必须显示 hard boundaries。
- [x] brief 不得修改 manifest 或 canonical state。
- [x] brief 对 chapter_write、repair、humanize、content_expand、graph_extract、memory_extract、character_memory、editorial_review、pacing_review 都可用。

推荐测试：

- `tests/test_production_experience.py`
- `tests/test_agent_task_protocol.py`
- `tests/test_cli.py`

验收查询：

```powershell
rg -n "agent-task brief|Work Order Renderer|input_files|allowed_output_paths|output_schema|failure_next_command" longform-novel-engine/src longform-novel-engine/tests longform-novel-engine/docs
```

## 4. Production Board

目标：按章节展示生产状态，而不是让用户手动拼 gate、manifest、memory、graph、editorial 文件。

- [x] `auto-write progress/report` 已能展示调度状态，并已由 production board 汇总到章节看板语境。
- [x] 新增 `longform-engine production board project.yaml [--from N --to M --json]`。
- [x] board 展示 draft/final/gate 状态。
- [x] board 展示 repair/humanize/expand 状态。
- [x] board 展示 graph/memory/character memory 状态。
- [x] board 展示 semantic pacing 状态。
- [x] board 展示 editorial expected/accepted/missing/invalid/need-human 状态。
- [x] board 展示 latest transaction/report 摘要。
- [x] board 支持 JSON contract，供 GUI/API 直接消费。

推荐测试：

- `tests/test_production_experience.py`
- `tests/test_cli.py`

验收查询：

```powershell
rg -n "production board|Production Board|gate_status|editorial|need_human|transaction" longform-novel-engine/src longform-novel-engine/tests longform-novel-engine/docs
```

## 5. Safe Loop Driver

目标：安全推进确定性步骤，直到下一个需要 Agent、人工或 apply/finalize 的阻断点。

- [x] `auto-write run` 是 scheduler，不是 writer。
- [~] `auto-write run` 已能识别 awaiting Agent draft、repair candidate、semantic output、editorial result。
- [x] 新增 `longform-engine production loop project.yaml [--max-steps N --no-apply]`。
- [x] loop 可以生成缺失的 writing task。
- [x] loop 可以对已存在的 Agent 输出运行 validate 或 submit。
- [x] loop 可以运行 deterministic gate-check。
- [x] loop 可以聚合已 accepted editorial role results。
- [x] loop 遇到需要 Agent 创作或语义判断时必须暂停。
- [x] loop 遇到 human approval/finalize 时必须暂停。
- [x] loop 默认不自动 apply。
- [x] loop 不调用 LLM，不生成正文，不绕过既有 CLI 边界直接写 final/RAG/graph/SQLite。

推荐测试：

- `tests/test_production_experience.py`
- `tests/test_orchestration.py`
- `tests/test_cli.py`

验收查询：

```powershell
rg -n "production loop|Safe Loop Driver|no-apply|awaiting_agent|awaiting_semantic|awaiting_editorial" longform-novel-engine/src longform-novel-engine/tests longform-novel-engine/docs
```

## 6. Editorial Team UX

目标：把编辑团队 fan-out/fan-in 做成可读的生产视图。

- [x] `editorial review` 可生成多角色任务。
- [x] `editorial submit-review` 可校验单角色 JSON。
- [x] `editorial aggregate` 可汇总 need-human。
- [x] production board 显示 expected roles。
- [x] production board 显示 accepted roles。
- [x] production board 显示 missing roles。
- [x] production board 显示 duplicate role results。
- [x] production board 显示 invalid role results。
- [x] production next 在 editorial 阻断时给出 role-specific work order。
- [x] need-human 原因必须可读并指向下一条命令。

推荐测试：

- `tests/test_production_experience.py`
- `tests/test_agent_task_protocol.py`
- `tests/test_cli.py`

验收查询：

```powershell
rg -n "expected_roles|accepted_roles|missing_roles|duplicate_role_results|invalid_results|need_human" longform-novel-engine/src longform-novel-engine/tests longform-novel-engine/docs
```

## 7. GUI/API JSON Contract

目标：GUI/API 直接消费体验层 JSON，不再自己拼散落文件。

- [x] task index、events、gate artifacts、validation reports、transaction reports 已存在。
- [x] `production status --json` 输出稳定 schema。
- [x] `production next --json` 输出稳定 schema。
- [x] `production board --json` 输出稳定 schema。
- [x] `agent-task brief --json` 输出稳定 schema。
- [x] JSON 中所有路径使用项目相对路径。
- [x] JSON 中所有命令使用可复制 CLI 字符串。
- [x] GUI/API contract 不包含正文全文、API key、完整 prompt 日志。
- [x] 文档列出 GUI/API 建议资源：status、next、board、brief、loop。

推荐测试：

- `tests/test_production_experience.py`
- `tests/test_cli.py`

验收查询：

```powershell
rg -n "production status|production next|production board|agent-task brief|--json|GUI/API" longform-novel-engine/src longform-novel-engine/tests longform-novel-engine/docs
```

## 8. No-Pollution / No-LLM Guard

目标：体验层不能因为追求 one-click 现场感而重新引入脚本内 LLM 或 canonical 直写。

- [x] release guard 已检查直接外部 LLM import/call pattern。
- [x] release guard 已检查隐藏外部 LLM API key 字符串。
- [x] release guard 已检查 `api_provider` 保持禁用。
- [x] release guard 增加体验层命令 guard marker。
- [x] release guard 检查 `production loop` 不 import OpenAI/Anthropic。
- [x] release guard 检查 `production loop` 不直接写 final/RAG/graph/SQLite。
- [x] release guard 检查 `agent-task brief` 是只读渲染。
- [x] no-pollution E2E 覆盖 production loop 暂停路径。

推荐测试：

- `scripts/release_surface_guards.py`
- `tests/test_agent_skill_integrity.py`
- `tests/test_production_experience.py`

验收查询：

```powershell
python longform-novel-engine/scripts/release_surface_guards.py
rg -n "OpenAI|Anthropic|OPENAI_API_KEY|ANTHROPIC_API_KEY|production loop|agent-task brief" longform-novel-engine/src longform-novel-engine/scripts longform-novel-engine/tests
```

## 9. Tests and Fixtures

目标：体验层有独立测试，而不是只靠底层 Agent task 测试。

- [x] 新增 `tests/test_production_experience.py`。
- [x] status 能汇总当前章节、gate、manifest、next command。
- [x] next 能选择最高优先级阻断任务。
- [x] board 能展示多章节状态。
- [x] brief 能渲染 input files、allowed output、schema、validate/apply/failure command。
- [x] loop 遇到 awaiting Agent 输出必须暂停。
- [x] loop 不调用 LLM。
- [x] loop 不绕过既有 CLI 边界直接写 final/RAG/graph/SQLite。
- [x] CLI 测试覆盖 production status/next/board/loop 和 agent-task brief。
- [x] fixture 覆盖 gate failed、awaiting repair、awaiting semantic、awaiting editorial、awaiting finalize。

推荐测试：

- `tests/test_production_experience.py`
- `tests/test_cli.py`
- `tests/test_e2e_agent_skill.py`

验收查询：

```powershell
rg -n "test_production|production status|production next|production board|production loop|agent-task brief" longform-novel-engine/tests
```

## 10. Definition of Done

体验层编排完成时必须满足：

- [x] `production status/next/board/loop` CLI 已实现。
- [x] `agent-task brief` CLI 已实现。
- [x] Next Action Center 能统一输出当前最安全下一步。
- [x] Work Order Renderer 能覆盖所有 Agent task type。
- [x] Production Board 能按章节展示生产状态。
- [x] Safe Loop Driver 能推进确定性步骤并在 Agent/human/apply/finalize 阻断点暂停。
- [x] Editorial Team UX 能显示 fan-out/fan-in 缺口和 need-human 原因。
- [x] GUI/API JSON contract 稳定且文档化。
- [x] release guard 覆盖体验层 no-LLM/no-pollution 边界。
- [x] 所有体验层测试通过。
- [x] README、AGENTS、硬化文档和本 checklist 引用保持同步。

最终验收命令：

```powershell
python -m pytest tests/test_production_experience.py tests/test_cli.py::test_cli_mutating_commands_are_marked_for_project_lock
python scripts/release_surface_guards.py
rg -n "AGENT_EXPERIENCE_ORCHESTRATION|production status|production next|production board|agent-task brief|production loop" README.md AGENTS.md docs/AGENT_COLLABORATION_HARDENING.md
```

当前第一阶段 Definition of Done 已闭环。后续若继续增强 GUI/API 或真实多 Agent 调度体验，应新增下一阶段 checklist，而不是改写本阶段完成定义。
