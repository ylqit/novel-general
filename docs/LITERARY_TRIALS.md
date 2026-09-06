# 文学评测操作流程

评测只读取已完成人工终稿、finalize、semantic apply 与 chapter close 的项目。它不会自动写书或批准正文。建议先在 Workspace Studio 的“质量评测”页面创建和评分；CLI 可用于同机分开评阅或导入离线评分。

## 创建匿名样本

先准备 `samples.json`，路径可以是工作区中另外的来源项目。以下是开篇诊断的单份样本；可添加原创、原创／穿越主角同人与原著人物中心同人项目。

```json
[
  {
    "config_path": "D:/NovelProjects/my-book/project.yaml",
    "chapter_start": 1,
    "chapter_end": 3
  }
]
```

```powershell
longform-engine literary create project.yaml --trial-id opening-01 --stage opening --samples-file samples.json --json
longform-engine literary status project.yaml --json
longform-engine literary export-pack project.yaml --trial-id opening-01 --output anonymous-pack.zip
```

阶段为 `opening`（3 章）、`sustained`（10 章）、`formal`（两类同人路线各 20 章）或 `crossover`（连续章节；顺序诸天跨卷）。每份样本保留当前配置、引擎版本与代码／规则资源指纹、原著基线、章节范围、正文和审稿证据绑定。不可取得的模型信息保留为空，不猜测宿主使用的模型。

## 独立评分

为三位独立人类评审分别注册代号。代号不是模型或 Agent 实例。

```powershell
longform-engine literary reviewer-add project.yaml --trial-id opening-01 --reviewer-id reader-1 --json
longform-engine literary review project.yaml --trial-id opening-01 --reviewer-id reader-1 --json
```

`review` 返回匿名材料与该评审人的 `draft` 和 `draft_sha256`。编辑 `draft` 对象并单独保存为评分文件。`pack_hash` 与评审身份必须来自该记录；人类填写分数、独立性声明、阅读影响、问题原文位置及可选耗时，不手填正文 hash。草稿保存使用读取时的版本，避免覆盖另一个窗口的修改。

```powershell
longform-engine literary save project.yaml --trial-id opening-01 --reviewer-id reader-1 --file reader-1.json --expected-sha256 CURRENT_DRAFT_SHA256 --json
longform-engine literary submit project.yaml --trial-id opening-01 --reviewer-id reader-1 --file reader-1.json --expected-sha256 CURRENT_DRAFT_SHA256 --json
longform-engine literary import-review project.yaml --trial-id opening-01 --reviewer-id reader-1 --file offline-reader-1.json --json
```

`import-review` 明确提交离线评分；网页的导入先保存个人草稿，由评审人另行点击独立提交。提交后不可改分，重复提交完全相同记录幂等返回，其他内容被拒绝。评审实例身份必须三人不同，所有人声明没有参与生成、修改或交换本轮评分。离线包仅含匿名正文、必要基线参考和公共评测规则，不导出私有映射或他人记录。

## 报告与分歧

```powershell
longform-engine literary report project.yaml --trial-id opening-01 --json
longform-engine literary report project.yaml --trial-id opening-01 --output opening-report.json
longform-engine literary resolve project.yaml --trial-id opening-01 --file resolution.json --json
```

`resolution.json` 包含 `submission_sha256`、`decided_by` 和 `decisions`。以报告中的问题 ID 为键，每项填写 `outcome`、`reason`、`follow_up`。分歧只能 `acknowledged`；严重问题可以 `needs_revision` 或在核对原文后、有充分理由的 `not_substantiated`。不允许改分、删选评分或把旧正文中的成立问题改成“已修复”。后续正文修订需要新评测。

人工工作量可在评测页面或 CLI 另行记录，未填为未知。

```powershell
longform-engine literary effort-record project.yaml --chapter-number 1 --human-review-minutes 18 --human-edit-minutes 35 --json
```

评分按 1—5 分、0.5 分步长填写。通用指标始终适用，分歧因果与跨体系代价／反制由当前合同决定。严重结论须指向准确原文并说明阅读损害；固定句长、对白率或每章悬崖结尾不是严重问题依据。

首次工程交付仍需执行真实试写：三个项目各 3 章诊断、两类同人各 10 章持续性诊断、各 20 章与三人独立人工验收，并分别验证三种跨界拓扑。题材、原著版本、逐章人工意图和终稿批准由作者提供。尚未完成的类型始终为未验证；内部阈值不代表平台接收或市场表现。
