# longform-novel-engine v0.4.4 Release Checklist

本清单定义 v0.4.4 的不可变源码门禁。按用户授权，本机仅在全部实现完成后执行一次单进程定向验证；完整跨平台回归、构建和分发审计由 Pull Request、master 与 tag GitHub Actions 执行。Actions run、不可变 tag 与 GitHub Release assets 是远程发布证据，不追加发布后证据提交。

## 配置与覆盖来源

- [x] 源码、包元数据、release channel、工作流默认 tag 和安装文档统一为 v0.4.4。
- [x] `resource-manifest.json` 在内容收敛后由生成脚本更新。
- [x] `literary_evidence_ready=false`，工程门禁不替代文学盲评。

## 统一章节路径

- [x] 正式章节继续只接受 `chNNN.md`，四位及以上章节号自然扩展。
- [x] 每个尚未应用方向的章节都必须完成人工 `chapter_direction`，基础原因是 `mandatory_chapter_direction`。
- [x] 已应用方向允许恢复执行，旧 `guided_mode` 候选不迁移。

## 内部质量证据

- [x] research promote 的 canon、RAG、graph、cache 与 SQLite 写入共用 transaction v3。
- [x] revision rollback 的 detach、stale、quality、memory/vector、Agent lifecycle 与 SQLite 写入共用 transaction v3。
- [x] `agent_data_pipeline_readiness_v4` 使用 `production_contract_ready`，不声明不存在的端到端 production smoke。

## 单进程定向验证

- 本版本按明确授权只执行一次定向 pytest，覆盖 research、revision、chapter direction、readiness、Skill 一致性与 release surface。
- 完整 Ruff、mypy、pytest、Skill/manifest/link 守卫、wheel/sdist 构建和审计由远程 CI 单进程作业执行。

## 发布前本地证据

- 发布提交固定为 `release: publish v0.4.4`，最终 master 只增加该一个发布提交。
- 分支固定为 `codex/v044-transaction-convergence`；进入 master 时不增加 merge commit。
- [x] 2026-08-19 单进程定向 pytest 首轮 51 项完成：49 passed；两个测试断言与既有初始化/设计编译语义不一致。
- [x] 修正断言后仅复查两个失败节点：2 passed in 2.79s；未重复运行完整本机测试矩阵。

## 远程发布证据

- Pull Request CI 与 master CI 必须全部通过，否则不得创建 tag。
- annotated tag 固定为 `v0.4.4` 且不得移动或覆盖。
- tag Release workflow 必须重新验证并发布 wheel、sdist 和 `SHA256SUMS` 到 GitHub Release。
- 发布完成后从不可变 v0.4.4 升级本机 pipx CLI 与 Codex Skill，并执行 version/status/doctor smoke。

## 发布结论

- 目标：公开 GitHub Release v0.4.4；不发布到额外软件包索引。
- GitHub Release、wheel、sdist 或校验和任一缺失时，发布状态保持 blocked。
