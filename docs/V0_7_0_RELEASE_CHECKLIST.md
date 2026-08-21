# v0.7.0 人类作者修订与平台低质合规 Checklist

本文是公开稳定版 `v0.7.0` 的实现与本地发布门禁清单。用户已于 2026-08-22 单独授权发布；远程 `master` CI、不可变 tag、Release 资产与远程安装 smoke 仍必须按 `RELEASE_RUNBOOK.md` 顺序完成后，才可对外宣告成功。发布过程不覆盖全局 Skill，也不接入平台账号或读者数据。

## 1. 协议与状态机

- [x] 新增 `human_author_revision_v1`、任务与 validation；绑定源候选、修订前 bundle、人工候选、记录和双稿语义复核 hash。
- [x] 人工记录至少覆盖两个影响维度，且场景因果或人物声音/情绪至少一个；每项保存精确 before/after span、意图与保护项。
- [x] 空白、格式、标点专改不能通过；人工候选要求 UTF-8、LF 和单一结尾换行，避免 Windows 行尾造成证据 hash 分叉。
- [x] `prose_revision_semantic_reviewer` 统一承担 Humanizer 与人工修订双稿语义保真；角色总数不增加。
- [x] 双稿审稿读取冻结的人工记录快照，最终记录只允许补绑定 semantic review hash，避免循环 hash。
- [x] `draft submit --agent human --overwrite` 强制消费当前验证记录；普通人工修订不消耗 repair 预算，repair 绑定候选消费原有轮次。
- [x] human 提交产生新候选 hash，并使旧 gate、审稿、咨询、接受与平台预检 stale；随后重跑完整 gate 和独立审稿。
- [x] `human_story_review_v4` 绑定候选、章节合同、承诺账本、因果模拟、review bundle 与人工修订六类 hash。
- [x] v4 强制三组人工核心正文证据；其余维度引用独立审稿覆盖，所有 finding 均需显式处置，前端不代填通过理由。
- [x] v0.6 `human_story_review_v3` 明确拒绝，不提供自动迁移或双读。

## 2. 去低质与作者声音

- [x] 空稿、Prompt/任务残留为 P0；可证明的大段精确重复和格式损坏可阻断。
- [x] 常见词、句长、对白率、感官密度、慢章和尾钩强度降为 P2 定位信号，不能独立产生 P1。
- [x] `anti_ai_editor` 与 `scene_prose_editor` 每章独立必审。
- [x] 模板化 P1 必须提供至少两个精确 span、重复叙事功能、读者损害与保护项。
- [x] 作者工作单不包含禁词表、检测词典、finding code 或编辑复发注册表。
- [x] 新增 `author_voice_edit_pair_v1`；只批准真实人工修改与当前 final 重合区域。
- [x] 第一至第三章关闭前每章至少一个已批准 pair；最多 12 个 active，超限时人工指定替换，不自动淘汰。
- [x] 后续写作任务最多加载两个与当前 POV/场景相关的人工正例及抽象原则。

## 3. 审稿台与咨询

- [x] 审稿台显示 AI 源稿、人工完整稿、diff、修改意图、风险分层深审和咨询。
- [x] 审稿台写入限制在 `50_workbench`；人工 repair 旧直提入口被拒绝，必须进入修订与语义验证。
- [x] 咨询绑定当前人工候选与 bundle；候选变化立即 stale，不能改正文、批准或 finalize。
- [x] 保留 loopback、一次性 token、Host/Origin、CSRF、CSP、HTML/JSON 安全输出和预期 hash 并发校验。

## 4. 平台政策与发布证据

- [x] 新增 `platform_publication_policy_registry_v1`，与市场画像分离；保存 claim、未知项、发布者、适用范围、核验日和下次复核日。
- [x] 番茄预检只映射公开的粗制滥造、格式混乱、结构失常和空洞水文治理，不把开篇章数、对白率或收益频率写成违规规则。
- [x] 起点预检明确“未发现可核验的公开全面 AI 禁令，平台内部判定未知”。
- [x] 国家生成合成内容标识要求只作投稿披露提示，不自动向正文插入、删除或规避标识。
- [x] `publication preflight` 固定 `blocking=false`，返回 `clear|attention|policy_verification_required`。
- [x] `creation_provenance_manifest_v1` 汇总方向、人工修订、声音、final 与审稿 hash，不保存完整 Prompt 或人类占比。
- [x] `publication_risk_report_v2` 不允许 AI 概率、检测通过、规避检测或人类占比字段。
- [x] `quality status --json` 保留三态，新增人工修订覆盖和两个平台预检状态；不改写 `literary_evidence_ready`。

## 5. 文档与发布面

- [x] 版本元数据、README 安装通道和 release channel 已切换为公开稳定版 0.7.0。
- [x] 同步 README、Architecture、Storage、Configuration、Pipeline、Gate 与两个内置 Skill 的当前协议说明。
- [x] 不修改历史 v0.6.0 release note/checklist 的历史事实。
- [x] 生成并核对资源 manifest。
- [x] Markdown 链接与文档一致性检查通过。
- [x] wheel/sdist 资源、禁入文件与 release surface audit 通过。
- [x] 隔离安装验证通过。

## 6. 测试与验收

- [x] Ruff 静态检查通过。
- [x] 新增并通过人工修订的缺失、纯标点、hash 漂移、语义失败、后续 Agent 改稿 stale 测试。
- [x] 新增并通过作者声音首三章、来源重合、12 个 active 替换测试。
- [x] 新增并通过平台官方来源、过期政策、非阻断及禁止字段测试。
- [x] 审稿台 XSS、CSRF、路径穿越、并发锁、旧 hash 与无自动理由测试通过。
- [x] 单进程目标类型检查通过。
- [x] 单进程完整 `pytest -q -p no:xdist` 通过：`383 passed in 2037.78s (0:33:57)`。
- [x] 构建后 wheel/sdist 审计与隔离安装 smoke test 通过。

## 7. 不作出的承诺

- [x] 不实现或宣传绕过起点、番茄未公开检测系统。
- [x] 不宣称平台允许或禁止全部 AI 辅助小说。
- [x] 不把人工修订 hash 当作文学生成、法律作者身份或平台接受证明。
- [x] 不把平台预检、作者接受或工程协议就绪改写为 `literary_evidence_ready=true`。
- [x] 本清单全部验证完成后只代表本地候选可发布；用户已单独授权本次发布，远程成功仍以 Actions、不可变 tag 与 Release assets 为准。

## 8. 发布授权与不可变边界

- [x] 用户已明确授权创建发布提交、推送 `master`、等待 CI、创建并推送 `v0.7.0` annotated tag，以及核验 GitHub Release。
- [x] 发布前不得创建 tag；只有发布提交的 `master` CI 全绿后才允许创建标签。
- [x] tag 创建后不得移动或覆盖；远程证据不通过时使用补丁版本修复。
- [x] GitHub Actions、Release assets 与远程安装结果属于发布后外部事实，不预填进不可变 tag 中的本清单；最终发布报告必须给出可核验 URL、提交和资产 hash。
- [x] 全局 CLI 与 Skill 同步不属于本次发布动作，继续等待用户另行决定。
