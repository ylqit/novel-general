# longform-novel-engine 配置说明

当前项目使用 schema v2。配置合并顺序为：

```text
config/default.engine.yaml
-> template/project.yaml
-> CLI 显式覆盖
```

未知字段直接失败；删除字段不双读、不迁移。

## Workspace Studio

Workspace Studio 的工作区不是项目配置字段，也没有隐式默认值。每次启动必须给出明确绝对目录：

```powershell
longform-engine studio serve --workspace "D:\NovelProjects" --create-workspace
```

工作区不能是磁盘根、操作系统用户目录或仓库根，浏览器只能发现、创建和导入该目录内的项目。同一工作区只允许一个 Studio 实例；后续桌面、Codex 或 CLI 启动会向现有实例申请新的单次 bootstrap URL，不会创建竞争服务。项目深链接使用 `studio serve project.yaml`，章节深链接另加 `--chapter N`。

Windows 桌面入口通过 `studio shortcut-install --workspace WORKSPACE` 显式安装。快捷方式使用当前 Python 环境的 `pythonw.exe`，只保存固定 Studio 启动参数；不保存 Codex 登录凭据、provider API key 或 Prompt。

Codex Agent 按钮只在本机 `codex --version` 与 `codex login status` 可用时启用。Agent Job 只能消费当前 `AgentTaskManifest v5`，每个项目同一时刻最多一个状态变化任务，输入和唯一输出都受 manifest 限定。这个边界没有浏览器可配置的命令、Prompt、sandbox 或任意输出路径。

## 同人资料触发与资料库路径

`creation.mode=fanfiction` 强制进入同人资料流程；`fanfiction.continuity_mode=crossover` 时每个 `fanfiction.sources[]` 都有独立资料包和覆盖状态。作品身份、权威版本和截止点必须由人工确定。默认覆盖模式为 `分层按需`：`design_core` 阻断正式路线，当前 `chapter_dependency` 只阻断依赖它的章节；`whole_to_cutoff` 必须由人工显式选择才成为门禁。

配置不为 Fix-it、穿越、能力体系、人物忠实度或跨界规则增加专用字段。批准的项目原著基线之后，`production next` 依次要求 `同人故事发动机`、路线候选、隔离独立复核和人工 apply；`route_family`、主角与原著关系、读者识别承诺、原创主线承诺、四种时间/知识范围、原著事件命运、职责承担者、一阶/二阶影响与动态跨界宪法都保存在开放语义文档中。多来源路线使用 `fixed_host | fusion_world | sequential_worlds` 拓扑，并按实际 `payload_kinds` 生成来源适配器要求；不能用配置关闭宿主世界适配、当前卷例外或不可逆后果门禁。

`original`、`inspired_original` 和 `adaptation_study` 中的作品名识别只能创建 `external_work_research_request_v1`。人工批准前不得联网；`use_original_elements` 不进入普通研究，必须改为同人模式。模型记忆不能填补 Canon 缺口。

用户级共享资料库默认使用操作系统用户数据目录。只有在需要迁移或隔离资料库时设置绝对路径：

```text
LONGFORM_SOURCE_LIBRARY=D:/author-data/原著资料库
```

相对路径直接失败。作者可见配置与目录使用中文；内部 schema 和稳定 ID 保持英文。

## 字数与滚动规划

`length.metric=content_characters_v1` 是唯一规模度量。总字数、章节软硬区间和卷目标都是 forecast，不是机械章节数。默认滚动窗口最多 20 章，语义层级为：

- firm：下一至第三章，必须有 v5 合同；其中改变长期故事状态的重大 Plot Node 逐项人工审批，微观动作、对话和过渡不建立审批配额；
- directional：第四至第十章；
- horizon：第十一至第二十章。

`production next` 在 firm 覆盖少于三章、活动卷换卷或 planning basis 漂移时强制重新规划。配置不能关闭独立语义审查或重大状态变化节点的逐项审批。

## 写作与人工参与

`writing.mode=agent_skill` 是正式模式。每章必须依次具备：

```text
chapter_contract_v5
human_chapter_intent_v3
chapter_story_brief_basis_v4
chapter_story_brief_v5
chapter_writing_task_v8
```

`chapter_coedit_session_v2`、`human_author_revision_v4`、`human_story_review_v7` 均无跳过开关。semantic apply 后的事件实现和 reader-promise 精确 span 也无跳过开关。

## 可组合故事画像

目标平台、setting、plot engine、叙事形式、前提装置、关系模式和 tone 保持正交。`story_profile.market.primary` 记录项目选择的主要编辑画像；平台标识不代表效果判断，也不会自动产生跨平台比较。

画像不进入作者工作单形成固定数值配额，也不推断平台推荐、留存或内部 AI 判断算法。

## 上下文与会话

`compact|standard|large` 是保守的 engine-controlled 容量档位。作者正文始终一次输出完整；核心证据装不下时返回 `prompt_budget_exceeded`，不静默截断。

开书/卷级规划可延续协调会话；每章作者使用新会话；repair 可继续本章；自然度、独立审稿和 final 后语义档案使用隔离会话。普通 CLI 不创建后台 Agent；Workspace Studio 只在用户点击当前 manifest 后调用一个受控 Codex CLI 任务，并按同一 session 策略选择新会话或 resume。

## 设定与反馈

设定事实只能使用 `canonical_fact_v2` 稳定 ID。`dependency_impact_v1` 只依据显式 dependency refs，不使用关键词推断。确定性 `must_stale` 不允许配置降级。

读者反馈批次永远非 canonical。接受反馈只能生成 planning proposal 或 canon-change seed，不能配置为直接修改正文、承诺账本或平台策略。

## 存储和安全

Agent 只能读取 manifest 的 `io.inputs` 并写唯一 `io.output.path`。Bible、outline、state、final、RAG、vector 和 SQLite 只能由 CLI 在 validate 后事务化写入。

本地 Workspace Studio 与兼容 Review Desk 固定 `127.0.0.1`，不提供远程监听配置。默认生产不需要 provider API key。

## 平台、权利和资料处理

项目不配置 AI 概率、检测规避、平台必过或人工写作比例。平台政策注册表分别记录分类、投稿、签约、特定激励、内容治理、权利风险和未知项。`creation.mode=fanfiction` 时，只有 `publication export --target ...` 要求当前 `fanfiction_publication_rights_decision_v1=proceed`；该决定绑定有效配置、来源 Canon、逐来源 `rights_status/commercial_intent/platform_policy_url` 声明和目标政策快照。缺失、`hold`、绑定变化或政策复核过期只阻断对应目标导出，不阻断创作、审阅、Canon 或定稿。原创项目不触发该门禁。

`source_processing.default_execution` 默认为 `local`。`source_processing.cloud.enabled` 默认为 `false`；启用 OpenAI 时必须显式填写版本化的视觉或转写模型，并保持 `require_per_job_human_approval=true`、`allow_automatic_fallback=false`、`retain_remote_files=false`。密钥只能由环境变量或操作系统凭据边界提供。
