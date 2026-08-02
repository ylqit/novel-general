# 起点优先的平台写作适配 Checklist

本文档验收 `longform-novel-engine` 的平台写作适配层。状态含义：`[x]` 已有实现与自动测试，`[~]` 工具已具备但仍缺真实运行证据，`[ ]` 尚未完成。

本阶段只做写前与审稿质量适配，不导入平台运营数据，不模拟推荐算法，不实现自动发布。主合同固定为 `qidian_male`，`fanqie_free` 只提供非阻断兼容观察。

## 1. Single Source Of Truth

- [x] 平台、题材、故事阶段、人审风格基线和项目覆盖统一由 `compile_effective_quality_contract()` 编译。
- [x] `creative.pipeline` 中重复的硬编码平台合同已删除。
- [x] `quality_profile_v1` 支持 `updated_at`、`evidence_level`、`source_refs` 和 `heuristic_notes`。
- [x] 合并顺序固定为 market、genre、global phase、market phase、human-approved baseline、project overrides。
- [x] 后层标量覆盖前层，列表整体替换，不做隐式并集。
- [x] 编译结果记录 `merge_trace`、`overridden_fields`、来源路径和 SHA-256。
- [x] Agent 工作单使用压缩合同，不重复嵌入来源与完整合并轨迹。

## 2. Qidian Male Contract

- [x] `opening` 要求前三章用事件证明主角处境、能力边界和长期矛盾。
- [x] `early_serial` 要求第 4-30 章验证核心卖点的可持续发展、关系杠杆和中期承诺。
- [x] `stable_serial` 轮换调查、冲突、成长代价、关系、探索、兑现和余波职责。
- [x] `volume_climax` 兑现卷级承诺，并让新问题由既有因果产生。
- [x] `aftermath` 允许慢章与完整收束，但要求人物、关系、目标或读者认知发生变化。
- [x] 默认平台偏离只产生 `P2_advisory`，不改变事实、人物、伏笔等既有 P0/P1 门禁。
- [x] 项目可显式将主平台偏离升级为 `P1_blocking`，编译结果必须公开该覆盖。
- [x] 合同明确禁止固定短句率、固定对白率、每章固定爽点和强制悬崖结尾。

## 3. Fanqie Compatibility View

- [x] 模板支持 `quality.profile.compatibility_markets: [fanqie_free]`。
- [x] 番茄观察最多三项，覆盖开篇困境识别、具体收益、场景进入和慢章状态变化。
- [x] 所有兼容观察固定为 `P2`、`blocking: false`。
- [x] 兼容观察不会进入主合同合并，不会自动改章、触发 Humanizer 或阻断 finalize。
- [x] 主市场切换为番茄时，会自动忽略“与自身比较”的继承兼容项。
- [x] 比较合同只读，不写 Bible、outline、final、RAG、graph、TCS、SQLite 或项目运行状态。

## 4. Interfaces And Explanation

- [x] `quality contract project.yaml --chapter N --explain` 输出合并轨迹、覆盖字段和阻断策略。
- [x] `quality contract project.yaml --chapter N --compare-market fanqie_free` 输出非阻断差异。
- [x] JSON 保持 `effective_quality_contract_v1`，新增 `primary_market`、`market_phase`、`merge_trace`、`overridden_fields`、`compatibility_observations` 和 `blocking_policy`。
- [x] `quality.profile.compatibility_markets` 和平台偏离策略具有配置校验。
- [x] 默认 engine 配置与 `qidian-longform` 模板使用同一 `quality.profile` 结构。

## 5. Writing And Review Integration

- [x] 章节卡区分 `platform_promise`、`chapter_duty`、`reader_gain`、`cost` 和 `relationship_move`。
- [x] 写作 brief 携带压缩后的主合同、阻断策略和最多三条兼容建议。
- [x] writing manifest 核心输入不超过 7 个文件，常规 Markdown 工作单不超过 20K 字符。
- [x] Reader Payoff 读取统一合同，并根据当前正文证据判断承诺、收益与代价。
- [x] Humanizer 只处理表达、场景化和人物声音，不把平台画像解释成表面配额。
- [x] 规划主编检查平台承诺是否可持续，不要求每章固定兑现或悬崖。
- [x] 读者质量审稿检查中期追读动力，番茄兼容观察不能单独形成 P0/P1。
- [x] 平台建议只能进入工作单、审稿或 feedback，不直接更新 canonical 状态。

## 6. Automated Evidence

- [x] 五个起点阶段均有独立合同编译测试。
- [x] 合并覆盖、列表替换、来源哈希与冲突字段有自动测试。
- [x] 起点主合同与番茄比较视图的非污染、非阻断属性有自动测试。
- [x] 章节卡、写作 brief、Humanizer、Reader Payoff 和编辑角色联动有自动测试。
- [x] CLI `--explain` 与 `--compare-market` 有 smoke test。
- [x] Skill 校验、资源 manifest、release guards 和完整 pytest 属于发布前回归门禁。

## 7. Real Quality Evidence

- [~] 使用至少一种起点男频真实题材完成现行合同下的连续 5 章复跑。
- [ ] 使用玄幻、都市或悬疑中至少两种题材完成 10 章匿名盲评。
- [ ] 比较启用与关闭平台阶段合同后的承诺兑现、人物稳定、节奏和返修次数。
- [ ] 验证番茄兼容提示不会诱发短句化、对白配额化或强制悬崖同质化。
- [ ] 至少三名独立评审完成来源未知的正文评分。
- [ ] 在真实盲评完成前，README 不宣称文学质量或平台推荐效果已经全面超越 `novel-skill`。

## 8. Public Claim Boundary

- [x] 可以表述：平台规则已收敛为可解释、可覆盖、可测试的统一工程合同。
- [x] 可以表述：起点为主合同，番茄为非阻断兼容视图。
- [x] 不可表述：引擎掌握起点或番茄的私有推荐算法。
- [x] 不可表述：通过单元测试即可证明追读、订阅、推荐或文学质量提升。
- [x] 不可表述：当前已经在文学效果上全面超越 `novel-skill`。

## Validation Commands

```text
python -m pytest tests/test_platform_writing_adaptation.py tests/test_quality_contract_and_creative_interaction.py tests/test_reader_payoff_review.py
python -m pytest tests/test_cli.py::test_quality_contract_cli_explains_primary_and_compatibility_markets
python scripts/sync_skill_references.py --check
python scripts/validate_skills.py
python scripts/release_surface_guards.py
python -m pytest
```

## Definition Of Done

工程层完成要求第 1-6、8 节全部为 `[x]`。文学与平台效果完成要求第 7 节全部通过真实运行和匿名盲评；在此之前，只能宣称架构适配能力成立，不能宣称平台结果或文学质量已经领先。
