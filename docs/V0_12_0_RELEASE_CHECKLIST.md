# v0.12.0 发布 Checklist

> 状态：本轮同人创作链重构和全部本地发布门禁已完成，远程 CI、不可变 tag、Release 制品与本机同步仍以下方未勾选项为准。原 v0.12–v0.14 分阶段方案从未发布；先前 `466 passed` 仅是上一阶段历史证据，本轮最终证据为 `471 passed in 1289.01s`。

## 协议收口

- [x] 公共边界只使用 `artifact_envelope_v1`、`source_asset_v1`、`evidence_reference_v1`、`workflow_record_v1`、`semantic_document_v1`、`human_decision_v1`、`agent_task_manifest_v5` 及现有事务/修订协议。
- [x] 人物、事件、关系、能力、外观、同人路线和跨界规则使用开放中文语义正文；未知文档类型和扩展字段无需修改 Schema。
- [x] LLM 候选、独立复核、人工决定和事务化 Canon apply 的权限边界通过验证。
- [x] `fanfiction_source_canon_v1/v2/v3` 与旧固定覆盖协议明确拒绝，不双读、不静默迁移。
- [x] v0.11 只支持 `migrate audit-v011` 与非原地 `migrate v011-to-v012`；旧事实降为待审语义重建候选，已定稿正文哈希不变。

## 资料、语义引擎与小说生产

- [x] Markdown/TXT/JSON/YAML/PDF/DOCX/JPG/PNG/漫画页/ASS/SSA/SRT/MP3/WAV/MP4/MKV 均可形成带原始位置的证据，能力不足时显式阻断。
- [x] Host Agent 整件媒体优先能力协商与确定性分段降级均可回溯；直接观察与解释推断严格分开。
- [x] 搜索授权、动态 `identity/design_core/volume_scope/chapter_dependency/whole_to_cutoff` 覆盖及三个活动检索域隔离通过本轮验证；不宣称已交付同人样本检索域。
- [x] 原著基线、同人连续性、小说故事 Canon 分层；项目 Canon 人工 apply 后才更新项目图谱、RAG 与 SQLite。
- [x] 创作沙盒不会改变 Canon；提升后仍只是待复核候选。
- [x] 全书方向、活动卷、重大节点人工审批、最近三章 firm 合同、Story Brief、事件/承诺核销和滚动重规划通过验证。
- [x] 人物理解按时期、知识边界、关系阶段与压力条件工作；故事发动机、原著事件命运、动态跨界规则、独立路线复核及原著一致性/同人创造性双轴审查通过本轮验证。
- [x] `fanfiction_context_bundle_v1` 使用稳定 claim 依赖、命名空间/时间/知识过滤和 Token 预算；姓名匹配、固定条数及无命中前几条回退已移除并有验收测试。
- [x] 因果、人物选择、场景具体性、对白声音、信息释放、节奏重复与文句修订可分目标执行，不包含 AI 检测规避或平台必过逻辑。

## 版本与活动文档

- [x] 包版本、运行时、release channel、README、活动架构/存储/配置/流程文档和两套仓库 Skill 均为 `0.12.0` 正式协议。
- [x] `docs/V0_12_SEMANTIC_ARCHITECTURE.md`、迁移说明和本 release note 与代码一致。
- [x] v0.13/v0.14 三阶段预备文档已删除，历史已发布文档保持不变。
- [x] 资源 manifest、Skill 引用副本与生成源一致。

## 本地发布验证

- [x] Ruff 通过。
- [x] 17 个关键 Mypy 模块通过。
- [x] 完整 pytest 以单进程通过：`471 passed in 1289.01s (0:21:29)`。
- [x] Skill/资源 manifest、引用一致性、Markdown 链接、Agent readiness 和 release surface guards 通过。
- [x] wheel 与 sdist 构建审计通过：wheel 201 项，sdist 314 项。
- [x] Python 3.12.2 隔离 pipx 安装、CLI `0.12.0`、Codex Skill 资源哈希、doctor、配置解释、10 个资料处理器注册与 Studio 入口通过。
- [x] 文学质量样例保持 `literary_evidence_ready=false`，除非另有真实盲审证据。

## 提交与远程发布

- [x] 发布提交只包含审阅过的项目改动，不包含原著原件、小说正文、API key、缓存、SQLite、模型或构建目录。
- [ ] 主分支 CI 完整通过。
- [ ] annotated `v0.12.0` tag 指向唯一已验证提交并已推送。
- [ ] GitHub Release 中 wheel、sdist 与 `SHA256SUMS` 均存在且哈希核验通过。
- [ ] 公开稳定版本只在不可变 Release 成功后从 `v0.11.0` 切换到 `v0.12.0`。

## 本机同步

- [ ] 从不可变 `v0.12.0` tag 安装全局 CLI，而不是从未提交工作树安装。
- [ ] 按用户要求同步目标宿主 Skill，并核对版本、资源哈希与 doctor。
- [ ] 未获授权的其他宿主 Skill 不更新。

完成所有项目之前，不得宣称 v0.12.0 已发布、能够证明“非 AI”、规避平台检测、保证签约，或保证同人角色百分之百符合原著。
