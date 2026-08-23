# longform-novel-engine v0.11.0 Release Checklist

本清单记录 v0.11.0 动态同人原著资料库的实现、正式发布门禁、GitHub Release 与本机更新。历史 release 文档保持原样。

## 协议收口

- [x] 用户级 `原著资料库`、项目固定绑定和项目独立 `fanfiction_source_canon_v2` 完成三级隔离。
- [x] 同人 open-book 自动创建动态中文资料包；每部作品的指定版本截至截止点覆盖在 Canon/设计前硬阻断。
- [x] crossover 对每部作品独立计算单元、维度、原件、hash 和版本冲突门禁。
- [x] original / inspired_original / adaptation_study 的作品名提及只产生待审申请，人工批准前不联网。
- [x] 写作中未知 Canon 引用生成无网络增量需求；人工批准、资料绑定和核销前阻断。
- [x] 全局修正不静默改变项目；升级提案只使用稳定事实 ID 和显式依赖，历史影响路由 `revision_branch_v2`。
- [x] 未绑定全局证据、越界 span、重复绑定、v1 Canon 和连续原文重构均被拒绝。
- [x] 原件不进入项目、Git、出版包、审计包或 Skill；未验证权利不能保留全文。
- [x] `literary_evidence_ready=false`，不声明检测规避、平台必过或人工写作比例。

## 版本与活动文档

- [x] 包、运行时、release channel、工作流默认 tag、README、安装文档和活动架构/存储/配置统一为 0.11.0。
- [x] 新增 `docs/V0_11_0_IMPLEMENTATION.md`、`docs/releases/v0.11.0.md` 和本清单。
- [x] 两套仓库 Skill 由 `shared/` 重新生成，`resource-manifest.json` 绑定 0.11.0。
- [x] 历史 v0.10 release note、checklist 与实施文档保持原样。

## 本地发布验证

不执行额外重复回归、安装 smoke、doctor 或语义 smoke。正式发布只执行仓库要求的一次单进程发布验证，并记录最终结果：

- [x] Ruff、11 个关键 Mypy 模块、完整单进程 pytest；最终结果为 `435 passed in 1190.16s`。
- [x] Skill 引用/包、资源 manifest、Markdown 链接、Agent readiness 与 release surface guards。
- [x] wheel/sdist 构建、checksum 和分发审计；wheel 186 项、sdist 293 项。
- [ ] `longform-engine release check --repository . --channel public --json`。

## 提交与远程发布

- [ ] 提交全部当前工作树改动，提交信息 `release: publish v0.11.0`。
- [ ] 推送 `master` 并确认主分支 GitHub Actions 全部成功。
- [ ] 创建并推送 annotated immutable tag `v0.11.0`；失败时不移动或覆盖。
- [ ] 确认 GitHub Release 存在 wheel、sdist 和 `SHA256SUMS` 三项制品。

## 本机同步

Release 成功后：

- [ ] 使用现有 pipx 的 Python 3.12 环境从不可变 `v0.11.0` tag 强制升级全局 CLI。
- [ ] 执行 `longform-engine skills update --tool codex` 同步本机 Codex Skill。
- [ ] 不执行额外 doctor、语义 smoke 或临时安装验证；不更新本机 Claude Skill。
