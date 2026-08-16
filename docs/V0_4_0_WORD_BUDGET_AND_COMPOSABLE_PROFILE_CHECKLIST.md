# v0.4.0 字数主导与混合题材写作验收清单

本清单记录 v0.4.0 的实现、工程发布准入与发布后质量证据。状态含义：`[x]` 已有实现和自动测试证据，`[~]` 已实现主要接口但仍缺完整规模或真实生产证据，`[ ]` 尚未完成。fixture、合成规模测试和协议通过不等于文学质量通过；两组真实五章盲评继续保留为发布后证据，不阻断本次工程架构版发布。

## Phase 0. 协议边界

- [x] 源码与公开稳定安装版本统一为 `0.4.0` / `v0.4.0`。
- [x] `schema_version: 2` 是 v0.4.0 项目配置唯一入口。
- [x] v0.3.x 的固定章节数、固定卷数和旧 `quality.profile.genre` 配置被明确拒绝。
- [x] v0.3.2 Release、Skill 内容和历史失败证据不被修改。
- [x] `config/release-channel.json` 将 `v0.4.0` 标记为 stable，并把未完成盲评记录为 deferred quality evidence。

## Phase 1. 统一字数口径

- [x] `content_characters_v1` 只统计正文 Unicode 字母和数字。
- [x] `display_characters` 只作为诊断指标，不驱动生产规模。
- [x] 标题、空白、标点、Markdown 标记、工作单和审稿产物不进入正文字符数。
- [x] 字数计算收敛到 `src/longform_engine/text_metrics.py`，业务模块不再各自实现估算函数。
- [x] 扩写工作单、CLI 和 gate finding 使用“正文字符数”命名，不把字符数误称为英文 word count。
- [x] 10 万、100 万、200 万和 300 万配置均有 forecast 测试。
- [x] 200 万以内标记为 formal；超过 200 万标记为 experimental。

## Phase 2. 字数主导完成条件

- [x] `length_contract_v2` 以 `target_total_characters` 为第一规模事实源。
- [x] 章节数、合理章节范围和卷数只由合同动态 forecast。
- [x] 默认 200 万、每章 3000 字预测约 667 章，软区间约 556 至 834 章，卷预测约 8 卷。
- [x] 全书完成要求人工批准结局、必要承诺闭环、进入字数容差且无 P0/P1。
- [x] 低于目标时返回“人工批准扩展故事弧”，禁止自动注水。
- [x] 高于目标时返回重估提示，不强制压缩已成立剧情。
- [x] 常规 `production next` 使用常数规模投影，不为判断完成状态遍历全部正文。

## Phase 3. 滚动纲要

- [x] `outline_design_candidate_v2` 只保存全书故事弧、卷级字符预算和首个详细窗口。
- [x] 详细窗口默认不超过 20 章。
- [x] 剩余计划不超过 8 章时创建 `outline_extension_candidate_v1`。
- [x] 扩展任务只生成声明范围，不要求一次铺满预计 667 章。
- [x] 最终章节数允许随平均章长、故事密度和人工改纲变化。
- [x] 伏笔计划支持 `arc_id + progress_window`，运行时解析为当前窗口。
- [x] 故事阶段优先读取人工批准故事弧，缺失时才读取正文字符进度。
- [x] 200 万 forecast fixture 的 outline context 保持在 18K 预算内。
- [~] 200 万真实项目中的滚动补窗、改纲、完结重估仍缺全链路运行证据。

## Phase 4. 可组合故事画像

- [x] 删除单值 `quality.profile.genre` 事实源。
- [x] 支持 market、setting、plot engines、narrative forms、premise devices、relationship modes、tone 和 creation mode 正交表达。
- [x] setting 与剧情引擎支持 `primary/supporting/accent` 优先级。
- [x] 每个分面输出可追溯来源、SHA-256、requirements、preferences、risks 和 review questions。
- [x] 冲突不按数组顺序覆盖，未解决冲突会阻止合同 ready。
- [x] 人工 resolution 必须含 conflict ID、decision 和 rationale；无对应冲突的多余 resolution 也会被拒绝。
- [x] 工作单每章最多激活三个相关分面。
- [x] 质量合同按市场、各故事分面、当前故事弧、人工风格和项目覆盖的固定语义顺序编译。

## Phase 5. 章节写作与审稿

- [x] 每章强制生成 `chapter_direction_candidate_v2`，不再只在特殊节点触发。
- [x] 每个方向提供 2 至 3 个选择及各自代价，并要求用户显式选择。
- [x] 方向合同包含全书目标、卷级目标、主角目标、场景链、角色欲望、关系变化、对白归属、主线和伏笔回响。
- [x] 写作角色要求开篇按故事弧呈现世界、处境、近期目标、长期方向和行动动机。
- [x] 写作与人物审稿检查主角主动选择、配角姓名与私欲、独立反应和连续对白归属。
- [x] 人物审稿继续要求每个 featured character 的精确正文证据。
- [x] 空 pass 被拒绝；证据不足必须返回 `unknown` 或 `insufficient_evidence`，不能用零 finding 冒充通过。
- [x] Humanizer 不把固定短句率、对白率、快节奏或悬崖结尾当成平台配额。

## Phase 6. Fixture 与规模测试

- [x] 单类型 fixture 覆盖玄幻成长、都市职业和武侠冒险。
- [x] 三分面 fixture 覆盖都市、穿越和轻小说。
- [x] 多引擎 fixture 覆盖玄幻、调查、群像和言情。
- [x] 同人 fixture 覆盖游戏异界、求生、轻小说、群像和同人。
- [x] 复杂 fixture 覆盖历史、武侠、权谋、多视角和感情线。
- [x] 冲突 fixture 验证互斥 POV、语气和结构必须进入人工处理。
- [x] pairwise 检查保证登记分面的两两组合至少被一个 fixture 覆盖。
- [x] 200 万规模以 667 章 fixture 验证 task index、`local_hnsw` RAG、伏笔最近动作、角色当前状态、TCS 和滚动任务保持有界；`local_sqlite` 在该规模不冒充正式向量后端。
- [ ] 使用第一种混合题材完成真实五章生产和匿名盲评。
- [ ] 使用第二种不同混合题材完成真实五章生产和匿名盲评。
- [ ] SAO v0.3.2 五章只保留为失败样本，不得重写或计作 v0.4.0 通过证据。

## Phase 7. 文档、证据与发布

- [x] README 将公开稳定版和安装命令统一到 `v0.4.0`。
- [x] README 与配置文档说明字数口径、滚动纲要、混合分面和每章人工方向。
- [x] 配置模板使用 v0.4.0 Schema，不再展示固定章节数和单值 genre。
- [x] Phase 6 readiness 证据已记录 `435 passed`、当前协议/角色 hash、Reader Payoff 三输入预算，并通过资源 manifest 与 release guards 校验。
- [ ] 两组混合题材五章盲评完成，且问题与评分可复核；该项是发布后文学证据，不用于本次工程发布准入。
- [x] 200 万正式工程规模综合测试完成；证据为合成工程 fixture，不替代真实中文正文或文学盲评。
- [x] 正式 `v0.4.0` wheel/sdist 构建和资源审计通过；全新临时 pipx `[semantic]` 环境中 Engine、双 Skill、doctor、模板、新项目和首个严格工作单均通过。
- [x] README 不含未经证据支持的“文学质量全面超越 novel-skill”声明。
- [x] 用户已明确授权创建 release commit、`v0.4.0` tag 和 GitHub Release。

## Required Commands

```powershell
python -m compileall -q src tests
python -m pytest
python scripts/sync_skill_references.py --check
python scripts/build_resource_manifest.py --check
python scripts/validate_skills.py
python scripts/check_agent_data_pipeline_readiness.py
python scripts/release_surface_guards.py
python -m build
```

## Engineering Release Definition Of Done

- [x] 200 万字符规模合成工程证据、滚动纲要和混合题材合同通过自动验证。
- [x] 发布继续保持 no-key Host Agent、无脚本内 LLM 和 canonical 显式写入边界。
- [~] 正式 `v0.4.0` wheel/sdist、资源审计和本地 pipx 安装通过；tag、GitHub Release 和远程 tag 安装等待发布提交。

## Post-release Literary Evidence

- [ ] 新项目可只给出总字符目标和混合故事画像，完成开书、滚动纲要、逐章人工方向、写作、审稿、显式定稿与语义关闭。
- [ ] 章节和卷数可随真实篇幅重估，不因旧固定上限提前完结或填充占位章。
- [ ] 任何题材组合只激活本章必要的最多三个分面，不造成 Prompt 标签堆积。
- [ ] 两组真实五章盲评和 200 万规模证据全部通过。
- [x] 发布文档明确禁止在盲评完成前宣称文学质量全面优于 `novel-skill`。
