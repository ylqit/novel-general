# longform-novel-engine 配置说明

v0.11.0 继续使用项目 schema v2。配置合并顺序为：

```text
config/default.engine.yaml
-> template/project.yaml
-> CLI 显式覆盖
```

未知字段直接失败；删除字段不双读、不迁移。

## 同人资料触发与资料库路径

`creation.mode=fanfiction` 强制进入同人资料流程；`fanfiction.continuity_mode=crossover` 时每个 `fanfiction.sources[]` 都有独立资料包和覆盖门禁。作品身份、权威版本、截止点与目录单元必须由人工确定。默认覆盖模式为 `全作到截止点`；改为创作范围或逐章补全必须记录人工理由。

`original`、`inspired_original` 和 `adaptation_study` 中的作品名识别只能创建 `external_work_research_request_v1`。人工批准前不得联网；`use_original_elements` 不进入普通研究，必须改为同人模式。模型记忆不能填补 Canon 缺口。

用户级共享资料库默认使用操作系统用户数据目录。只有在需要迁移或隔离资料库时设置绝对路径：

```text
LONGFORM_SOURCE_LIBRARY=D:/author-data/原著资料库
```

相对路径直接失败。作者可见配置与目录使用中文；内部 schema 和稳定 ID 保持英文。

## 字数与滚动规划

`length.metric=content_characters_v1` 是唯一规模度量。总字数、章节软硬区间和卷目标都是 forecast，不是机械章节数。默认滚动窗口最多 20 章，但 v0.10 固定语义层级为：

- firm：下一至第三章，必须有 v5 合同和全节点人工审批；
- directional：第四至第十章；
- horizon：第十一至第二十章。

`production next` 在 firm 覆盖少于三章、活动卷换卷或 planning basis 漂移时强制重新规划。配置不能关闭独立语义审查或逐节点审批。

## 写作与人工参与

`writing.mode=agent_skill` 是正式模式。每章必须依次具备：

```text
chapter_contract_v5
human_chapter_intent_v2
chapter_story_brief_basis_v3
chapter_story_brief_v5
chapter_writing_task_v7
```

`chapter_coedit_session_v2`、`human_author_revision_v4`、`human_story_review_v7` 均无跳过开关。semantic apply 后的事件实现和 reader-promise 精确 span 也无跳过开关。

## 可组合故事画像

市场、setting、plot engine、叙事形式、前提装置、关系模式和 tone 保持正交。`story_profile.market.primary=qidian_male` 是主要编辑画像；`fanqie_free` 只作为 P2 非阻断兼容观察。

画像不进入作者工作单形成固定数值配额，也不推断平台推荐、留存或内部 AI 判断算法。

## 上下文与会话

`compact|standard|large` 是保守的 engine-controlled 容量档位。作者正文始终一次输出完整；核心证据装不下时返回 `prompt_budget_exceeded`，不静默截断。

开书/卷级规划可延续协调会话；每章作者使用新会话；repair 可继续本章；自然度、独立审稿和 final 后语义档案使用隔离会话。CLI 不创建后台 Agent。

## 设定与反馈

设定事实只能使用 `canonical_fact_v2` 稳定 ID。`dependency_impact_v1` 只依据显式 dependency refs，不使用关键词推断。确定性 `must_stale` 不允许配置降级。

读者反馈批次永远非 canonical。接受反馈只能生成 planning proposal 或 canon-change seed，不能配置为直接修改正文、承诺账本或平台策略。

## 存储和安全

Agent 只能读取 manifest 的 `io.inputs` 并写唯一 `io.output.path`。Bible、outline、state、final、RAG、vector 和 SQLite 只能由 CLI 在 validate 后事务化写入。

本地审稿台固定 `127.0.0.1`，不提供远程监听配置。默认生产不需要 provider API key。

## 平台和质量

平台预检固定 `blocking=false`。项目不配置 AI 概率、检测规避、平台必过或人工写作比例。`literary_evidence_ready` 只能由合格真实盲评 manifest 改变；当前保持 `false`。

当前公开稳定配置是 v0.11.0。发布记录见 [`V0_11_0_RELEASE_CHECKLIST.md`](V0_11_0_RELEASE_CHECKLIST.md)。
