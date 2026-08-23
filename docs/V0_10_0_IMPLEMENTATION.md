# v0.10 语义规划、分卷滚动与版本化回溯

> 状态：v0.10.0 活动协议。本文不代表任何平台会接受、推荐或无法识别机器辅助内容。

## 设计边界

v0.10 把“格式正确”和“小说语义成立”拆成两个不可互相冒充的判断：

1. `structural_validation_v2` 只检查字段、范围、稳定 ID、哈希、滚动层级和依赖闭包，固定输出 `semantic_verdict=not_evaluated`。
2. `evidence_review_v2` 由独立审查角色执行，必须绑定当前规划文件的字节哈希和精确证据 span。
3. 独立语义审查通过后，仍需单独的 `human_planning_approval_v1`；机器通过不能替代人工批准。
4. 任何文件或审查结果发生漂移，后续批准和 apply 都会失效。

词面命中只能形成 P2 诊断提示。它不能自动证明剧情事件已发生、读者承诺已兑现，也不能直接制造 P1 或配额失败。

## 分卷与滚动窗口

`planning_bundle_v1` 同时携带：

- `book_spine_v1`：全书核心冲突、终局边界、主角弧和不可破坏项；
- `volume_skeletons_v1`：所有卷的边界与生命周期；
- `volume_plan_v1`：当前活动卷的详细计划；
- `rolling_window_plan_v2`：1–3 章 firm、4–10 章 directional、11–20 章 horizon；
- `chapter_forecast_v1`：窗口内每章的方向预测；
- `chapter_contract_v5`：仅覆盖 firm 层，按章节拓扑声明失败、选择、代价、余波是否适用；
- `semantic_obligation_v1`：世界、知识、关系、人物、资源、地点、承诺和读者认知等语义义务。

滚动不是一次生成全书细纲。完成章节后应重新编译窗口：已经进入正文的历史事实不在主线原地改写，新的 firm 章节必须重新经过结构校验、独立语义审查和人工批准。

## 全情节节点审批

firm 章节使用 `plot_node_table_v1`。所有进入因果图的节点都必须逐行记录以下一种人工决定：

- `approve`
- `adjust`
- `reject`
- `defer`

没有批量批准，也没有默认批准。任一 `reject` 或 `defer` 会阻止整份规划 apply。`micro` 节点可以服务行文，但不会自动进入状态图；`state_change` 节点在 apply 后成为 `planned_approved` 的 `narrative_event_v1`。

章节完成后，事件实现必须引用终稿和语义账本的当前 SHA-256、Unicode code point 精确 span，并由人工确认。正文里出现类似事件的词语不构成实现证据；新发现的因果节点必须退回规划审批。

## 读者承诺

`reader_promise_ledger_v2` 只接纳人类明确选择的候选承诺，不再从模板自动补出“前三章”“卷末”等隐藏承诺。章节允许没有承诺动作。阶段性兑现使用稳定 `stage_id`，进展与兑现均需终稿精确证据、语义账本哈希和人工确认。

## 历史回溯

历史修改使用 `revision_branch_v2`，范围必须是 `E..当前终稿头`：

```text
revision branch
  -> 隔离复制基线与不可变 head 清单
  -> 按章节顺序完成方向、节点、语义、人工锁定、终稿门禁、语义实现、chapter close
  -> revision record
  -> 人工选择 abandon 或 promote
```

存在活动分支时，`production next` 阻止主线续写。`abandon` 不修改 canonical、SQLite、RAG 或图谱；`promote` 在同一外层事务中替换分支章节、语义账本和 closure，并触发语义/RAG/DB 重建。基线 head 漂移会阻止记录和提升。

创建分支示例：

```powershell
longform-engine revision branch project.yaml `
  --from-chapter 37 --to-chapter 52 `
  --reason "调整卷末因果链" --created-by human
```

## CLI 顺序

规划文件先保存在 `50_workbench/planning/`，典型顺序如下：

```powershell
longform-engine planning structural-validate project.yaml --file 50_workbench/planning/bundle.json
longform-engine planning semantic-bind project.yaml --subject 50_workbench/planning/bundle.json ...
longform-engine planning semantic-validate project.yaml --file 50_workbench/planning/application.json
longform-engine planning approval-record project.yaml --application 50_workbench/planning/application.json ...
longform-engine planning node-decisions-record project.yaml --bundle 50_workbench/planning/bundle.json ...
longform-engine planning apply project.yaml --bundle 50_workbench/planning/bundle.json ... --approved-by human
```

省略号代表必须显式提供的角色、审查结果、理由和输出文件参数；以 `--help` 为准。所有这些命令都进入项目级写入锁。

## 正文协作锚点

审稿台选区使用 `text_anchor_v2`：浏览器 UTF-16 位置先转换成 Unicode code point，再冻结源稿哈希、选段哈希、段落 ID、段落哈希和前后上下文。因此 emoji、扩展汉字等字符不会造成 Python 与浏览器 span 偏移；源稿、Story Brief 或人工意图变化后，旧会话会变成 stale。

## 起点与番茄边界

本项目优化的是可读性、人物能动性、因果连续性、节奏与人工创作控制，不实现“规避 AI 检测器”，也不声称能通过起点或番茄的未知审核算法。平台观察只能是非阻断、可追溯的编辑参考；最终投稿必须遵守当时的平台规则，并由作者对内容和披露义务负责。

## 当前实施状态

已落地：结构/语义分离、分卷和滚动协议、唯一章合同 v5、Story Brief v5、全节点逐项审批、事件与承诺精确证据、设定依赖传播、非 canonical 读者反馈、隔离历史分支、事务化提升、主线阻断、审稿台 Unicode 锚点及 CLI 写入锁。

本次 v0.10.0 最终接线后按发布授权未运行测试或 smoke；第一阶段曾记录的 415 passed 不能解释为最终发布验证。
