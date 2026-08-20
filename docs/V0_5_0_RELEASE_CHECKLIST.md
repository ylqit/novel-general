# longform-novel-engine v0.5.0 Release Checklist

v0.5.0 是不兼容的网文叙事协议升级。旧 `novels` 项目不迁移、不重写，也不作为发布能力证据。发布提交携带 v0.5.0 稳定元数据；远程 CI、tag workflow 与 Release assets 完成前不得宣告公开发布成功。

## 协议与叙事合同

- [x] Book Design 要求 `story_engine_contract_v1`，覆盖读者幻想、行动/成长/关系/问题循环、分阶段兑现、载体调色板与主题载体限制。
- [x] `chapter_contract_v3`、`chapter_direction_candidate_v4`、`chapter_writing_task_v4`、`chapter_story_brief_v2` 和 `structure_observation_v2` 已成为唯一当前协议。
- [x] 公共章节协议拒绝 `information_release` 及 v1 别名，改用 `chapter_turn`、`reveal_boundary` 和 `reader_gain`。
- [x] 同人方向保护原作结果、人物能动性和情绪归属；长期事实或保护结果改变必须进入改纲。

## 作者、编辑与人工审核

- [x] 作者 Markdown 只渲染 `chapter_story_brief_v2`；事实、承诺、因果模拟和编辑模式保持分层。
- [x] 最近五章载体诊断支持 3/5 P2、4/5 人工理由、`SERIAL_CARRIER_REPETITION` 与 `THEME_DISPLACES_EVENT`。
- [x] `scene_prose_editor` 每章必审，其他审稿角色只能追加。
- [x] `human_story_review_v2` 的 accept/repair/redirect 绑定候选、章节合同、承诺账本和因果模拟 hash；redirect 使用 transaction v3。
- [x] `quality_feedback` 公开概念已由纯内部 `editorial_pattern_registry` 取代；它不进入作者上下文、事实、RAG 或 Graph，也不替代当前候选门禁。
- [x] `literary_evidence_ready` 只由可校验 `literary_evidence_manifest_v1` 计算；当前没有三组真实证据，因此保持 `literary_evidence_ready=false`。

## 配置与覆盖来源

- [x] 新项目默认 `qidian_male`，`fanqie_free` 只提供 P2 兼容建议；显式番茄项目仍受支持。
- [x] 起点/番茄当前合同和阶段建议逐项绑定 `market_evidence_registry_v1` 的来源、日期、证据等级与执行级别，不推断推荐算法。
- [x] 统一章节路径继续使用 `chNNN.md`，四位及以上章节号自然扩展。
- [x] Codex/Claude Skill 与 shared 协议保持镜像一致。

## 统一章节路径

- [x] 正式章节、草稿、摘要和章节工件继续使用 `chNNN` 命名，旧 `novels` 示例不迁移。

## 内部质量证据

- [x] fact inventory、读者承诺、因果模拟、编辑 pattern、结构观察、编辑 finding 与人工 span 各自留在正确角色词汇边界。
- [x] 工程门禁不把合成测试、结构指标或单次 smoke 当成文学盲评。
- [x] `blind_review_pack_v3` 固定支持 `qidian_opening_3`、`fanqie_opening_3` 和 `serial_arc_15`，manifest 只保存 hash、聚合与结论，不保存正文。

## 发布前本地证据：单进程定向验证

- [x] 章节合同、承诺账本、因果模拟、Story Brief、场景审查、人工简审和盲评 manifest 定向测试通过。
- [x] redirect/改纲/rollback 故障注入证明 pattern、promise、simulation、任务、章节卡与 SQLite 精确恢复。
- [x] release surface、Skill 镜像和资源清单验证通过。

本地证据（2026-08-20）：单进程执行
`python -m pytest -q -x --tb=short tests/test_story_architecture_v050.py tests/test_editorial_independence_and_feedback.py tests/test_revision.py tests/test_blind_review.py tests/test_agent_skill_integrity.py tests/test_skills.py tests/test_protocol_convergence.py tests/test_quality_contract_and_creative_interaction.py tests/test_rolling_outline.py tests/test_fanfiction_workflow.py tests/test_orchestration.py tests/test_distribution.py`，退出码 `0`，结果 `120 passed`。随后 `python scripts/validate_skills.py`、Skill 镜像哈希核对、`git diff --check` 和旧公开语义扫描均通过；`novels/` 无改动。

本节只能在单进程定向验证实际完成后填写证据；不得预填通过数量或把接口实现当成验证成功。

## 远程发布证据

- [ ] Pull Request CI 全平台通过。
- [ ] master CI 通过后创建不可变 annotated tag `v0.5.0`。
- [ ] GitHub Release workflow 完成 wheel、sdist、审计、远程 tag 校验与 `SHA256SUMS`。
- [ ] GitHub Release assets 可下载；完成前不得宣告 v0.5.0 Release 成功。
