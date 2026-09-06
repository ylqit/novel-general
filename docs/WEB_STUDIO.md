# 小说创作工作台

Workspace Studio 是 `longform-novel-engine` 的本机 Web 入口。它把原创小说、同人小说、Codex 候选任务、章节审稿和明确人工批准放在同一个 `127.0.0.1` 服务中；不改变文件事实源、Agent manifest、transaction v3 和章节关闭门禁。

## 1. 推荐启动方式

Windows 日常入口：

```powershell
longform-engine studio shortcut-install --workspace "D:\NovelProjects"
```

该命令创建桌面“小说创作工作台”。快捷方式使用安装该命令的 Python 环境中的 `pythonw.exe`，双击后执行固定的工作区级 `studio serve`。快捷方式不保存 ChatGPT/Codex 凭据、provider API key 或 Prompt。

直接管理入口：

```powershell
longform-engine studio serve --workspace "D:\NovelProjects" --create-workspace
longform-engine studio serve "D:\NovelProjects\original-novel\project.yaml"
longform-engine studio serve "D:\NovelProjects\original-novel\project.yaml" --chapter 12
```

Codex App 快捷语句：

```text
/打开小说创作工作台
/打开创作控制台
/打开章节工作台
```

Skill 不是后台服务。它是 Codex 执行小说工单时遵守的读写、会话、校验和人工审批协议；打开 Web 不需要先手动“启动 Skill”。

## 2. 工作区与单实例

`--workspace` 必须是明确绝对目录。为避免意外扩大浏览器访问范围，磁盘根、操作系统用户目录和仓库根不能作为工作区。Studio 只能发现该目录的直接项目，或用户显式导入且仍位于工作区内的嵌套项目；Agent staging 不参与项目发现。

每个工作区只有一个服务实例。启动器先读取工作区内的实例元数据并向现有 loopback 服务申请新的单次 bootstrap URL；如果实例不存在，才获取独占生命周期锁、选择端口并启动。进程退出时只清理自身 PID 和 launcher secret 所拥有的两项生命周期文件，不删除项目内容。

## 3. 创建原创与同人项目

工作区首页提供：

- 创建原创小说；
- 创建单作品同人；
- 创建跨作品同人；
- 导入工作区内已有 `project.yaml`。

创建页面收集书名、slug、目标平台、目标读者、写作视角与风格、核心阅读承诺、全书问题、结局方向、禁止体验、总字数、单章/单卷目标和滚动规划参数。确认后只创建项目目录、配置并执行 `open-book`；不会自动运行 Codex，也不会自动批准后续设计。

同人项目还必须逐来源填写原作身份、创作者、Canon 截止点、连续性模式、允许元素、权利状态、资料留存策略、商业意图和平台政策链接。默认选择是 `unverified + metadata_only`。创建同人项目前必须明确确认权利与资料留存风险；这项声明不是法律核验、原著授权或平台接受保证。

原创与同人项目始终拥有独立目录、Canon、Agent task、章节状态和审计记录。跨作品同人的每个原作来源也保持独立的版本、截止点和后续覆盖状态。

## 4. 页面路由

| 路由 | 作用 |
| --- | --- |
| `/` | 工作区首页、项目卡片、Codex 状态和导入 |
| `/projects/new` | 创建原创、同人或跨作品同人 |
| `/projects/{id}` | 项目驾驶舱与当前唯一安全动作 |
| `/projects/{id}/design` | 开书、创意、Book Design 和编译候选 |
| `/projects/{id}/sources` | 原著资料状态与缺口入口 |
| `/projects/{id}/fanfiction` | 同人 Canon、故事发动机和路线状态 |
| `/projects/{id}/planning` | 总纲、卷纲、firm 三章和滚动规划状态 |
| `/projects/{id}/chapters/{n}` | 统一章节创作、审稿、语义与关闭中心 |
| `/projects/{id}/knowledge` | 人物、事件、图谱、伏笔和承诺状态 |
| `/projects/{id}/publication` | 发布材料与目标平台预检入口；不会直接向外部平台发布 |
| `/projects/{id}/literary` | 质量评测：创建匿名包、独立评分、报告与分歧处理 |
| `/projects/{id}/literary/review/{trial}/{reviewer}` | 独立评审入口，只加载匿名材料和本人记录 |
| `/projects/{id}/recovery` | 项目锁、事务、Agent Job 和审计恢复入口 |

当前安全动作来自 `production next`，页面不自行猜测命令。确定性推进只能使用 `production loop --no-apply` 语义；遇到 Agent 输出、人工决定或 canonical 边界立即停止。

## 5. Codex Agent Job

页面只允许启动当前项目的当前 `AgentTaskManifest v5`。点击“交给 Codex”后：

1. 再次验证 task ID、manifest schema、生命周期和唯一输出；
2. 确认该项目没有另一个活动状态变化任务；
3. 把 manifest 和声明输入复制到隔离 staging，拒绝符号链接；
4. 通过 stdin 启动 `codex exec --json --sandbox workspace-write`；
5. 按 manifest session 策略选择新会话或恢复允许的会话；
6. 任务结束后检查 staging inventory、输入 hash 与额外写入；
7. 只复制声明的 UTF-8 输出，并执行控制面协议校验；
8. 把候选交还页面，等待人类查看、选择或要求重写。

浏览器 API 不接受任意 Prompt、命令、工作目录、sandbox 参数或输出路径。任务状态只保存 job ID、task ID、时间、session ID、状态、事件计数和压缩错误，不保存完整 Prompt 日志。页面关闭后任务继续，重新打开可查询；Codex 失败或取消不改变 canonical。

Codex App 不必一直打开，但本机必须安装 Codex CLI、登录当前账号，并保证 `codex exec` 可运行。不可用时 Web 仍可打开，项目查看和人工操作继续可用，Agent 按钮显示阻断原因。Web 不要求也不保存 OpenAI API Key。

## 6. 人工批准与章节闭环

以下动作彼此独立，且都绑定当前候选或正式正文 SHA-256、`approved_by=human` 与动作专属确认字段：

- 选择并批准设计 Markdown 候选；
- 应用经过校验的 design semantic compile delta；
- 应用经过校验的人工深审决定；
- `chapter finalize`；
- `chapter semantic-apply`；
- 批准作者声音编辑对；
- 确认每个获批事件的实现、延期或取消；
- 确认每个非 defer reader-promise 的正式正文精确 span；
- `chapter close`。

页面不会把一个确认复用于另一个动作。打开页面、查看 diff、勾选普通 UI、Agent 成功、确定性 gate 通过或浏览器关闭，都不构成上述批准。章节未 close 时，生产状态机仍禁止进入下一章。

当章节已有可审草稿且尚无 final 时，统一章节路由挂载 Review Desk，支持精确 span 批注、AI 源稿/人工稿 diff、人工全文修订、共编咨询、人工候选提交、故事深审准备/校验和深审决定显式 apply。独立 `review serve` 只为兼容与故障恢复保留。

## 7. 安全模型

- 服务只绑定 `127.0.0.1`，没有远程监听选项；
- 首次访问使用一次性 bootstrap token，使用后立即失效；
- 后续请求要求 HttpOnly、SameSite=Strict session cookie；
- 状态变化请求要求正确 Host、Origin 和专用 CSRF header；
- 页面使用 CSP、`frame-ancestors 'none'`、`nosniff` 和 same-origin opener policy；
- 工作区路径经过 resolve 与 containment 校验；
- 项目配置、Agent 输入和输出不能逃逸各自边界；
- 同一项目不并行运行两个状态变化 Agent Job；
- 自动 Agent 永远不能直接写 Bible、outline、state、final、RAG、Graph、vector 或 SQLite；
- 所有 canonical mutation 继续使用现有验证、项目锁、transaction 和恢复协议。

## 8. 故障与恢复

如果浏览器关闭，重新双击快捷方式或再次执行 `studio serve`；现有服务会发放新的单次 URL。如果 Codex Job 失败，页面显示紧凑错误并保留正式状态，先检查本机 Codex 登录、当前 task 是否 stale 和 output 协议，不要手工移动 staging 输出到 canonical。

如果服务已经退出但实例文件残留，下一次启动只在确认 PID 不存在后清理自身两项 Studio 生命周期文件。如果项目存在 lock、pending transaction 或其他 canonical blocker，进入项目恢复页并按 `recovery status` 返回的精确 SHA 使用恢复动作；不要手工删除项目锁、transaction report 或 snapshot。

## 9. 发布边界

发布中心用于准备导出状态、平台预检、权利决定和本地产物。它不登录起点，不代替作者同意平台条款，也不直接把正文发布到外部平台。同人目标导出仍要求当前目标的人工 `proceed` 决定，并绑定配置、来源 Canon、逐来源权利声明和政策快照；任何绑定漂移都会使旧决定失效。


## 质量评测与人工工作量

从项目侧栏“质量评测”选择工作区内项目、阶段和章节范围。开篇诊断每份 3 章，持续性诊断每份 10 章，正式同人验收要求原创／穿越主角与原著人物中心两类路线各 20 章；跨界诊断使用连续章节，顺序诸天必须跨过卷边界。创建前直接核对 final、人工关闭记录、语义 ledger、终稿修订、独立审稿屏障和批准原著绑定，不接受手填门禁通过报告。

创建后为三位未参与样本创作的人类评审分别生成入口。入口凭证绑定当前项目、评测与评审人；页面只读取匿名正文、必要的批准基线参考和本人的评分记录。可在正文框选取问题原文，填写阅读影响，保存草稿，并在完成独立阅读后提交。提交后的评分不可修改；重复提交相同记录返回幂等结果。网页提供匿名包导出、本人评分文件导出／导入，供离线评阅使用；不提供远程评审服务。本机入口使用已打开 Studio 的浏览器会话，凭证只在创建时显示。

报告重新计算所有适用指标的三人中位数。自然度、人物自主性、追读意愿，以及同人适用的基线忠实度与新增阅读价值至少 4 分；其他适用指标至少 3.5 分。重要分歧必须记录处理意见，所有分数保留。严重问题确认成立时，必须修订并创建新评测；不能通过处理意见把问题改为已修复并沿用旧正文评分。未达到三人独立提交时不形成验收结论。

本项目已关闭章节可以另行记录人工审阅和实质修改耗时，留空为未知。报告把作者工作时间与评审时间分开显示。正文、原著基线、配置、引擎代码或规则资源发生变化时，原评测显示 stale；正常归档支持从已验证审计包读取原审稿证据，不要求恢复生产候选。

质量页面分别展示生产协议、人工终稿验收、文学试写证据和平台预检。样本阈值是内部试写标准，不推断平台通过率、AI 检测结果或读者市场表现。工程夹具和模拟评分只证明协议行为，不能替代真实试写及三人独立人工评审。

CLI 与网页调用相同领域 API，详见 [文学评测操作流程](LITERARY_TRIALS.md)。
