# Operator Guide

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
| `/提取原著设定`、`/批准原著设定` | 先执行 `source-library extraction-template --item-id ...` 生成不覆盖的 `source_extraction_candidate_v1` 骨架，逐条填写短证据后再执行 `source-library extraction-approve ... --approved-by human` |
| `/处理版本冲突` | `longform-engine fanfiction conflict-apply project.yaml --source-id SOURCE --file 决定.yaml --approved-by human` |
| `/补全当前章节资料` | 写作门禁自动生成 `gap-request`；人工执行 `gap-approve` 后才可 `source-search`，绑定批准资料后执行 `gap-resolve` |
| `/查看原著资料升级` | `longform-engine fanfiction upgrade-status project.yaml --json` |
| `/申请原著资料升级` | `longform-engine fanfiction upgrade-propose ... --created-by human` |
| `/应用原著资料升级` | `longform-engine fanfiction upgrade-apply ... --proposal ... --review ... --decision ...` |
| `/申请外部作品研究` | `longform-engine research external-request ...` |
| `/批准外部作品研究` | `longform-engine research external-approve ... --approved-by human` |

同人设计前，每部原著都必须完成权威版本、截止点、全目录单元、来源和维度覆盖。跨作品项目必须全部通过。全局资料升级只生成提案；未来影响使 Canon/规划 stale，历史影响路由 `revision_branch_v2`。项目不会复制完整原件，也不会从普通网页拼接受版权保护的连续小说、字幕或剧本。

本指南对应 v0.11.0。旧 v0.9 项目、证据、Skill 与 `fanfiction_source_canon_v1` 不能继续使用。

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

语义审查者必须与规划作者/编译者独立。每个 firm Plot Node 都要一个人工决定；不能批量默认批准。

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

## 8. 恢复和平台边界

发生写入中断先执行：

```powershell
longform-engine recovery status project.yaml --json
longform-engine production next project.yaml
```

不要手工删除锁、事务或 SQLite。起点是主要编辑画像，番茄是非阻断观察；预检不承诺平台通过，也不报告 AI 概率或规避检测。

`literary_evidence_ready=false` 仍是当前事实。
