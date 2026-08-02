# 通用人物表现与场景化叙事架构 Checklist

本文档归档 `longform-novel-engine` 的人物表现与场景化叙事改造。状态约定：`[x]` 表示已有代码与自动测试，`[~]` 表示工程链路已接入但仍缺真实生产证据或更深规则，`[ ]` 表示尚未完成。

- 实施日期：2026-08-01
- 适用版本：v0.3.0 working tree
- 边界：no-key Host Agent；不新增脚本内 LLM；不自动 finalize；不允许 Agent 直接写 Bible、outline、final、RAG、graph、TCS 或 SQLite。
- 回归样本：现有 15 章只作为后续 audit 输入，本轮不覆盖任何 final 正文。

## Phase 1. Canonical Character Expression Contract

- [x] 新增 `character_expression_profile_v1`，canonical 路径为 `10_bible/character_expression.json`。
- [x] 项目画像覆盖 narrative distance、expression mode、description density、dialogue mode、voice separation 和 ensemble mode。
- [x] 每个角色必须声明感知偏向、决策偏向、话语层级、会话策略、情绪泄漏、身体在场、社交面具、私人欲望、矛盾和反差对象。
- [x] 合同必须覆盖 `characters.json` 的全部稳定角色 ID，拒绝缺失、重复和越界引用。
- [x] 正/反例使用结构化 `voice_examples`，并明确样本是参照证据，不是机械复读模板。
- [x] 无效人物表达候选只写 validation/workbench，不污染 Bible、final、RAG、graph、TCS 或 SQLite。

## Phase 2. Book Design v2 And Compatibility

- [x] 新建 Book Design Agent 工单默认要求 `book_design_candidate_v2`。
- [x] v2 同时携带 `narrative_expression_profile` 与 `character_expression_contracts`，一次人工事务 apply 写入人物表达 Bible。
- [x] `book_design_candidate_v1` 继续可读、可校验、可 apply，不破坏旧项目。
- [x] v1 apply 会写入 `needs_character_expression_enrichment` 等价状态；纲要完成后 `production next` 指向 `character design-task`。
- [x] 历史项目若从未声明人物表达 marker，继续按 legacy 兼容路径运行；一旦存在新 marker 或 canonical 文件，就启用严格校验。
- [x] `character design-validate` 不写 canonical；`character design-apply --approved-by human` 使用事务和回滚。

## Phase 3. Chapter Character Performance Packet

- [x] 每章生成 `50_workbench/character_packets/chNNN.json`，schema 为 `character_expression_packet_v1`。
- [x] packet 包含 POV、featured characters、scene want、private pressure、voice state、allowed change、关系阶段、身体策略、表达画像和反差合同。
- [x] 每个写作 packet 最多嵌入两个已批准声音样本。
- [x] 写作 manifest 显式声明 packet，Agent 不需要扫描整个 Bible 或全部历史正文。
- [x] 常规原创写作任务继续保持最多 7 个输入文件和 20K 编译 brief 预算。
- [x] Codex/Claude 工作单新增 `Character Performance Packet` 区块，明确“人物差异不等于对白数量”。
- [x] packet 缺字段时只产生受控空值/保守策略，不允许猜测写回 canonical。

## Phase 4. Scene-Based Chapter Contract

- [x] 章节卡支持 `plot_obligation` 与 `dramatic_freedom` 分离。
- [x] 章节卡支持 `pov_character_id`、`featured_character_ids`、`characterization_focus`、`scene_wants` 和 `voice_state`。
- [x] 章节卡支持 `opposing_wants`、`hidden_agenda`、`relationship_move`、`irreversible_action` 与 `emotional_aftereffect`。
- [x] 章节卡支持 `embodiment_strategy` 和 `summary_scene_policy`，避免把“多写外貌”误当人物塑造。
- [x] Beat expansion 会把相反欲望、隐藏议程、不可逆行动和情绪余波传入每个场景材料要求。
- [~] `outline_design_candidate_v1` 仍只强制旧版连续章节字段；新的场景字段可携带但尚未升级为 outline v2 的强制 schema。
- [ ] 为悬疑、玄幻、都市、言情、群像分别建立 scene contract fixture，证明同一结构不会把题材写成同一种节奏。

## Phase 5. Humanizer v4 And Deterministic Diagnostics

- [x] Humanizer 两遍规则升级为 schema version 4。
- [x] Pass 1 继续清理 meta residue、模板句、意义膨胀、总结腔和固定动作。
- [x] Pass 2 明确保留人物感知/决策偏向、话语层级、社交面具与情绪泄漏。
- [x] Pass 2 强化相反欲望、隐藏议程、不可逆行动和情绪余波，不允许事实漂移。
- [x] 禁止用通用口头禅、强加方言、固定外貌段落或统一对白配额伪造差异。
- [x] 修正 `dialogue_ratio` 口径：新增真实 `dialogue_char_ratio` 与 `dialogue_mark_density`，保留可比较字段。
- [x] 诊断输出对白归因覆盖、说话人平均长度、疑问/命令/纠正比例、领域词汇和 swapability risk。
- [x] 诊断输出说明式对白、身体在场、内心活动和旁白解释指标。
- [x] 低对白在 `dialogue_mode=sparse` 时不会产生通用低对白警告；所有指标均为诊断证据，不是文学配额。
- [~] 说话人归因当前依赖姓名和常见发言动词；省略主语、连续轮替和复杂嵌套引语仍需要 Agent semantic review。

## Phase 6. Character Editor And Evidence Review

- [x] 编辑团队新增独立 `character_editor`，与 writing/anti-AI/serial/editorial 角色隔离上下文。
- [x] writing_agent 与 anti_ai_editor 的输入包含章节人物包。
- [x] character_editor 读取当前章节、章节卡、人物包、人物表达 Bible 和角色表，不读取其他编辑结果。
- [x] 新人物表达项目的第 1-3 章强制选择 character_editor。
- [x] 初登场、POV 切换、关系转折、对白/声音/人物/身体风险和反馈复发会选择 character_editor。
- [x] character_editor 不得提交空 pass；每个 featured character 即使通过也必须有正文证据和 character ID。
- [x] P0/P1 证据继续要求精确当前章节文本，aggregate 保留少数派阻断。
- [~] 相同人物风险跨章复发已能进入 feedback registry 并再次选角，但“第二次自动升为 P1”的专用计数规则尚未实现。

## Phase 7. Cross-Chapter Audit And Sample Governance

- [x] 新增 `character audit-task --from-chapter A --to-chapter B`。
- [x] `character_expression_review_v1` 覆盖 voice fit、swapability、character-as-function、embodied presence、narrator over-explains 和 dialogue-as-exposition。
- [x] 每个章节和每个被审人物都必须提供 hash-bound exact span；pass 也不能空审。
- [x] 范围 audit 缺少任一章节的 final/draft 来源时，任务创建立即失败并报告章号。
- [x] `audit-apply` 只归档到 `50_workbench/character_reviews/`，canonical targets 为空。
- [x] 新增 `character samples-approve --approved-by human`。
- [x] 声音样本只能来自 `40_manuscript/final/` 的当前 hash/span，使用事务写回表达 Bible。
- [x] Agent 不能自批样本，draft/repair/research inbox 片段不能进入批准样本库。
- [ ] 对现有 15 章运行真实 `character audit-task`，由 Host Agent 生成并 validate 审稿 JSON。
- [ ] 从修订后定稿章节人工批准第一批正/反例，记录为什么可复用而不是只保存台词。

## Phase 8. Public CLI And Skill Workflow

- [x] 新增 `character design-task|design-validate|design-apply`。
- [x] 新增 `character audit-task|audit-validate|audit-apply`。
- [x] 新增 `character samples-approve`。
- [x] `production next` 能在 v1 enrichment 场景给出唯一人物设计下一步。
- [x] Codex/Claude Skill 说明 Humanizer v4、人物包和非配额原则。
- [x] shared command protocol 增加 `/工程人物设计`、`/工程人物审稿` 和 `/工程人物样本批准`。
- [x] Skill references 已由 `scripts/sync_skill_references.py --write` 同步。
- [x] README 公开人物表现、场景合同、诊断口径和 CLI 入口。

## Phase 9. Automated Verification

- [x] Book Design v1 兼容并进入 enrichment。
- [x] Book Design v2 与人物表达在同一人工事务 apply。
- [x] 无效人物表达候选不污染 canonical。
- [x] 人物包进入写作 manifest 且保持 7 文件/20K 预算。
- [x] 高对白同质样本触发 swapability 与 dialogue-as-exposition risk。
- [x] sparse 对白画像不因对白少单独报警。
- [x] character_editor 空 pass 被拒绝，带 featured character 证据的 pass 可通过。
- [x] 跨章 audit 校验 hash/span，apply 后只增加 workbench 报告。
- [x] 声音样本要求人工批准、final 来源和精确 span。
- [ ] 增加不少于四种中文网文类型的合成 fixture 和至少一种群像 fixture。

## Phase 10. Real Production Evidence

- [ ] 冻结现有 15 章原稿 hash，保存人物/场景诊断基线，不修改原始 final。
- [ ] 完成 1-15 章 character audit，确认是否复现“人物特色弱、描写少、声音同质、叙事文感强”四类人工反馈。
- [ ] 选择 3 章做受控修订：首章、关系推进章、程序性信息最密章节。
- [ ] 以同一 Host Agent/模型对比修订前后人物辨识、场景具体性、对白可交换性、读者理解和 AI 味。
- [ ] 至少三名独立读者盲评，不能使用引擎自评分替代。
- [ ] 真实证据完成前，README 不宣称人物表现或文学质量已经优于 `novel-skill`。

## Validation Commands

```powershell
python scripts/sync_skill_references.py --check
python scripts/validate_skills.py
python scripts/release_surface_guards.py
python -m pytest tests/test_character_expression.py tests/test_intelligence_tasks.py tests/test_editorial_independence_and_feedback.py tests/test_creative_operator.py tests/test_gates.py tests/test_cli.py
python -m pytest
```

## Implementation Evidence (2026-08-01)

- [x] `python -m compileall -q src tests` 通过。
- [x] `python -m pytest tests/test_character_expression.py tests/test_intelligence_tasks.py -q`：27 passed。
- [x] 人物与同人兼容定向回归：15 passed；旧版 fanfiction design 在 outline 后显式进入 character expression enrichment。
- [x] `python scripts/sync_skill_references.py --check`：Codex/Claude references 同步。
- [x] `python scripts/build_resource_manifest.py --check`：资源哈希清单为当前版本。
- [x] `python scripts/validate_skills.py`：Skill packages validated。
- [x] `python scripts/release_surface_guards.py`：no-key、canonical 边界与发布面防线通过。
- [x] `python -m pytest -q`：297 passed，耗时 208.92 秒；仅保留既有 `pytest-asyncio` 配置弃用警告。
- [x] 本轮没有生成或提交小说正文、RAG、graph、TCS、SQLite 或 benchmark runtime 产物。

## Definition Of Done

- [~] 工程准入：人物合同、章节人物包、场景字段、诊断、人物编辑、跨章 audit、样本批准、CLI、Skill 和 no-pollution 测试全部通过。
- [ ] 生产准入：现有 15 章 audit 与三章受控修订完成，未出现 canonical 污染或上下文预算回退。
- [ ] 质量准入：独立盲评证明人物辨识和场景化叙事显著改善，且连续性、节奏和返修成本不退化。
