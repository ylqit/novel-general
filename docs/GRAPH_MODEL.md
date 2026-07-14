# 知识图谱模型

本地 story graph 的事实源是：

```text
30_state/story_graph.json
```

SQLite 里的 `entities`、`entity_mentions`、`events` 只是 mirror，可通过 `db sync` 或图谱更新后自动重建。

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

## Update

`graph update --chapter N` 是保守更新，不凭空发明设定：

1. 读取 `40_manuscript/final/chNNN.md`。
2. 从 `10_bible/characters.json`、`locations.json`、`factions.json` 和已有 `story_graph.json` 获取 canon 实体。
3. 用实体名称和 aliases 匹配正文。
4. 为命中的实体写入 chapter mention。
5. 基于章节标题和摘要写入一个章节事件。
6. 保存 `story_graph.json`。
7. 自动执行 `db sync`，镜像到 SQLite。

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
