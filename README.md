# longform-novel-engine

`longform-novel-engine` 是面向中文长篇小说的本地生产控制面。它把原创小说、单作品同人和跨作品同人的资料、设计、规划、写章、审稿、人工修订、定稿、语义落库、发布预检与故障恢复组织成可审计流程。

系统不会替作者自动批准故事决定，也不会把模型输出直接写入 Canon。AI 只产生 workbench 候选；正式设定、大纲、终稿、语义账本和章节关闭状态都由确定性命令在校验后写入，并保留明确的人类确认。

## 产品边界

- 原创、同人和跨作品同人是彼此独立的项目，不共享正文或 Canon。
- 同人项目先固定原著版本、截止点、资料证据和权利声明，再进入故事发动机与路线设计。
- Codex 只读取当前 `AgentTaskManifest v5` 声明的输入，并只写声明的唯一候选输出。
- 浏览器不能提交任意命令、任意 Prompt、任意输出路径或任意工作区外路径。
- 打开页面、启动 Codex、校验候选都不等于批准 Canon、finalize、semantic apply 或 chapter close。
- 项目不会保存 OpenAI API key，也不会把完整 Prompt 日志写入小说工程。
- 平台预检只检查当前目标、项目配置、权利决定和政策快照，不预测审核、签约、推荐、收益或内容接受结果。

## 安装

需要 Python 3.11 或更高版本。当前公开稳定版为 `v0.14.0`。稳定安装固定到不可变源码 Tag；工作区中的未发布修改不会随该 Tag 安装：

```powershell
py -3 -m pipx install --force 'longform-novel-engine[semantic] @ git+https://github.com/ylqit/novel-general.git@v0.14.0'
longform-engine skills install --tool codex --force
longform-engine doctor --tool codex
```

本版本为 Tag-only 发布，不提供 GitHub Release wheel、sdist 或 `SHA256SUMS`。源码工作区安装：

```powershell
cd longform-novel-engine
python -m pip install -e ".[semantic]"
longform-engine skills install --tool codex
longform-engine doctor --tool codex
```

如果只使用确定性流程，可省略 `semantic` extra。模型、缓存、SQLite、正文和项目运行时文件都保留在本机，不进入发行包。

## Web 日常入口

作者日常使用工作区级 Studio：

```powershell
longform-engine studio serve --workspace "D:\NovelProjects" --create-workspace
```

Windows 可安装桌面快捷方式：

```powershell
longform-engine studio shortcut-install --workspace "D:\NovelProjects"
```

再次启动会复用同一工作区的本地 Studio。服务只监听 `127.0.0.1`，通过单次 bootstrap URL 建立本地会话，并使用 Host、Origin、Cookie、CSRF 与 CSP 约束浏览器访问。

已有项目和章节可直接深链接：

```powershell
longform-engine studio serve "D:\NovelProjects\my-book\project.yaml"
longform-engine studio serve "D:\NovelProjects\my-book\project.yaml" --chapter 12
```

兼容入口仍可单独打开章节审稿台：

```powershell
longform-engine review serve "D:\NovelProjects\my-book\project.yaml" --chapter 12
```

Studio 可以创建原创、同人和跨作品同人项目，显示当前唯一安全动作，并在用户明确点击后调用本机已登录的 Codex CLI。浏览器关闭不会取消正在运行的 Agent Job；重新打开同一工作区后可以恢复任务状态。Codex 不可用时仍可查看项目、编辑人工材料和执行不依赖 Agent 的结构化操作。

当前源码工作台提供作品书架、实际卷章目录、历史正文与归档版本阅读、资料和正文搜索，以及作品、卷、章节和选段讨论。阅读主题与工作台主题独立，字号、面板和阅读位置可以恢复。修改草稿保存在工作区材料中，来源变化和多标签页冲突会明确提示；采用候选与确认终稿继续使用原有批准流程。操作与验收范围见 [Web 工作台说明](docs/WEB_STUDIO.md) 和 [功能验收清单](docs/WEB_STUDIO_ACCEPTANCE.md)。

## 创建项目

Web 首页提供三种创建入口：

- 原创小说：填写标题、作者、规模、目标读者、内容语言和目标平台后创建项目。
- 同人小说：在通用字段之外填写原作、连续性模式、Canon 截止点、权利状态与商业意图。
- 跨作品同人：为每个来源分别填写作品身份、采用版本、截止点和权利声明。

CLI 管理入口仍可创建项目：

```powershell
longform-engine init-project "D:\NovelProjects\original-book" --title "原创小说" --author "作者"
longform-engine open-book "D:\NovelProjects\original-book\project.yaml"
```

项目创建后，以以下命令查看当前动作：

```powershell
longform-engine production next project.yaml
```

## 同人资料与路线

同人项目的资料链为：

```text
作品身份与采用版本
→ Canon 截止点与动态覆盖需求
→ 本地资料导入、规范化与证据分段
→ 人工批准的项目原著基线 Canon
→ 同人故事发动机候选与人工批准
→ 同人路线候选、隔离复核与人工批准
→ 活动卷投影、原著事件命运、人物知识边界和跨界规则
→ 当前章节 fanfiction_context_bundle_v3
```

默认资料门禁只要求 `design_core` 和当前章节真实依赖的 `chapter_dependency`。截至截止点的全作覆盖只有在作者明确选择 `whole_to_cutoff` 后才成为门禁。每部原著独立保存身份、版本、截止点、证据和绑定；完整原件保留在用户资料库，项目只保存固定绑定、批准语义主张与必要短证据。

同人路线分为 `oc_si_progression`、`canon_character_centered` 和 `hybrid`。跨作品路线使用 `fixed_host`、`fusion_world` 或 `sequential_worlds` 拓扑，并按实际传递的能力、物品、组织、身份、知识和关系编译相互作用规则，不建立作品之间的全排列矩阵。

## 小说生产流程

### 1. 开书与设计

原创项目从 Book Ideation、人工方案选择和 Book Design 开始。同人项目先完成原著资料、项目 Canon、故事发动机和路线复核。任何设计 Agent 都只写候选文件，正式设计由人工批准后进入 Canon。

### 2. 总纲、分卷与滚动规划

规划产物包含 Book Spine、分卷骨架、唯一活动卷、最多 20 章滚动窗口、章节 forecast、语义义务、Plot Node 表、Reader Promise v2 和 firm 章节合同。

规划候选先做结构校验，再由隔离会话进行语义复核。作者批准整体规划后，还要逐项决定 firm 层会改变长期目标、关系、事件命运、能力规则、身份、生死、组织或跨界规则的重大 Plot Node。普通对话、动作、过渡和局部节拍不创建审批节点。

### 3. 人工章节意图与写作

每章写作前必须具备：

```text
chapter_contract_v5
→ human_chapter_intent_v3
→ chapter_story_brief_basis_v4
→ chapter_story_brief_v5
→ chapter_writing_task_v8
```

作者工作单只显示可读故事信息，不包含控制面 ID、hash、原始 RAG、Graph、SQLite、平台诊断或 Prompt 日志。每章写作使用新的作者会话；repair 可以继续本章会话；独立审稿和语义提取使用隔离会话。

### 4. 审稿与人工终稿

AI 草稿必须经过门禁、独立审稿和必要的 repair。Review Desk 支持：

- 查看草稿、Story Brief、章节合同和 Reader Promise 账本；
- 选择正文精确 span 并添加结构化批注；
- 创建 `chapter_coedit_session_v2` 共编咨询与完整 workbench 候选；
- 完成人工全文修订，查看 AI 源稿与人工稿 diff；
- 保存 `human_author_revision_v4` 并进行隔离语义复核；
- 准备、校验并由人明确 apply `human_story_review_v7`。

人工终稿锁定后，任何后续 AI 正文变换都会使终稿证据失效。P0/P1 finding 不能靠后续未报告自动消失，必须有可追溯处置。

### 5. 定稿、语义应用与关闭

通过人工深审后，章节按顺序执行：

```text
chapter finalize
→ canonical_delta_v1 semantic validate/apply
→ 人工确认获批事件 realized/deferred/cancelled
→ 人工确认 Reader Promise 精确证据或 defer 原因
→ chapter close
```

事件实现和非 defer 承诺必须引用当前 final 的精确 Unicode span，并绑定当前 semantic ledger hash。`chapter_closure_v2` 固化 final、semantic ledger、event ledger 和 reader-promise ledger；任何证据漂移都会阻断关闭。

## 发布与权利边界

目标平台是项目配置与导出参数，不是质量结论。同人导出要求当前 `fanfiction_publication_rights_decision_v1=proceed`，并绑定有效配置、来源 Canon、逐来源权利声明和目标政策快照。缺失决定、`hold`、hash 漂移或政策复核过期只阻断对应目标导出，不阻断创作、审稿、Canon 或定稿。原创项目不触发同人权利决定门禁。

系统不提供法律意见，不替作者取得授权，也不从普通网页拼接受版权保护的连续小说、字幕或剧本。

## 恢复与审计

发生中断时先运行：

```powershell
longform-engine recovery status project.yaml --json
longform-engine production next project.yaml
```

不要手工删除项目锁、事务目录或 SQLite。恢复动作必须绑定当前恢复报告的精确 hash，并由人明确选择回滚、清理已提交事务或回收已确认失效的锁。

历史 workbench 产物可以先 dry-run，再归档到带清单和 hash 的审计 ZIP。final、语义账本、规划账本、关闭记录和当前状态视图不会被归档删除。

## 当前事实文档

- [Architecture](docs/ARCHITECTURE.md)
- [Storage Model](docs/STORAGE_MODEL.md)
- [Configuration](docs/CONFIGURATION.md)
- [Operator Guide](docs/OPERATOR_GUIDE.md)
- [Web Studio](docs/WEB_STUDIO.md)
- [Pipeline Model](docs/PIPELINE_MODEL.md)
- [RAG Model](docs/RAG_MODEL.md)
- [Graph Model](docs/GRAPH_MODEL.md)
- [SQLite Model](docs/SQLITE_MODEL.md)
- [Gate Model](docs/GATE_MODEL.md)
- [Skill Installation](docs/SKILL_INSTALLATION.md)
- [Release History](docs/RELEASE_HISTORY.md)

## License

See [LICENSE](LICENSE).

本地质量评测的创建、匿名评分、证据绑定和分阶段试写流程见[质量评测与试写指南](docs/LITERARY_TRIALS.md)。生产协议可用、人工终稿验收、试写证据与平台预检分别显示，未完成真实试写时不宣称文学质量已合格。
