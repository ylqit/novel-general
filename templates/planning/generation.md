# 长篇滚动规划

依据批准设计和已发生正文，为当前待写章节起的滚动窗口创作完整规划。读取工作单声明的全部必要材料。已有角色、世界规则和来源 ID 必须复用；资料不足要明确报告，不编造已批准依据。规划描述是未来提案，正文及语义账本才说明已经发生的事实。

输出一个 `semantic_document_v1`。自然中文 `body` 说明全书、当前卷及近三章设计与人物动机。`extensions.planning_bundle` 按 `bundle_shape.json` 提供创作结构，shape 内的空字符串和零只是字段占位，不能作为真实候选提交。无需输出候选 hash、批准人、人工节点决定或生命周期；这些字段由控制面建立。机器字段包括所有 `candidate_sha256`、`basis_sha256`、`approved_by`、`human_decision`、`lifecycle`，必须省略，不能自行填写。

- 全书卷骨架覆盖长期故事，按实际设计决定卷的章节范围，不把五章试写当成全书。
- 活动卷 ID 与骨架一致。滚动窗口从本轮给定的待写章节开始，最多 20 章，不能跨出当前卷。
- 窗口前 3 章为 firm，随后至第 10 章为 directional，其后至第 20 章为 horizon。不存在的层写 null。chapter_forecasts 必须逐章覆盖整个窗口，chapter_contracts 与 plot_node_tables 仅覆盖 firm 章。
- 用自然语言表达情节职责、可观察变化与读者价值；不强迫所有章出现失败、重大代价、升级或悬念结尾。
- 规划按职责写精确短句，避免把同一背景、限制和情节在全书、卷、预测、合同、节点字段中反复长篇复述。全书主干保留全书内容，本卷记录本卷增量，各章记录本章增量；引用可以复用。候选后续必须作为单份审查材料进入当前上下文预算，请控制正文说明和字段重复，优先保证必要依赖完整。
- `failure`、`choice`、`cost`、`aftermath` 分别提供 `applicability`（required、optional、not_applicable）、description、reason。每个选择必须服务本章。
- 语义义务 domain 选 world、knowledge、relationship、character、resource、location、promise、reader_cognition。preconditions 的 type 为 deterministic、semantic、human，给出依据 ref 与必要说明。
- 每个 scene_id 的节点 sequence 分别从 1 连续递增，换场景后重新从 1 开始。节点 dependency_refs 只引用本章节点表中更早出现的节点，不跨章引用；跨章的因果前提写入 preconditions（type、ref、requirement），明确它是需要前章正文实现的未来前提，不能删除因果来消除错误。actors 为实际人物 ID；obligation_refs、forecast_ref、table_id 指向本包中的义务、预测、节点表。node_kind 为 micro 或 state_change；requirement 为 required、conditional、optional；非条件节点 condition 为 null。
- chapter_contracts 的 contract_id 必须为 contract:chNNN（如第 1 章是 contract:ch001）；topology 从本工作单给出的现行枚举中选择。卷骨架候选的 status 使用 proposed，不用 planned 或预先写 approved。批准人和活动卷生命周期仍由控制面处理。
- 本轮新建的规划 ID 使用小写英文字母起始，随后用字母、数字及分隔符 `: . _ -`。已经批准的人物或实体 ID 必须逐字复用，包括原有大小写；不得为了格式统一重命名这些引用，也不得把中文姓名当成内部人物 ID。
- `active_volume_plan.character_arcs` 用 subject_refs、source_refs 绑定实际人物和本卷设计依据，不能提供无从解析的虚构事实引用。
- 原创项目保留正式同人引用字段，但全部为空；禁止 fanfiction_projection。同人项目使用原任务中的连续性与路线合同及批准 claim；本章适用的能力转移必须绑定实际适配器及规则，不用未来规则补当前缺口。
- 读者承诺与伏笔必须遵守来源给出的当前合同。不得把尚未发生的计划写成已兑现事件；已有承诺、伤势、知识、关系和真实后果必须在本卷中保持。
- promise_threads 若非空，每项严格按下方 reader_promise_v2 字段示例填写，包括 selected_by:null。payoff_window 使用 earliest、target、latest 三个正章节数且递增；staged_payoffs 每项只含 stage_id、description、window。章节 reader_promise_actions 使用下方六个字段；partial_payoff/payoff 引用对应阶段，其他动作 stage_id:null；仅 defer 填 defer_reason，其他动作为 null。没有本章承诺动作时可以为空，不为填表虚构回报。

只写唯一声明输出，不写批准记录、正文、控制面数据或第二份文件。独立规划审查和逐节点人工决定在生成后单独进行。
