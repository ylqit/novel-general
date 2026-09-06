# Architecture

本文描述 `longform-novel-engine` 的当前架构：确定性基础设施、Host Agent 语义工作和人工决定各自拥有明确边界；原创、同人及跨作品同人共用生产控制面，但人物、事件、关系、能力、外观或跨界规则不固化成封闭内容表。

```text
浏览器/CLI 暂存 → source_asset_v1 → 内部资料项/处理记录
→ evidence_reference_v1 → semantic_document_v1 语义候选
→ 独立语义复核 → human_decision_v1 → 项目固定绑定
→ 项目原著基线 semantic_document_v1 人工 apply
→ 项目 Graph / RAG / SQLite 增量物化
```

`studio serve` 提供工作区级统一 loopback Web；章节审稿挂载在同一 Studio 路由，`review serve` 只保留为恢复与兼容入口。资料处理默认本地；OpenAI 只允许逐任务人工授权的非 Canon OCR、视觉或 ASR 预处理，不能用于小说正文、规划、审稿、Canon 决策或发布判断。

## 1. 系统定位

Host Agent 负责创作、规划、语义判断和独立审稿；确定性控制面负责协议、路径、hash、状态机、事务和 canonical 写入。普通 CLI 工作流不在后台调用 LLM。Workspace Studio 只有在用户点击当前 manifest 的 Agent 动作后，才能启动一个受限的本机 Codex CLI 子进程；它不是通用 LLM 客户端，也不创建并行写入者。

```text
Host Agent
  -> bounded AgentTaskManifest v5
  -> one declared workbench output
  -> deterministic structural validation
  -> independent semantic review
  -> explicit human decision
  -> transaction v3 canonical apply
  -> rebuildable graph / RAG / vector / SQLite views
```

### 1.1 Workspace Studio 与 Codex 边界

```text
浏览器结构化动作
  -> 当前 production action / 当前 AgentTaskManifest v5
  -> manifest 声明输入复制到隔离 staging
  -> prompt 经 stdin 交给 codex exec --json
  -> Codex 只写 staging 中声明的唯一 output
  -> 检测额外文件与输入篡改
  -> 协议校验后复制回项目 workbench 候选
  -> 人类预览、选择并显式批准
  -> 确定性 transaction v3 canonical mutation
```

Web 后端不接受浏览器提供的命令行或 Prompt，不保存 OpenAI API key，也不把 Codex 进程输出保存为完整 Prompt 日志。每个项目同一时刻最多一个状态变化 Agent Job；章节写作、repair、独立审稿和语义档案仍遵守 manifest 的 session 策略。Web 页面关闭不改变任务状态，Codex 失败也不会改变正式项目状态。

工作区必须是用户给出的绝对目录，不能隐式指向用户目录、磁盘根或仓库根。每个工作区由 lock 与本机实例元数据保证只有一个 Studio；可信启动器可通过随机 launcher secret 请求新的单次 bootstrap URL。浏览器会话继续使用 loopback、Host、Origin、session cookie、CSRF 与 CSP 防护。启动或打开任何页面都不构成 canonical 授权。

## 2. 事实层级

1. 人工批准的治理、`canonical_fact_v2`、Book Spine、分卷和滚动规划。
2. `40_manuscript/final/chNNN.md`。
3. 与 final 精确 span 绑定的 semantic ledger、事件实现和承诺证据。
4. graph、角色状态、伏笔状态、TCS 等物化视图。
5. RAG、向量、SQLite 和缓存等可重建视图。

摘要、关键词命中、平台启发式或 Agent 推断不能覆盖高层事实。

### 2.1 原著资料、同人连续性与故事 Canon

```text
当前用户级原著证据库（非 Canon）
-> 具体小说的固定资料绑定与动态覆盖计划（workbench）
-> 该小说人工批准的原著基线 Canon
-> 分歧 / AU / 续写 / 跨界连续性 Canon
-> 已批准设计与定稿章节形成的故事 Canon
```

用户级资料库使用稳定作品、资料项和 `source_asset_v1` 身份，并以输入、处理配置和规范化输出 hash 固定内部记录。目录名可改，引用只认稳定 ID 与 hash。格式处理结果和全局语义候选永不自动进入任何项目的 Bible、RAG、Graph 或 SQLite。

项目在 `50_workbench/同人原著资料/<作品名>/` 保存固定绑定、动态覆盖需求、批准语义候选与短证据。原件仍位于用户库。每部作品的版本和截止点由人工确定，不预建小说、动画、人物等空目录。默认门禁是 `design_core` 和当前 `chapter_dependency`；`whole_to_cutoff` 只在人工选择后生效。`fanfiction_source_canon_v1/v2/v3` 直接不兼容，v0.11 只允许非原地单向导入。

项目内容统一使用 `semantic_document_v1`：中文正文是主要语义载体，只有会被 Canon、图谱或依赖传播使用的断言才拆成带证据 claim。事实、解释、假设和创作建议必须分层；开放中文 `document_type` 与 `extensions` 不要求修改 Schema。

同人正式设计在项目原著基线 Canon 之后增加两个门禁：人工批准的 `同人故事发动机`，以及与路线生成会话隔离的 `同人路线独立复核`。`compile_fanfiction_creative_requirements` 把六种连续性模式与 `oc_si_progression | canon_character_centered | hybrid` 三种叙事承担方式正交编译为创作、上下文与复核要求。共享要求是人物自主性、主角与原著关系、原著辨识度、本作新增阅读价值、持续叙事动力和责任分配。补完、前传与人物理解可以提供新增价值，不强迫改命、升级、唯一初始变量或原作事件结束后的独立原创主线。分歧路线必须提供有效因果，并允许多个有明确关系的初始条件。`event_disposition_applicability` 以状态、理由与当前路线 claim 引用说明事件处置是否适用；保留原著事件允许原有因果，不适用理由仍须独立审稿。创作合同版本由 CLI 固化；人工决定绑定路线、复核、故事发动机和原著 Canon hash，旧任务不自动升级。

章节后处理只能提出 `已实现重大分歧` 声明。人工批准后，每个稳定触发身份独立、幂等地创建一次未来知识重估；三个不同重大分歧会形成三个任务，重复运行不会复制任务。批准结果以“仍可靠、部分可靠、已失效、反向误导”描述适用章节范围，并通过 live provenance pin 与不可变 archive 进入后续章节的精确依赖闭包。

章节侧使用可重建、只接受当前版本的内部 `fanfiction_context_bundle_v3`。选择优先级固定为“全局故事承诺/显式不变量 → 章节显式引用 → 递归依赖闭包 → 当前来源/人物/事件/卷/篇章结构化范围 → 可选 RAG”；仅因章节号或卷范围适用不会把整份路线塞入上下文。v2 分离作者自然中文投影与带来源、稳定 claim/evidence ID 的精确审阅投影，并记录来源分区、命名冲突和逐分区预算。必需证据超预算时在任何产物写入前阻断，列出最大占用项与缩小范围建议；不得截断。该 bundle 与全部来源 hash 纳入 `chapter_story_brief_basis_v4` 和审阅 provenance。

活动运行时检索域只包括 `source_evidence`、`project_canon` 和 `project_story`。同人案例与技法属于仓库角色配置、质量规则和抽象验收夹具，不是第四个运行时检索域，也不进入项目 Canon、RAG 或图谱。

跨界路线显式声明 `fixed_host | fusion_world | sequential_worlds`。需求编译器只依据实际 `transfers[].payload_kinds` 生成相互作用主题，经“来源适配器 → 项目跨界宪法 → 当前卷例外”验证能量补充、作用对象、激活条件、成本、反制、身份、组织响应、死亡/返回与不可逆后果。固定宿主必须有默认宿主；融合世界必须提供世界规则优先级；顺序诸天必须逐卷唯一宿主并保留至少一项跨卷关系、身体状态、资源债务、敌对关系、知识失效或总目标进度。系统不生成来源间 N×N 兼容矩阵。

全局资料修正不会改变已固定项目。系统只报告升级候选并生成 `fanfiction_source_upgrade_proposal_v1`；独立语义审查不得降级由稳定事实 ID/显式引用得到的 `must_stale`。未来升级使项目 Canon 和明确依赖产物 stale；影响定稿章节时只创建 `revision_branch_v2`，不改当前绑定或正文。

## 3. 语义规划与分卷滚动

`planning_bundle_v1` 同时提供：

- `book_spine_v1` 与 `volume_skeletons_v1`；
- 唯一活动 `volume_plan_v1`；
- 最多 20 章的 `rolling_window_plan_v2`；
- firm 1–3 章、directional 4–10 章、horizon 11–20 章；
- 逐章 forecast、语义义务、Plot Node 表和 `chapter_contract_v5`；
- 明确选入的 `reader_promise_v2`，不从文本猜测承诺。

结构校验只判断字段、范围、稳定 ID、引用与 hash。独立语义审查必须绑定原规划 bytes，并与作者/编译者角色隔离；人工随后批准整体规划，并对 firm 层每个改变长期目标、关系、原著事件命运、能力规则、身份、生死、组织或跨界规则的 state-changing Plot Node 逐项 `approve|adjust|reject|defer`。普通对话、动作、过渡和局部节拍不创建审批节点；只有全部重大节点获批后，CLI 才能事务化写入 canonical。

章节关闭推进 `planning_cursor_v1`。下一章不足三个有效 firm 合同、进入新卷或 planning basis 漂移时，`production next` 必须返回重新规划；重新规划仍需独立语义审查和重大状态变化节点审批。

## 4. 唯一章节合同与作者上下文

正式章节合同只位于 `20_outline/chapter_contracts/chNNN.json`，schema 只有 `chapter_contract_v5`。v4 章节卡、别名、双读和自动迁移均不存在。

合同绑定：

- forecast、章节拓扑、章节职责、可观察变化与读者价值；
- `failure|choice|cost|aftermath` 的显式 applicability；
- 当前人工批准的 Plot Node 表；
- 语义义务、承诺动作、保护项和禁止偏移。

写作链只有：

```text
human_chapter_intent_v3
-> chapter_story_brief_basis_v4
-> chapter_story_brief_v5
-> chapter_writing_task_v8
-> chapter_story_brief_renderer_v5
```

作者稿只展示可读拓扑、剧情义务、批准节点、人物选择、读者价值、必要事实与声音。内部 ID、hash、原始 RAG、Graph、SQLite、平台诊断和 Prompt 日志不得进入作者工作单。

共编使用 `chapter_coedit_session_v2`；人工终稿使用 `human_author_revision_v4`；最终深审使用 `human_story_review_v7`。任何 v0.9 schema 直接不兼容。

## 5. 章节关闭

final 后的 `canonical_delta_v1` 先生成 semantic ledger 和派生视图。语义应用不再自动推进承诺。

关闭前必须满足：

- 每个获批 state-changing event 已由人确认 `realized|deferred|cancelled`；realized 必须引用当前 final 的 Unicode 精确 span，并绑定 semantic ledger hash；
- 每个非 defer reader-promise 动作都有当前 final 精确 span、semantic ledger hash 和人工确认；
- defer 动作有逐章人工原因；
- gate、独立审稿、人工终稿、深审、任务 lineage 和作者声音门禁均为当前状态。

`chapter_closure_v2` 固化 final、semantic ledger、event ledger 与 reader-promise ledger hash。未完成时 `chapter close` 阻断。

## 6. 设定传播与版本化回溯

活动设定协议为：

```text
canonical_fact_v2
-> canon_change_proposal_v1
-> dependency_impact_v1
-> canon_change_semantic_review_v1
-> human_canon_change_decision_v1
```

依赖闭包只读取稳定事实 ID 和显式 dependency/obligation refs，不使用关键词猜测。确定性闭包中的 `must_stale` 不允许被语义审查或人工决定降级。

未来生效的变更事务化更新 `10_bible/canonical_facts.json`，并在 `30_state/stale_artifacts.json` 标记受影响 forecast、合同、节点、Story Brief 和任务。重新规划写入新证据和新 hash 后才能解除。

影响已定稿章节的变更不直接修改 mainline，而是创建覆盖 `E..当前头` 的 `revision_branch_v2`。提升分支时统一重建 semantic ledger、图谱、RAG、向量和 SQLite。

## 7. 人工读者反馈

`reader_feedback_batch_v1` 与 `human_reader_feedback_decision_v1` 永远位于 workbench，不能直接修改正文、承诺账本、平台策略或 canonical。被人接受的假设只能转换为 planning proposal 或 canon-change seed，再进入各自完整审批链。

## 8. 写入、恢复与派生状态

所有 canonical mutation 使用项目锁和 transaction v3：

```text
preparing -> prepared -> applied
                 \-> rolled_back
```

文件是事实源；SQLite、vector、RAG 和查询缓存可重建。普通命令发现 pending transaction 或 stale lock 时必须先路由 `recovery status`。恢复动作绑定精确报告 SHA 和人工审批，禁止手工删锁或快照。

## 9. 平台、权利与导出边界

平台注册表按目标和 `category_availability | submission_eligibility | signing_eligibility | incentive_eligibility | content_governance | rights_risk | disclosure_requirement` 分开保存来源、核对日期、下次复核日期、状态和未知项。`platform_publication_preflight_v2` 只报告当前配置、政策和权利状态，不预测平台接受，也不形成正文配额。

同人项目只有具体 `publication export --target` 会读取 `fanfiction_publication_rights_decision_v1`。决定必须为当前 `proceed` 并精确绑定有效配置、来源 Canon、逐来源权利声明和目标政策快照；缺失、`hold`、hash 漂移或政策复核过期只阻断该目标导出。该决定不是法律意见、授权或接受保证，原创项目不触发。
