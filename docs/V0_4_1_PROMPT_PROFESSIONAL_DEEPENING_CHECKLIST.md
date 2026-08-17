# v0.4.1 中文小说 Prompt 逐项专业深化 Checklist

本文档逐项验收 27 个角色、12 个 Playbook 与 44 个故事分面，共 83 项。状态：`[x]` 未设计，`[~]` 已有基础但证据未闭合，`[x]` 独特内容、结构检查及正反边界微型校准全部通过。

每项必须在 `config/v041_release_acceptance_fixtures.yaml` 拥有唯一 fixture。实际区段、source hash、估算 units 与运行时预算由 readiness 的 `professional_prompt_inventory` 记录；任何一项未完成时 `professional_prompt_ready=false`。公共安全边界不参加重复度判断，真实章节盲评另设发布总门。

## Roles (27)

### role.chapter_author `[x]`
- 焦点：场景目标、阻力、选择、状态变化和余波形成连续正文，而非叙事摘要。
- [x] 独特深化与结构检查；[x] 校准 `role.chapter_author`；[x] hash/预算证据。

### role.repair_author `[x]`
- 焦点：finding 根因、最小修复半径、保护项和回归风险。
- [x] 独特深化与结构检查；[x] 校准 `role.repair_author`；[x] hash/预算证据。

### role.expansion_writer `[x]`
- 焦点：只扩展会改变信息、关系、资源、风险或决定的功能性场景。
- [x] 独特深化与结构检查；[x] 校准 `role.expansion_writer`；[x] hash/预算证据。

### role.humanizer `[x]`
- 焦点：保持事实等价，清理功能重复并恢复人物化表达。
- [x] 独特深化与结构检查；[x] 校准 `role.humanizer`；[x] hash/预算证据。

### role.chapter_story_editor `[x]`
- 焦点：提出因果真正不同的章节方向，并明确收益、代价和后续依赖。
- [x] 独特深化与结构检查；[x] 校准 `role.chapter_story_editor`；[x] hash/预算证据。

### role.character_performance_architect `[x]`
- 焦点：按关系对象设计注意、阈值、话语策略、身体泄漏和失控修复。
- [x] 独特深化与结构检查；[x] 校准 `role.character_performance_architect`；[x] hash/预算证据。

### role.book_architect `[x]`
- 焦点：可持续卖点、目标阶梯、冲突引擎、主角缺陷和结局边界。
- [x] 独特深化与结构检查；[x] 校准 `role.book_architect`；[x] hash/预算证据。

### role.longform_outline_architect `[x]`
- 焦点：字数预算、故事弧承诺、卷级升级和滚动章节窗口。
- [x] 独特深化与结构检查；[x] 校准 `role.longform_outline_architect`；[x] hash/预算证据。

### role.continuity_outline_editor `[x]`
- 焦点：改纲依赖、人物弧、伏笔窗口、陈旧计划和保留范围。
- [x] 独特深化与结构检查；[x] 校准 `role.continuity_outline_editor`；[x] hash/预算证据。

### role.creative_facilitator `[x]`
- 焦点：单一决策变量、真实互斥选项、叙事代价和人工选择。
- [x] 独特深化与结构检查；[x] 校准 `role.creative_facilitator`；[x] hash/预算证据。

### role.fanfiction_architect `[x]`
- 焦点：canon 截止点、分歧、蝴蝶效应、原创主线和 OOC 容忍度。
- [x] 独特深化与结构检查；[x] 校准 `role.fanfiction_architect`；[x] hash/预算证据。

### role.fanfiction_canon_archivist `[x]`
- 焦点：来源事实、时间线、角色声音、证据定位和不确定性。
- [x] 独特深化与结构检查；[x] 校准 `role.fanfiction_canon_archivist`；[x] hash/预算证据。

### role.canonical_semantic_archivist `[x]`
- 焦点：章节事实抽取与设计文档编译分轨，原子 delta 不越权推断。
- [x] 独特深化与结构检查；[x] 校准 `role.canonical_semantic_archivist`；[x] hash/预算证据。

### role.research_synthesizer `[x]`
- 焦点：claim、来源 hash、span、证据强度、冲突来源和未知项。
- [x] 独特深化与结构检查；[x] 校准 `role.research_synthesizer`；[x] hash/预算证据。

### role.semantic_style_analyst `[x]`
- 焦点：统计指纹与语义风格分离，描述习惯而非模仿具体作者。
- [x] 独特深化与结构检查；[x] 校准 `role.semantic_style_analyst`；[x] hash/预算证据。

### role.adaptation_analyst `[x]`
- 焦点：技法机制、迁移条件、失效边界与来源表达复制风险。
- [x] 独特深化与结构检查；[x] 校准 `role.adaptation_analyst`；[x] hash/预算证据。

### role.anti_ai_editor `[x]`
- 焦点：功能重复、总结腔、伪细节、意义膨胀和全员同声。
- [x] finding 成立/证据/分级/误报/repair；[x] 校准 `role.anti_ai_editor`；[x] hash/预算证据。

### role.canon_fidelity_reviewer `[x]`
- 焦点：canon 事实、人物价值排序、分歧因果、能力规则和原创贡献。
- [x] finding 成立/证据/分级/误报/repair；[x] 校准 `role.canon_fidelity_reviewer`；[x] hash/预算证据。

### role.character_editor `[x]`
- 焦点：单章人物欲望、可拒绝项、对白目的、身体反应和配角自主性。
- [x] finding 成立/证据/分级/误报/repair；[x] 校准 `role.character_editor`；[x] hash/预算证据。

### role.character_performance_reviewer `[x]`
- 焦点：跨章稳定内核、关系对象差异、成长触发和声音坍缩。
- [x] finding 成立/证据/分级/误报/repair；[x] 校准 `role.character_performance_reviewer`；[x] hash/预算证据。

### role.humanizer_semantic_reviewer `[x]`
- 焦点：双稿事实等价、知识边界、关系结果、线索和语义锚点。
- [x] finding 成立/证据/分级/误报/repair；[x] 校准 `role.humanizer_semantic_reviewer`；[x] hash/预算证据。

### role.planning_chief_editor `[x]`
- 焦点：章节职责、上游承诺、因果依赖和中期连载可持续性。
- [x] finding 成立/证据/分级/误报/repair；[x] 校准 `role.planning_chief_editor`；[x] hash/预算证据。

### role.reader_payoff_reviewer `[x]`
- 焦点：正文实际收益、人物代价、承诺推进和虚假兑现。
- [x] finding 成立/证据/分级/误报/repair；[x] 校准 `role.reader_payoff_reviewer`；[x] hash/预算证据。

### role.reader_experience_editor `[x]`
- 焦点：读者当下能否理解人物在做什么、为何行动及关键因果。
- [x] finding 成立/证据/分级/误报/repair；[x] 校准 `role.reader_experience_editor`；[x] hash/预算证据。

### role.scene_prose_editor `[x]`
- 焦点：关键转折是否被场景化，空间、动作、对白与心理是否协同。
- [x] finding 成立/证据/分级/误报/repair；[x] 校准 `role.scene_prose_editor`；[x] hash/预算证据。

### role.semantic_continuity_reviewer `[x]`
- 焦点：知识、动机、关系、空间、时间、能力和伏笔的前置状态核对。
- [x] finding 成立/证据/分级/误报/repair；[x] 校准 `role.semantic_continuity_reviewer`；[x] hash/预算证据。

### role.semantic_pacing_reviewer `[x]`
- 焦点：压力、释放、转折、余波、职责重复与跨章疲劳。
- [x] finding 成立/证据/分级/误报/repair；[x] 校准 `role.semantic_pacing_reviewer`；[x] hash/预算证据。

## Playbooks (12)

### playbook.opening_and_mainline `[x]`
- 焦点：目标阶梯、前三章承诺和延迟揭示边界；校准 `playbook.opening_and_mainline`。
- [x] 创作/诊断/修复/保护项；[x] 三类微例；[x] hash/预算证据。

### playbook.scene_causality `[x]`
- 焦点：进入条件、阻力、选择、状态变化和余波；校准 `playbook.scene_causality`。
- [x] 创作/诊断/修复/保护项；[x] 三类微例；[x] hash/预算证据。

### playbook.character_agency `[x]`
- 焦点：欲望、拒绝权、主动选择和关系后果；校准 `playbook.character_agency`。
- [x] 创作/诊断/修复/保护项；[x] 三类微例；[x] hash/预算证据。

### playbook.dialogue_and_subtext `[x]`
- 焦点：说话目的、应答链、信息保留和 speaker 归属；校准 `playbook.dialogue_and_subtext`。
- [x] 创作/诊断/修复/保护项；[x] 三类微例；[x] hash/预算证据。

### playbook.interiority_and_emotion `[x]`
- 焦点：刺激、解释、阈值、选择和情绪余波；校准 `playbook.interiority_and_emotion`。
- [x] 创作/诊断/修复/保护项；[x] 三类微例；[x] hash/预算证据。

### playbook.world_rules_and_exposition `[x]`
- 焦点：规则证明、能力代价和信息投放；校准 `playbook.world_rules_and_exposition`。
- [x] 创作/诊断/修复/保护项；[x] 三类微例；[x] hash/预算证据。

### playbook.relationship_dynamics `[x]`
- 焦点：双方目标、边界、杠杆和关系状态迁移；校准 `playbook.relationship_dynamics`。
- [x] 创作/诊断/修复/保护项；[x] 三类微例；[x] hash/预算证据。

### playbook.foreshadow_and_mystery `[x]`
- 焦点：表面问题、回响、误导、重组和兑现；校准 `playbook.foreshadow_and_mystery`。
- [x] 创作/诊断/修复/保护项；[x] 三类微例；[x] hash/预算证据。

### playbook.serial_pacing `[x]`
- 焦点：章节职责、压力释放、兑现和长连载疲劳；校准 `playbook.serial_pacing`。
- [x] 创作/诊断/修复/保护项；[x] 三类微例；[x] hash/预算证据。

### playbook.anti_ai_expression `[x]`
- 焦点：功能重复、总结腔、伪细节、同声对白和误报控制；校准 `playbook.anti_ai_expression`。
- [x] 创作/诊断/修复/保护项；[x] 三类微例；[x] hash/预算证据。

### playbook.ensemble_and_viewpoint `[x]`
- 焦点：视角权限、群像能动性和信息差；校准 `playbook.ensemble_and_viewpoint`。
- [x] 创作/诊断/修复/保护项；[x] 三类微例；[x] hash/预算证据。

### playbook.fanfiction_canon `[x]`
- 焦点：canon 截止点、OOC、分歧因果和原创贡献；校准 `playbook.fanfiction_canon`。
- [x] 创作/诊断/修复/保护项；[x] 三类微例；[x] hash/预算证据。

## Story Facets (44)

### facet.setting.xuanhuan `[x]`
- 焦点：阶序、资源、规则代价和越阶后果；[x] 校准 `facet.setting.xuanhuan` 与 hash/预算证据。
### facet.setting.xianxia `[x]`
- 焦点：修行资源、时间、因果债和道途选择；[x] 校准 `facet.setting.xianxia` 与 hash/预算证据。
### facet.setting.wuxia `[x]`
- 焦点：门派、江湖声望、招式意图和承诺；[x] 校准 `facet.setting.wuxia` 与 hash/预算证据。
### facet.setting.urban `[x]`
- 焦点：职业流程、权限、金钱和社会反应；[x] 校准 `facet.setting.urban` 与 hash/预算证据。
### facet.setting.history `[x]`
- 焦点：制度、交通、信息速度、身份和时代认知；[x] 校准 `facet.setting.history` 与 hash/预算证据。
### facet.setting.science_fiction `[x]`
- 焦点：假设、约束、失效条件和社会后果；[x] 校准 `facet.setting.science_fiction` 与 hash/预算证据。
### facet.setting.game_fantasy `[x]`
- 焦点：界面规则、玩家知识、失败反馈和 NPC 能动性；[x] 校准 `facet.setting.game_fantasy` 与 hash/预算证据。

### facet.plot_engines.progression `[x]`
- 焦点：获得、试用、代价、整合和责任；[x] 校准 `facet.plot_engines.progression` 与 hash/预算证据。
### facet.plot_engines.survival `[x]`
- 焦点：威胁、资源、时间、退路和群体分配；[x] 校准 `facet.plot_engines.survival` 与 hash/预算证据。
### facet.plot_engines.revenge `[x]`
- 焦点：对象、证据、道德边界和追索代价；[x] 校准 `facet.plot_engines.revenge` 与 hash/预算证据。
### facet.plot_engines.mystery `[x]`
- 焦点：发现、证据、解释、行动验证和答案公平性；[x] 校准 `facet.plot_engines.mystery` 与 hash/预算证据。
### facet.plot_engines.political_intrigue `[x]`
- 焦点：资源、权限、信息差、承诺和可兑现筹码；[x] 校准 `facet.plot_engines.political_intrigue` 与 hash/预算证据。
### facet.plot_engines.war `[x]`
- 焦点：命令、情报延迟、补给、责任链和战术后果；[x] 校准 `facet.plot_engines.war` 与 hash/预算证据。
### facet.plot_engines.business `[x]`
- 焦点：客户价值、现金流、合同、执行和竞争反应；[x] 校准 `facet.plot_engines.business` 与 hash/预算证据。
### facet.plot_engines.romance `[x]`
- 焦点：双方欲望、边界、误读、行动和关系承诺；[x] 校准 `facet.plot_engines.romance` 与 hash/预算证据。

### facet.narrative_forms.light_novel `[x]`
- 焦点：互动节拍、人物反应、心理作用和轻快因果；[x] 校准 `facet.narrative_forms.light_novel` 与 hash/预算证据。
### facet.narrative_forms.ensemble `[x]`
- 焦点：成员目标、拒绝能力、贡献和能动性轮换；[x] 校准 `facet.narrative_forms.ensemble` 与 hash/预算证据。
### facet.narrative_forms.single_lead `[x]`
- 焦点：稳定视角锚点与配角独立行动；[x] 校准 `facet.narrative_forms.single_lead` 与 hash/预算证据。
### facet.narrative_forms.single_lead_only `[x]`
- 焦点：主角承担关键选择但不垄断他人能力；[x] 校准 `facet.narrative_forms.single_lead_only` 与 hash/预算证据。
### facet.narrative_forms.multi_pov `[x]`
- 焦点：视角认知偏差、独有问题和切换价值；[x] 校准 `facet.narrative_forms.multi_pov` 与 hash/预算证据。
### facet.narrative_forms.episodic `[x]`
- 焦点：单元闭环与跨单元状态增量；[x] 校准 `facet.narrative_forms.episodic` 与 hash/预算证据。
### facet.narrative_forms.road_novel `[x]`
- 焦点：地点改变资源、规则、关系和人物认知；[x] 校准 `facet.narrative_forms.road_novel` 与 hash/预算证据。

### facet.premise_devices.transmigration `[x]`
- 焦点：身份记忆、身体处境、知识失效和适应成本；[x] 校准 `facet.premise_devices.transmigration` 与 hash/预算证据。
### facet.premise_devices.rebirth `[x]`
- 焦点：记忆优势、未来可靠度、改写反馈和新代价；[x] 校准 `facet.premise_devices.rebirth` 与 hash/预算证据。
### facet.premise_devices.system `[x]`
- 焦点：系统能力、未知边界、失败条件和角色自主性；[x] 校准 `facet.premise_devices.system` 与 hash/预算证据。
### facet.premise_devices.no_system `[x]`
- 焦点：目标、资源和反馈均来自世界因果；[x] 校准 `facet.premise_devices.no_system` 与 hash/预算证据。
### facet.premise_devices.time_loop `[x]`
- 焦点：重置范围、记忆、不可逆代价和每轮差异；[x] 校准 `facet.premise_devices.time_loop` 与 hash/预算证据。
### facet.premise_devices.infinite_flow `[x]`
- 焦点：副本边界、失败代价、跨本债务和长期变化；[x] 校准 `facet.premise_devices.infinite_flow` 与 hash/预算证据。
### facet.premise_devices.identity_swap `[x]`
- 焦点：身体习惯、社会记忆、权限和暴露风险；[x] 校准 `facet.premise_devices.identity_swap` 与 hash/预算证据。

### facet.relationship_modes.romance `[x]`
- 焦点：双方目标、边界、吸引轨迹和拒绝权；[x] 校准 `facet.relationship_modes.romance` 与 hash/预算证据。
### facet.relationship_modes.friendship `[x]`
- 焦点：支持、反对、共同承诺和利益分歧；[x] 校准 `facet.relationship_modes.friendship` 与 hash/预算证据。
### facet.relationship_modes.family `[x]`
- 焦点：责任、控制、记忆、爱与怨并存；[x] 校准 `facet.relationship_modes.family` 与 hash/预算证据。
### facet.relationship_modes.team `[x]`
- 焦点：专长、信息、决策权和协作摩擦；[x] 校准 `facet.relationship_modes.team` 与 hash/预算证据。
### facet.relationship_modes.rivalry `[x]`
- 焦点：独立标准、成长路线、胜负规则和相互校准；[x] 校准 `facet.relationship_modes.rivalry` 与 hash/预算证据。
### facet.relationship_modes.master_disciple `[x]`
- 焦点：知识、权力边界、传承和独立判断；[x] 校准 `facet.relationship_modes.master_disciple` 与 hash/预算证据。

### facet.tone.adventure `[x]`
- 焦点：未知、路线选择、风险和认知变化；[x] 校准 `facet.tone.adventure` 与 hash/预算证据。
### facet.tone.suspense `[x]`
- 焦点：信息缺口、风险逼近、证据和可回答问题；[x] 校准 `facet.tone.suspense` 与 hash/预算证据。
### facet.tone.lighthearted `[x]`
- 焦点：互动、错位和恢复节拍不抹除真实后果；[x] 校准 `facet.tone.lighthearted` 与 hash/预算证据。
### facet.tone.humorous `[x]`
- 焦点：预期偏差、身份错位、关系默契和后续作用；[x] 校准 `facet.tone.humorous` 与 hash/预算证据。
### facet.tone.dark `[x]`
- 焦点：约束变窄、道德压力和仍然存在的选择；[x] 校准 `facet.tone.dark` 与 hash/预算证据。
### facet.tone.unrelieved_dark `[x]`
- 焦点：希望、关系和选择空间持续收窄但不机械虐待；[x] 校准 `facet.tone.unrelieved_dark` 与 hash/预算证据。
### facet.tone.hot_blooded `[x]`
- 焦点：前置行动、共同风险、承诺和代价；[x] 校准 `facet.tone.hot_blooded` 与 hash/预算证据。
### facet.tone.warm `[x]`
- 焦点：记住细节、承担劳动、尊重边界和关系分歧；[x] 校准 `facet.tone.warm` 与 hash/预算证据。
### facet.tone.tragic `[x]`
- 焦点：选择、现实约束、代价累积和不可撤销变化；[x] 校准 `facet.tone.tragic` 与 hash/预算证据。

## Global Gates

- [x] 83 项均在单一 YAML fixture 中拥有正例、反例和边界例。
- [x] readiness 输出 `professional_prompt_ready=true` 和完整 inventory。
- [x] 运行时最多加载一个角色、三个 Playbook、三个故事分面，校准内容不进入普通工作单。
- [x] 不新增 Agent 输出 Schema、脚本内 LLM、多进程或测试文件。
- [x] 完整单进程 pytest、Skill 同步、资源 manifest、readiness 和 release guards 通过。
- [x] 协议稳定版允许发布；六类混合题材人工盲评完成前保持“高级专业候选 Prompt”，并继续阻止任何文学质量领先声明。

## Verification Evidence

- readiness：12/12 检查通过，`professional_prompt_ready=true`，inventory 为 27 roles + 12 Playbooks + 44 facets = 83。
- 校准：单一 `config/v041_release_acceptance_fixtures.yaml` 保存 249 条互不重复的正例、反例和边界例。
- 渐进加载：代表任务记录实际角色区段、Playbook 区段、selection hash 与 estimated units；校准和 reference 区段不进入普通工作单。
- 自动验证：最终发布候选回归为 `293 passed in 619.84s`；Skill 引用同步、Skill 校验、resource manifest、readiness 与 release guards 均通过。
- 发布边界：六类混合题材人工盲评尚未完成；这不再阻止 v0.4.1 作为协议、Prompt、Schema 与上下文工程稳定版发布，但继续阻止“高级专业文学效果”或“全面优于 novel-skill”的声明。
