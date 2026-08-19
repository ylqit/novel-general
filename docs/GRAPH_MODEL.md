# 知识图谱模型

本地 story graph 的当前物化视图是：

```text
30_state/story_graph.json
```

章节 final 与经证据校验的 semantic ledger 才是生产事实源。默认生产通过显式 `chapter semantic-apply` 在 transaction v3 中统一物化 story graph、角色状态、伏笔、TCS、RAG 和 SQLite。SQLite 里的 `entities`、`entity_mentions`、`events` 只是 mirror。

## 命令

```powershell
python -m longform_engine.cli graph validate project.yaml
python -m longform_engine.cli graph update project.yaml --chapter 12
python -m longform_engine.cli graph check project.yaml
```

## Schema

顶层结构：

```json
{
  "entities": [],
  "relationships": [],
  "events": []
}
```

v1 支持的实体类型：

- `character`
- `location`
- `organization`
- `ability`
- `item`
- `secret`
- `foreshadowing`
- `event`

`faction` 会被归一化为 `organization`，用于兼容 `10_bible/factions.json`。

## Validate

`graph validate` 检查：

- `entities`、`relationships`、`events` 是否为 list。
- entity 是否有 `id`、`name`、合法 `type`。
- entity id 是否重复。
- relationship 是否有 `source`、`target`、`type`。
- event 是否有 `id`、`title`。
- relationship 和 event participant 是否引用了已注册实体。

有 error 时命令返回非 0；warning 不阻断。

## Manual Update

`graph update --chapter N` 是保留的人工维护和诊断入口，不属于默认逐章生产链。它进行保守更新，不凭空发明设定：

1. 读取 `40_manuscript/final/chNNN.md`。
2. 从 `10_bible/characters.json`、`locations.json`、`factions.json` 和已有 `story_graph.json` 获取 canon 实体。
3. 用实体名称和 aliases 匹配正文。
4. 为命中的实体写入 chapter mention。
5. 基于章节标题和摘要写入一个章节事件。
6. 保存 `story_graph.json`。
7. 自动执行 `db sync`，镜像到 SQLite。

正常章节无需另跑此命令；`chapter semantic-apply` 已从一次 `canonical_delta_v1` 统一生成同一批派生视图。`graph extract/apply` 同样只用于显式维护、回填或诊断。

## Check

`graph check` 生成：

```text
50_workbench/graph_reports/graph_check.md
```

当前检查项：

- validate errors。
- 重复实体名。
- relationship 状态冲突。
- ability 超出 `max_level` 或缺少 cost/limitation。
- event 时间线倒退或缺少 chapter number。
- 同一角色同章多地点冲突。

门禁可读取这份报告，把严重冲突转为门禁失败。
