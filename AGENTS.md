# longform-novel-engine Agent Guide

## Agent Collaboration Hardening Docs

处理 Agent 协作层、无外部 Key、AgentTaskManifest、validate/apply 边界或 GUI/API 任务队列时，优先阅读：

1. `docs/AGENT_COLLABORATION_HARDENING.md`
2. `docs/AGENT_COLLABORATION_HARDENING_CHECKLIST.md`
3. `docs/AGENT_EXPERIENCE_ORCHESTRATION.md`
4. `docs/AGENT_EXPERIENCE_ORCHESTRATION_CHECKLIST.md`
5. `docs/AGENT_APP_WORKFLOW_PRODUCTIZATION.md`
6. `docs/AGENT_APP_WORKFLOW_PRODUCTIZATION_CHECKLIST.md`

硬化文档定义了 `agent_skill` 默认路径、`api_provider` 保留但禁用的边界、`no final` / `no rag` / `no graph direct` / `no sqlite direct` hard boundaries，以及后续 strict manifest validation、lifecycle states 和 transaction rollback 的验收标准。
体验层编排文档定义了 Next Action Center、Agent Work Order Renderer、Production Board、Safe Loop Driver 和 GUI/API JSON contract 的后续优化方向。

本项目是面向中文长篇网文的本地工程化创作引擎。Agent 处理本目录时，应优先保证长篇连续性、命令驱动、可恢复落盘和发布边界，不要把它退化成单次 prompt 生成器。

## 优先阅读

1. `README.md`
2. `docs/AGENT_COLLABORATION_HARDENING.md`
3. `docs/AGENT_COLLABORATION_HARDENING_CHECKLIST.md`
4. `docs/AGENT_EXPERIENCE_ORCHESTRATION.md`
5. `docs/AGENT_EXPERIENCE_ORCHESTRATION_CHECKLIST.md`
6. `docs/AGENT_APP_WORKFLOW_PRODUCTIZATION.md`
7. `docs/AGENT_APP_WORKFLOW_PRODUCTIZATION_CHECKLIST.md`
8. `docs/SKILL_INSTALLATION.md`
9. `docs/NOVEL_SKILL_CAPABILITY_GAP_CHECKLIST.md`
10. `docs/CONFIGURATION.md`
11. `docs/PIPELINE_MODEL.md`
12. `docs/GATE_MODEL.md`
13. `docs/RAG_MODEL.md`
14. `docs/GRAPH_MODEL.md`
15. `docs/RESEARCH_MODEL.md`
16. `docs/REVISION_MODEL.md`
17. `docs/SQLITE_MODEL.md`
18. `config/default.engine.yaml`
19. `templates/qidian-longform/project.yaml`
20. `src/longform_engine/cli.py`

## 工程边界

- 文件是事实源，SQLite 只能作为可重建派生索引。
- CLI 是正式状态变更入口；Agent 不应绕过 CLI 写正式正文、长期记忆、RAG、图谱或数据库。
- 默认 `writing.mode = agent_skill`，Codex / ClaudeCode 负责写章节正文草稿，`longform-engine` 负责门禁、定稿、索引和落盘。
- Codex / ClaudeCode 日常生产应先运行 `production next`，再用 `agent-task brief` 渲染工作单；不要从全项目搜索来替代工作单输入列表。
- 新增命令或配置字段时，同步 `config/default.engine.yaml`、`templates/qidian-longform/project.yaml`、相关 docs 和 tests。
- 不要把真实正文、API key、完整 prompt 日志、SQLite、模型缓存或 `novels/` 运行产物提交到 Git。

## Agent-Skill 写作边界

Agent 允许做的事：

- 读取 `50_workbench/writing_tasks/chNNN.md` 和同名 JSON 任务包。
- 根据任务包生成章节正文。
- 将正文草稿写入 `50_workbench/agent_drafts/chNNN.codex.md` 或 `50_workbench/agent_drafts/chNNN.claude.md`。

Agent 禁止直接写入或修改：

- `40_manuscript/final/`
- `60_rag/`
- `30_state/story_graph.json`
- `70_runtime/db/`

草稿进入工程状态的唯一入口：

```powershell
python -m longform_engine.cli draft submit project.yaml --chapter N --file 50_workbench/agent_drafts/chNNN.codex.md --agent codex
python -m longform_engine.cli draft submit project.yaml --chapter N --file 50_workbench/agent_drafts/chNNN.claude.md --agent claude
```

章节进入定稿状态的唯一入口：

```powershell
python -m longform_engine.cli chapter finalize project.yaml --chapter N --approved-by human
```

上一章未 final 或上一章门禁失败时，下一章 `continue-write` 必须阻断。失败草稿不得进入 RAG、知识图谱、final 状态或 SQLite 正式章节索引。
