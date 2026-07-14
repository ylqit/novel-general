# Agent App Workflow Productization Checklist

本文档用于后续验证 Codex App、Codex CLI 和 Claude Code 的小说生产体验封装是否真正落地。

## 1. Status Legend

- `[ ]` 未开始。
- `[~]` 已有基础，但还没有完整验收。
- `[x]` 已实现，并有可重复验证方式。

## 2. Productized Agent Entry

- [x] Codex skill 默认要求先运行 `production next`。
- [x] Claude Code skill 默认要求先运行 `production next`。
- [x] 有 Agent task 时，skill 要求运行 `agent-task brief` 渲染工作单。
- [x] `/工程下一步` 映射到 `longform-engine production next project.yaml`。
- [x] `/工程工单` 映射到 `longform-engine agent-task brief project.yaml TASK`。
- [x] `/工程生产状态` 映射到 `longform-engine production status project.yaml`。
- [x] `/工程生产看板` 映射到 `longform-engine production board project.yaml`。
- [x] `/工程推进` 映射到 `longform-engine production loop project.yaml --no-apply`。

## 3. Agent Work Order

- [x] 工作单显示 Agent role and goal。
- [x] 工作单显示 input files。
- [x] 工作单显示 allowed output paths。
- [x] 工作单显示 output schema。
- [x] 工作单显示 validate/apply/failure command。
- [x] 工作单显示 hard boundaries。
- [x] 工作单显示 forbidden direct writes。
- [x] `chapter_write` 有章节作者说明。
- [x] `repair` 有修章作者说明。
- [x] `humanize` 有 Humanizer 说明。
- [x] `content_expand` 有扩写作者说明。
- [x] `graph_extract` 有图谱抽取员说明。
- [x] `memory_extract` 有语义记忆抽取员说明。
- [x] `character_memory` 有角色记忆员说明。
- [x] `editorial_review` 有编辑角色说明。
- [x] `pacing_review` 有节奏读者说明。

## 4. Context Budget

- [x] 工作单声明只读取 manifest `input_files` 和工作单。
- [x] 工作单禁止默认扫描整个项目。
- [x] 工作单禁止把未声明 draft、repair candidate、research inbox 当成 canon。
- [x] 工作单禁止读取或直接写 final/RAG/graph/TCS/SQLite。

## 5. Feedback Carryover

- [x] 下一章 writing task JSON 包含 `feedback_carryover`。
- [x] 下一章 writing task Markdown 包含 `Feedback Carryover`。
- [x] feedback 来源限制为 gate、repair、humanize、semantic pacing、editorial aggregate。
- [x] feedback source files 进入 chapter_write manifest input files。
- [x] feedback 只作为写作提醒，不直接修改 final/RAG/graph/TCS/SQLite。

## 6. Safe Loop Boundary

- [x] `production loop` 默认 `--no-apply`。
- [x] 遇到 Agent output 阻断时暂停。
- [x] 遇到 human finalize 阻断时暂停。
- [x] 遇到 canonical apply/finalize 阻断时暂停。
- [x] 不引入 Python CLI 内部 LLM 调用。
- [x] 不要求 hidden external API key。

## 7. Quality Benchmark

- [x] 产品化文档包含 5 章 smoke benchmark 方法。
- [x] 产品化文档包含 10 章 quality benchmark 记录字段。
- [~] 5 章 smoke benchmark 需要真实 Agent 产品会话执行。
- [~] 10 章 quality benchmark 需要真实 Agent 产品会话执行。

## 8. Verification Commands

```powershell
python scripts/validate_skills.py
python scripts/release_surface_guards.py
python -m pytest tests/test_skills.py tests/test_orchestration.py::test_continue_write_creates_agent_writing_task_by_default tests/test_production_experience.py
python -m pytest tests/test_agent_task_protocol.py tests/test_e2e_agent_skill.py tests/test_cli.py::test_cli_mutating_commands_are_marked_for_project_lock
```

## 9. Definition of Done

- [x] 文档已落盘。
- [x] skill、shared command protocol 和 workflow mapping 已引用生产体验入口。
- [x] `agent-task brief` 已升级为产品化工作单。
- [x] `continue-write` 已写入 feedback carryover。
- [x] 新增或更新测试覆盖 command protocol、brief 渲染和 feedback carryover。
