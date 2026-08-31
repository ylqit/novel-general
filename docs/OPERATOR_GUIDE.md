# Operator Guide

## Web 小说创作工作台

日常使用优先启动一个明确的工作区：

```powershell
longform-engine studio serve --workspace "D:\NovelProjects" --create-workspace
```

Windows 可先安装桌面入口：

```powershell
longform-engine studio shortcut-install --workspace "D:\NovelProjects"
```

工作区首页可创建原创、同人和跨作品同人项目，或导入工作区内已有 `project.yaml`。项目驾驶舱始终显示 `production next` 的当前安全动作；只有 manifest 当前、Codex CLI 已登录且用户点击“交给 Codex”时才启动 Agent Job。设计候选批准、编译后的 Canon apply、人工深审决定、finalize、semantic apply、事件/承诺确认和 close 是彼此独立的人类确认动作。

已有项目或章节可以深链接打开：

```powershell
longform-engine studio serve "D:\NovelProjects\my-book\project.yaml"
longform-engine studio serve "D:\NovelProjects\my-book\project.yaml" --chapter 12
```

浏览器关闭不会取消正在运行的 Agent Job；重新打开同一工作区后可恢复查看。Codex 不可用时仍能查看项目、编辑人工材料和执行不依赖 Agent 的结构化操作。详细安全边界和页面路由见 [`WEB_STUDIO.md`](WEB_STUDIO.md)。

## 同人原著资料

作者侧中文入口与底层命令映射如下：

| 中文入口 | CLI |
| --- | --- |
| `/创建同人作品资料` | `longform-engine fanfiction pack-init project.yaml` |
| `/选择原著版本`、`/制定全作覆盖计划` | 编辑中文 `全作覆盖计划.yaml` 后执行 `fanfiction coverage-apply ... --approved-by human` |
| `/查看全作资料缺口` | `longform-engine fanfiction coverage-gaps project.yaml --json` |
| `/逐项搜索原著资料` | `longform-engine fanfiction source-search project.yaml --source-id SOURCE --gap GAP --query QUERY` |
| `/导入本地原著资料` | `longform-engine source-library item-import ... --approved-by human` |
| `/绑定全局原著资料` | `longform-engine fanfiction item-bind ... --approved-by human` |
| `/提取原著设定`、`/批准原著设定` | 对 `evidence_ready` 资料执行 `source-library task-create --task-type source_fact_extraction`，或生成不覆盖的 `semantic_document_v1` 骨架；逐条绑定证据位置后人工 `task-apply` 或 `extraction-approve` |
| `/处理版本冲突` | `longform-engine fanfiction conflict-apply project.yaml --source-id SOURCE --file 决定.yaml --approved-by human` |
| `/补全当前章节资料` | 写作门禁自动生成 `gap-request`；人工执行 `gap-approve` 后才可 `source-search`，绑定批准资料后执行 `gap-resolve` |
| `/查看原著资料升级` | `longform-engine fanfiction upgrade-status project.yaml --json` |
| `/申请原著资料升级` | `longform-engine fanfiction upgrade-propose ... --created-by human` |
| `/应用原著资料升级` | `longform-engine fanfiction upgrade-apply ... --proposal ... --review ... --decision ...` |
| `/工程同人故事发动机` | `longform-engine fanfiction story-engine-task/validate/apply ... --approved-by human` |
| `/工程同人路线设计` | `longform-engine fanfiction design-task/validate ...` |
| `/工程同人路线复核` | 隔离会话执行 `fanfiction design-review-task/validate ...`，随后 `design-apply --review ... --approved-by human` |
| `/查看原著事件命运` | `longform-engine fanfiction event-disposition-status project.yaml --json` |
| `/查看人物知识边界`、`/查看跨界规则`、`/查看同人章节上下文` | `longform-engine fanfiction context-status project.yaml --chapter N --json` |
| `/申请外部作品研究` | `longform-engine research external-request ...` |
| `/批准外部作品研究` | `longform-engine research external-approve ... --approved-by human` |

同人设计前，每部原著都必须完成身份、采用版本、截止点和 `design_core` 需求；写章前只硬校验当前 `chapter_dependency`。`whole_to_cutoff` 全作覆盖必须由人工显式选择。跨作品项目分别显示每部原著状态。全局资料升级只生成提案；未来影响使 Canon/规划 stale，历史影响路由 `revision_branch_v2`。项目不会复制完整原件，也不会从普通网页拼接受版权保护的连续小说、字幕或剧本。

项目原著基线 Canon 获批后，先批准同人故事发动机，再设计路线；路线必须经过不同隔离会话的独立复核。故事发动机、路线、复核和原著 Canon 的 hash 共同决定当前状态。原著事件命运、人物知识边界和跨界规则通过开放 `semantic_document_v1` claim 表达，不建立专用封闭 Schema。当前章内部上下文以稳定 claim 引用和 Token 预算编译；作者 Story Brief 只显示自然中文。

`fanfiction_source_canon_v1/v2/v3` 与旧固定内容协议不能继续作为当前证据；旧资料项目必须使用显式审计和非原地导入。

创作沙盒与迁移入口：

```powershell
longform-engine sandbox create project.yaml --type 分歧点试验 --title 标题 --body-file IDEA.md
longform-engine sandbox promote project.yaml --sandbox SANDBOX.json --candidate CANDIDATE.json --approved-by human --reason 理由
longform-engine migrate audit-v011 --source OLD_PATH --json
longform-engine migrate v011-to-v012 --source OLD_PATH --destination NEW_PATH --approved-by human
```

沙盒提升只产生正式语义候选，不会直接改变 Canon、图谱、RAG、大纲或正文。

## 1. 每轮唯一入口

```powershell
longform-engine production next project.yaml
```

返回 Agent task 时：

```powershell
longform-engine agent-task brief project.yaml TASK_OR_PATH
```

Agent 只读 manifest 的 `io.inputs`，只写 `io.output.path`。校验成功后仍须由人明确 apply/finalize。

## 2. 规划、分卷与节点审批

准备 `planning_bundle_v1` 后依次执行：

```powershell
longform-engine planning structural-validate project.yaml --file 50_workbench/planning/planning_bundle_v1.json
longform-engine planning semantic-bind project.yaml --subject 50_workbench/planning/planning_bundle_v1.json --profile architecture --author-task-id AUTHOR_TASK --author-role-id AUTHOR_ROLE --reviewer-task-id REVIEW_TASK --reviewer-role-id REVIEW_ROLE --reviewer-version REVIEW_VERSION --review-result REVIEW_RESULT --output 50_workbench/planning/semantic_application.json
longform-engine planning semantic-validate project.yaml --file 50_workbench/planning/semantic_application.json
longform-engine planning approval-record project.yaml --application 50_workbench/planning/semantic_application.json --decision approve --reason HUMAN_REASON --approved-by human --output 50_workbench/planning/human_approval.json
longform-engine planning node-decisions-record project.yaml --bundle 50_workbench/planning/planning_bundle_v1.json --file 50_workbench/planning/node_decisions_input.json --decided-by human --output 50_workbench/planning/node_decisions.json
longform-engine planning apply project.yaml --bundle 50_workbench/planning/planning_bundle_v1.json --application 50_workbench/planning/semantic_application.json --approval 50_workbench/planning/human_approval.json --node-decisions 50_workbench/planning/node_decisions.json --approved-by human
```

语义审查者必须与规划作者/编译者独立。firm 层中改变长期目标、关系阶段、原著事件命运、能力规则、死亡/存活/背叛/身份揭露、跨界规则、组织迁移或核心职责的 state-changing Plot Node 必须逐项获得人工决定；普通对话、动作、过渡、短战斗节拍和局部幽默不创建审批节点，也不能批量默认批准重大节点。

## 3. 人工写前意图与写作

```powershell
longform-engine chapter human-intent-task project.yaml --chapter N
# 人工从空白字段填写 human_chapter_intent_v2
longform-engine chapter human-intent-validate project.yaml --chapter N --file INTENT_FILE
longform-engine chapter human-intent-apply project.yaml --chapter N --file INTENT_FILE --approved-by human
longform-engine continue-write project.yaml --chapter N
```

作者只读 `chapter_story_brief_v5`。任务通过 `chapter_story_brief_basis_v3` 绑定 v5 合同、批准节点、语义义务、滚动窗口和必要事实。

## 4. 候选、共编、审稿与人工终稿

```powershell
longform-engine draft submit project.yaml --chapter N --file AGENT_CANDIDATE --agent codex
longform-engine production next project.yaml
```

可选共编使用 `chapter_coedit_session_v2`，只能产生完整 workbench 候选。P0/P1 必须先 repair。独立审稿完成后：

```powershell
longform-engine chapter human-revision-task project.yaml --chapter N
# 人工完成全文候选与 human_author_revision_v4
longform-engine chapter human-revision-validate project.yaml --chapter N --file HUMAN_TEXT --record HUMAN_RECORD
longform-engine chapter human-review-task project.yaml --chapter N
# 人工填写 human_story_review_v7
longform-engine chapter human-review-validate project.yaml --chapter N --file REVIEW_FILE
longform-engine chapter human-review-apply project.yaml --chapter N --file REVIEW_FILE --approved-by human
longform-engine chapter finalize project.yaml --chapter N --approved-by human
```

人工锁定后的任何 AI 正文变换都会使终稿和接受证据 stale。

## 5. 语义、事件、承诺和关闭

```powershell
longform-engine chapter semantic-task project.yaml --chapter N
longform-engine chapter semantic-validate project.yaml --chapter N --file SEMANTIC_FILE
longform-engine chapter semantic-apply project.yaml --chapter N --file SEMANTIC_FILE
```

随后准备人工确认的 event-realization application 和 promise-evidence application：

```powershell
longform-engine chapter event-realization-validate project.yaml --file EVENT_APPLICATION
longform-engine chapter event-realization-apply project.yaml --file EVENT_APPLICATION
longform-engine chapter promise-evidence-validate project.yaml --file PROMISE_APPLICATION
longform-engine chapter promise-evidence-apply project.yaml --file PROMISE_APPLICATION
longform-engine chapter close project.yaml --chapter N --approved-by human
```

非 defer promise 必须引用当前 final 的精确 Unicode span；事件 realized 也必须有精确 span。没有 event 或 promise action 时对应列表可以为空，但 ledger schema 仍必须当前。

## 6. 设定变更与历史回溯

```powershell
longform-engine canon-change impact project.yaml --proposal PROPOSAL --output IMPACT
longform-engine canon-change validate project.yaml --proposal PROPOSAL --impact IMPACT --review REVIEW --decision DECISION
longform-engine canon-change apply project.yaml --proposal PROPOSAL --impact IMPACT --review REVIEW --decision DECISION
```

未来变更会更新 `canonical_fact_v2` 并标记依赖产物 stale。影响已定稿章节时，apply 只创建 `revision_branch_v2`；按分支顺序完成 E..当前头后再人工 promote，统一重建语义、图谱、RAG、向量和 SQLite。

## 7. 人工读者反馈

```powershell
longform-engine reader-feedback batch-record project.yaml --file BATCH
longform-engine reader-feedback decision-record project.yaml --batch BATCH_PATH --file DECISION
longform-engine reader-feedback propose project.yaml --batch BATCH_PATH --decision DECISION_PATH --target planning
```

反馈不能直接改正文、承诺或平台策略，只能生成 planning proposal 或 canon-change seed。

## 8. 发布、产物与恢复

发布前先查看目标预检和来源权利状态：

```powershell
longform-engine publication risk-report project.yaml --target TARGET
longform-engine publication preflight project.yaml --target TARGET
longform-engine publication rights-decision project.yaml --target TARGET --decision proceed --approved-by human --note NOTE
longform-engine publication export project.yaml --target TARGET
```

同人权利决定只约束指定目标的导出。配置、来源 Canon、逐来源权利声明或政策快照变化后必须重新决定；预检和决定都不构成授权、法律意见或平台接受保证。

历史 workbench 产物先预览再归档：

```powershell
longform-engine artifacts compact project.yaml --through N --dry-run
longform-engine artifacts compact project.yaml --through N
longform-engine artifacts verify project.yaml
```

审计 ZIP 保留清单和内容哈希；final、语义账本、规划账本、关闭记录和当前状态视图不会被归档删除。

发生写入中断先执行：

```powershell
longform-engine recovery status project.yaml --json
longform-engine production next project.yaml
```

不要手工删除锁、事务或 SQLite。恢复动作必须绑定 `recovery status` 的当前报告 hash，并由人明确选择允许的恢复操作。
