"""Research inbox, impact analysis, and canon promotion."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import hashlib
import json
import re

from longform_engine.config import ConfigDocument
from longform_engine.db import query_table, sync_database
from longform_engine.rag import build_context
from longform_engine.storage import atomic_write_text, resolve_project_root


class ResearchError(ValueError):
    """Raised when a research command cannot complete."""


@dataclass(frozen=True)
class ResearchItemResult:
    """A research inbox item created by add/search."""

    item_id: str
    status: str
    item_file: str
    content_file: str
    title: str
    source_type: str
    sources: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ImpactAnalysisResult:
    """Impact report for a research item."""

    item_id: str
    report_file: str
    report_json: str
    impacted_characters: tuple[str, ...]
    impacted_chapters: tuple[str, ...]
    impacted_foreshadowing: tuple[str, ...]
    impacted_graph_nodes: tuple[str, ...]
    impacted_future_cards: tuple[str, ...]
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class ResearchPromoteResult:
    """Promotion result after an inbox item becomes canon."""

    item_id: str
    status: str
    canon_file: str
    impact_report: str
    rag_chunk_file: str
    context_file: str
    graph_file: str
    db_chunks: int
    canon_paths: tuple[str, ...]


@dataclass(frozen=True)
class KnowledgeGapResult:
    """Detected knowledge gaps and a research plan artifact."""

    chapter_number: int | None
    report_file: str
    plan_file: str
    gaps: tuple[str, ...]
    sources_checked: tuple[str, ...]


WebFetcher = Callable[[str, int, int], list[dict[str, Any]]]


def add_research(
    config: ConfigDocument,
    *,
    file_path: str | Path,
    title: str | None = None,
    source_url: str | None = None,
    tags: list[str] | None = None,
) -> ResearchItemResult:
    """Add a user-provided research note to the isolated inbox."""

    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise ResearchError(f"Research source file does not exist: {path}")
    content = safe_read_text(path)
    inferred_title = title or extract_title(content, fallback=path.stem)
    payload = make_inbox_payload(
        config,
        title=inferred_title,
        content=content,
        source_type="manual_file",
        source_url=source_url,
        sources=[
            {
                "type": "manual_file",
                "path": str(path),
                "url": source_url,
                "title": inferred_title,
                "credibility": "user_provided",
            }
        ],
        tags=tags or [],
        external_id=str(path),
    )
    item_json, item_md = write_inbox_item(config, payload, content)
    return ResearchItemResult(
        item_id=payload["id"],
        status=payload["status"],
        item_file=str(item_json),
        content_file=str(item_md),
        title=payload["title"],
        source_type=payload["source_type"],
        sources=tuple(payload["sources"]),
    )


def search_research(
    config: ConfigDocument,
    query: str,
    *,
    limit: int | None = None,
    fetcher: WebFetcher | None = None,
) -> ResearchItemResult:
    """Search the web and place summarized results in the research inbox."""

    cleaned_query = query.strip()
    if not cleaned_query:
        raise ResearchError("Research search query cannot be empty.")
    research_config = config.data.get("research", {})
    if research_config.get("web_search_enabled") is False:
        raise ResearchError("research.web_search_enabled is false.")

    limit = limit or int(research_config.get("search_limit") or 5)
    timeout = int(research_config.get("network_timeout_seconds") or 8)
    fetch = select_web_fetcher(config, fetcher)
    network_status = "ok"
    try:
        sources = fetch(cleaned_query, limit, timeout)
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        network_status = f"fallback:{exc.__class__.__name__}"
        sources = []

    if not sources:
        sources = [
            {
                "type": "web_search_fallback",
                "title": cleaned_query,
                "url": f"https://www.google.com/search?q={quote(cleaned_query)}",
                "summary": "Network search did not return parseable results; review the query manually before promotion.",
                "credibility": "unverified",
            }
        ]

    content = format_search_content(cleaned_query, sources, network_status)
    payload = make_inbox_payload(
        config,
        title=f"联网检索：{cleaned_query}",
        content=content,
        source_type="web_search",
        source_url=sources[0].get("url"),
        sources=sources,
        tags=["web_search", cleaned_query],
        external_id=cleaned_query,
    )
    payload["query"] = cleaned_query
    payload["network_status"] = network_status
    payload["provider"] = search_provider_name(config, fetcher)
    item_json, item_md = write_inbox_item(config, payload, content)
    return ResearchItemResult(
        item_id=payload["id"],
        status=payload["status"],
        item_file=str(item_json),
        content_file=str(item_md),
        title=payload["title"],
        source_type=payload["source_type"],
        sources=tuple(payload["sources"]),
    )


def impact_analyze(config: ConfigDocument, *, research_item: str) -> ImpactAnalysisResult:
    """Generate an impact report before canon promotion."""

    root = resolve_project_root(config)
    item_path = resolve_research_item_path(config, research_item)
    item = read_json(item_path, default={})
    if not isinstance(item, dict) or not item.get("id"):
        raise ResearchError(f"Invalid research item: {research_item}")

    text = research_text(root, item)
    keywords = tuple(extract_keywords(f"{item.get('title', '')} {text}")[:20])
    impacts = {
        "characters": match_bible_entities(root, keywords, text, "10_bible/characters.json"),
        "chapters": match_chapters(root, keywords, text),
        "foreshadowing": match_outline_collection(root, keywords, text, "20_outline/foreshadowing_ledger.json"),
        "graph_nodes": match_graph_nodes(root, keywords, text),
        "future_cards": match_future_cards(root, keywords, text),
    }

    report_dir = root / str(config.data.get("research", {}).get("impact_report_dir", "50_workbench/impact_reports"))
    report_dir.mkdir(parents=True, exist_ok=True)
    report_md = report_dir / f"{item['id']}.md"
    report_json = report_dir / f"{item['id']}.json"
    payload = {
        "research_item_id": item["id"],
        "title": item.get("title"),
        "status": item.get("status"),
        "keywords": list(keywords),
        "impacts": impacts,
        "recommended_actions": recommended_actions(impacts),
        "generated_at": utc_now(),
    }
    atomic_write_text(report_json, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(report_md, format_impact_markdown(payload))
    return ImpactAnalysisResult(
        item_id=str(item["id"]),
        report_file=str(report_md),
        report_json=str(report_json),
        impacted_characters=tuple(impacts["characters"]),
        impacted_chapters=tuple(impacts["chapters"]),
        impacted_foreshadowing=tuple(impacts["foreshadowing"]),
        impacted_graph_nodes=tuple(impacts["graph_nodes"]),
        impacted_future_cards=tuple(impacts["future_cards"]),
        keywords=keywords,
    )


def promote_research(
    config: ConfigDocument,
    *,
    research_item: str,
    approved_by: str = "cli",
    review_note: str | None = None,
) -> ResearchPromoteResult:
    """Promote an inbox item into canon files, RAG chunks, graph, and SQLite."""

    root = resolve_project_root(config)
    item_path = resolve_research_item_path(config, research_item)
    item = read_json(item_path, default={})
    if not isinstance(item, dict) or not item.get("id"):
        raise ResearchError(f"Invalid research item: {research_item}")

    impact = impact_analyze(config, research_item=str(item["id"]))
    text = research_text(root, item)
    promoted_at = utc_now()
    canon_record = {
        "id": item["id"],
        "title": item.get("title"),
        "content": text,
        "summary": item.get("summary"),
        "source_type": item.get("source_type"),
        "source_url": item.get("source_url"),
        "sources": item.get("sources", []),
        "tags": item.get("tags", []),
        "impact_report": relative_path(root, Path(impact.report_file)),
        "approved_by": approved_by,
        "review_note": review_note,
        "promoted_at": promoted_at,
    }

    research_config = config.data.get("research", {})
    canon_path = root / str(research_config.get("canon_file", "10_bible/research_canon.jsonl"))
    impact_ledger = root / str(research_config.get("impact_ledger", "20_outline/research_impact_ledger.jsonl"))
    upsert_jsonl(canon_path, canon_record)
    upsert_jsonl(
        impact_ledger,
        {
            "id": item["id"],
            "title": item.get("title"),
            "impact_report": relative_path(root, Path(impact.report_file)),
            "impacts": asdict(impact),
            "promoted_at": promoted_at,
        },
    )
    rag_chunk = write_research_rag_chunk(config, canon_record)
    graph_file = update_research_graph(config, canon_record)
    context = build_context(
        config,
        chapter_number=next_context_chapter(root),
        query_text=research_context_query(canon_record, impact.keywords),
    )

    item["status"] = "promoted"
    item["promoted_at"] = promoted_at
    item["approved_by"] = approved_by
    item["review_note"] = review_note
    item["impact_report"] = relative_path(root, Path(impact.report_file))
    item["canon_paths"] = [
        relative_path(root, canon_path),
        relative_path(root, impact_ledger),
        relative_path(root, rag_chunk),
        relative_path(root, graph_file),
    ]
    atomic_write_text(item_path, json.dumps(item, ensure_ascii=False, indent=2) + "\n")
    sync_database(config)
    chunk_count = len(query_table(config, "chapter_chunks", limit=10000))
    return ResearchPromoteResult(
        item_id=str(item["id"]),
        status="promoted",
        canon_file=str(canon_path),
        impact_report=impact.report_file,
        rag_chunk_file=str(rag_chunk),
        context_file=context.context_file,
        graph_file=str(graph_file),
        db_chunks=chunk_count,
        canon_paths=tuple(item["canon_paths"]),
    )


def detect_knowledge_gaps(
    config: ConfigDocument,
    *,
    chapter_number: int | None = None,
    text: str | None = None,
) -> KnowledgeGapResult:
    """Detect research gaps from cards, writing tasks, gate failures, and graph warnings."""

    root = resolve_project_root(config)
    sources: list[Path] = []
    if chapter_number is not None:
        sources.extend(
            [
                root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json",
                root / "50_workbench" / "writing_tasks" / f"ch{chapter_number:03d}.md",
                root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json",
            ]
        )
    sources.append(root / "50_workbench" / "graph_reports" / "graph_check.md")

    corpus = [text or ""]
    checked: list[str] = []
    for path in sources:
        if path.exists():
            checked.append(relative_path(root, path))
            corpus.append(safe_read_text(path))
    gaps = extract_gap_phrases("\n".join(corpus))
    plan_dir = root / "50_workbench" / "research_plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"ch{chapter_number:03d}" if chapter_number is not None else "project"
    report_file = plan_dir / f"knowledge_gaps_{suffix}.json"
    plan_file = plan_dir / f"knowledge_gaps_{suffix}.md"
    payload = {
        "chapter_number": chapter_number,
        "gaps": gaps,
        "sources_checked": checked,
        "recommended_actions": [
            f"research search project.yaml \"{gap}\" --json" for gap in gaps[:5]
        ],
        "created_at": utc_now(),
    }
    atomic_write_text(report_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(
        plan_file,
        "\n".join(
            [
                f"# Knowledge Gap Plan {suffix}",
                "",
                "## Gaps",
                "",
                *([f"- {gap}" for gap in gaps] or ["- None"]),
                "",
                "## Sources Checked",
                "",
                *([f"- `{source}`" for source in checked] or ["- None"]),
                "",
                "Research search writes inbox only. Promotion is required before canon/RAG/graph changes.",
                "",
            ]
        ),
    )
    return KnowledgeGapResult(
        chapter_number=chapter_number,
        report_file=str(report_file),
        plan_file=str(plan_file),
        gaps=tuple(gaps),
        sources_checked=tuple(checked),
    )


def extract_gap_phrases(text: str) -> list[str]:
    gaps: list[str] = []
    patterns = [
        r"(?:research gap|needs research|knowledge gap|verify|unknown)\s*[:：-]\s*([^\n。；;]{2,80})",
        r"(?:资料缺口|需要考证|待考证|未知设定)\s*[:：-]?\s*([^\n。；;]{2,80})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            gaps.append(match.group(1).strip())
    warning_markers = ("graph_consistency", "ability boundary", "location conflict", "历史", "制度", "地理", "术语")
    for line in text.splitlines():
        if any(marker in line for marker in warning_markers):
            cleaned = re.sub(r"^[#*\-\s`]+", "", line).strip()
            if cleaned:
                gaps.append(cleaned[:80])
    if not gaps:
        keywords = extract_keywords(text)[:5]
        gaps.extend(f"verify story detail: {keyword}" for keyword in keywords)
    return dedupe(gaps)[:12]


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def select_web_fetcher(config: ConfigDocument, fetcher: WebFetcher | None) -> WebFetcher:
    if fetcher is not None:
        return fetcher
    provider = search_provider_name(config, None)
    if provider == "zh.wikipedia":
        return fetch_web_results
    if provider == "static_fallback":
        return fetch_static_fallback
    if provider == "duckduckgo_html":
        return fetch_duckduckgo_html
    raise ResearchError(f"Unsupported research.search_provider: {provider}")


def search_provider_name(config: ConfigDocument, fetcher: WebFetcher | None) -> str:
    if fetcher is not None:
        return "injected_fetcher"
    return str(config.data.get("research", {}).get("search_provider") or "zh.wikipedia")


def fetch_web_results(query: str, limit: int, timeout: int) -> list[dict[str, Any]]:
    """Fetch lightweight search results from the zh.wikipedia search API."""

    params = urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": max(1, min(limit, 10)),
            "format": "json",
            "utf8": 1,
        }
    )
    url = f"https://zh.wikipedia.org/w/api.php?{params}"
    request = Request(url, headers={"User-Agent": "longform-novel-engine/0.1 research"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = payload.get("query", {}).get("search", [])
    results = []
    for row in rows[:limit]:
        title = str(row.get("title") or query)
        summary = clean_html(str(row.get("snippet") or ""))
        results.append(
            {
                "type": "web_search_result",
                "provider": "zh.wikipedia",
                "title": title,
                "url": f"https://zh.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                "summary": summary,
                "credibility": "reference",
            }
        )
    return results


def fetch_static_fallback(query: str, limit: int, timeout: int) -> list[dict[str, Any]]:
    return [
        {
            "type": "web_search_fallback",
            "provider": "static_fallback",
            "title": query,
            "url": f"https://www.google.com/search?q={quote(query)}",
            "summary": "Static fallback provider; manually review sources before promotion.",
            "credibility": "unverified",
        }
    ][: max(1, limit)]


def fetch_duckduckgo_html(query: str, limit: int, timeout: int) -> list[dict[str, Any]]:
    url = f"https://duckduckgo.com/html/?q={quote(query)}"
    request = Request(url, headers={"User-Agent": "longform-novel-engine/1.0"})
    with urlopen(request, timeout=timeout) as response:
        html = response.read().decode("utf-8", errors="ignore")
    results: list[dict[str, Any]] = []
    for match in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL):
        title = re.sub(r"<[^>]+>", "", unescape(match.group(2))).strip()
        href = unescape(match.group(1)).strip()
        if title:
            results.append(
                {
                    "type": "web_search_result",
                    "provider": "duckduckgo_html",
                    "title": title,
                    "url": href,
                    "summary": title,
                    "credibility": "search_result",
                }
            )
        if len(results) >= limit:
            break
    return results


def make_inbox_payload(
    config: ConfigDocument,
    *,
    title: str,
    content: str,
    source_type: str,
    source_url: str | None,
    sources: list[dict[str, Any]],
    tags: list[str],
    external_id: str,
) -> dict[str, Any]:
    root = resolve_project_root(config)
    item_id = stable_research_id(source_type, external_id, content)
    candidate_scope = infer_candidate_impact_scope(root, content)
    return {
        "id": item_id,
        "status": "inbox",
        "title": title.strip() or item_id,
        "source_type": source_type,
        "source_url": source_url,
        "sources": sources,
        "summary": summarize(content, 260),
        "credibility": infer_credibility(sources),
        "candidate_impact_scope": candidate_scope,
        "tags": tags,
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content_file": f"{item_id}.md",
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


def write_inbox_item(config: ConfigDocument, payload: dict[str, Any], content: str) -> tuple[Path, Path]:
    root = resolve_project_root(config)
    inbox = root / str(config.data.get("research", {}).get("inbox_dir", "50_workbench/research_inbox"))
    inbox.mkdir(parents=True, exist_ok=True)
    item_json = inbox / f"{payload['id']}.json"
    item_md = inbox / f"{payload['id']}.md"
    payload["content_file"] = relative_path(root, item_md)
    atomic_write_text(item_json, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(item_md, format_research_markdown(payload, content))
    return item_json, item_md


def write_research_rag_chunk(config: ConfigDocument, canon_record: dict[str, Any]) -> Path:
    root = resolve_project_root(config)
    chunks_dir = root / "60_rag" / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    item_id = canon_record["id"]
    text = f"{canon_record.get('title', '')}\n\n{canon_record.get('content', '')}".strip()
    path = chunks_dir / f"{item_id}.json"
    payload = {
        "research_item_id": item_id,
        "source_path": "10_bible/research_canon.jsonl",
        "chunks": [
            {
                "id": f"research:{item_id}:0",
                "chapter_number": None,
                "chunk_index": 0,
                "title": canon_record.get("title"),
                "text": text,
                "keywords": extract_keywords(text),
                "word_count": estimate_words(text),
                "token_estimate": max(1, estimate_words(text) // 2),
                "metadata": {
                    "canon": True,
                    "source_type": "research_promote",
                    "research_item_id": item_id,
                    "source_url": canon_record.get("source_url"),
                },
            }
        ],
        "updated_at": utc_now(),
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def update_research_graph(config: ConfigDocument, canon_record: dict[str, Any]) -> Path:
    root = resolve_project_root(config)
    graph_path = root / "30_state" / "story_graph.json"
    graph = read_json(graph_path, default={})
    if not isinstance(graph, dict):
        graph = {}
    graph.setdefault("entities", [])
    graph.setdefault("relationships", [])
    graph.setdefault("events", [])
    event = {
        "id": f"research:{canon_record['id']}",
        "title": f"Research Canon: {canon_record.get('title')}",
        "chapter_number": None,
        "participants": [],
        "consequences": canon_record.get("summary") or summarize(canon_record.get("content", ""), 240),
        "opens_threads": [],
        "closes_threads": [],
        "source_path": "10_bible/research_canon.jsonl",
        "metadata": {
            "research_item_id": canon_record["id"],
            "source_url": canon_record.get("source_url"),
            "promoted_at": canon_record.get("promoted_at"),
        },
    }
    upsert_by_id(graph["events"], event)
    graph["updated_at"] = utc_now()
    atomic_write_text(graph_path, json.dumps(graph, ensure_ascii=False, indent=2) + "\n")
    return graph_path


def next_context_chapter(root: Path) -> int:
    state = read_json(root / "30_state" / "novel_state.json", default={})
    if isinstance(state, dict):
        last_finalized = as_int(state.get("last_finalized_chapter"))
        if last_finalized > 0:
            return last_finalized + 1
    final_numbers = [
        number
        for path in sorted([*(root / "40_manuscript" / "final").glob("*.md"), *(root / "40_manuscript" / "final").glob("*.txt")])
        if (number := parse_chapter_number(path)) is not None
    ]
    return (max(final_numbers) + 1) if final_numbers else 1


def research_context_query(canon_record: dict[str, Any], keywords: tuple[str, ...] | list[str]) -> str:
    pieces = [
        str(canon_record.get("title") or ""),
        str(canon_record.get("summary") or ""),
        " ".join(str(item) for item in keywords[:12]),
    ]
    return " ".join(piece for piece in pieces if piece).strip() or str(canon_record["id"])


def resolve_research_item_path(config: ConfigDocument, item: str) -> Path:
    root = resolve_project_root(config)
    raw = Path(item).expanduser()
    if raw.exists():
        return raw.resolve()
    inbox = root / str(config.data.get("research", {}).get("inbox_dir", "50_workbench/research_inbox"))
    candidates = [inbox / item, inbox / f"{item}.json"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise ResearchError(f"Research item not found: {item}")


def research_text(root: Path, item: dict[str, Any]) -> str:
    content_file = str(item.get("content_file") or "")
    if content_file:
        raw_path = Path(content_file)
        candidates = [raw_path if raw_path.is_absolute() else root / raw_path]
        candidates.append(root / "50_workbench" / "research_inbox" / raw_path.name)
        for path in candidates:
            if path.exists():
                text = safe_read_text(path)
                return strip_research_markdown(text)
    return str(item.get("content") or item.get("summary") or "")


def infer_candidate_impact_scope(root: Path, content: str) -> dict[str, Any]:
    keywords = extract_keywords(content)[:12]
    return {
        "keywords": keywords,
        "characters": match_bible_entities(root, keywords, content, "10_bible/characters.json")[:8],
        "chapters": match_chapters(root, keywords, content)[:8],
        "graph_nodes": match_graph_nodes(root, keywords, content)[:8],
    }


def match_bible_entities(root: Path, keywords: tuple[str, ...] | list[str], text: str, relative: str) -> list[str]:
    records = normalize_collection(read_json(root / relative, default=[]))
    matches = []
    for record in records:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or record.get("title") or "")
        aliases = [str(alias) for alias in normalize_list(record.get("aliases"))]
        if has_text_match(text, keywords, [name, *aliases, str(record.get("description") or "")]):
            matches.append(name or str(record.get("id")))
    return unique(matches)


def match_chapters(root: Path, keywords: tuple[str, ...] | list[str], text: str) -> list[str]:
    matches = []
    for directory in (root / "40_manuscript" / "summaries", root / "40_manuscript" / "final"):
        for path in sorted([*directory.glob("*.md"), *directory.glob("*.txt")]):
            content = safe_read_text(path)
            if has_text_match(text, keywords, [content, path.stem]):
                matches.append(path.stem)
    return unique(matches)


def match_outline_collection(root: Path, keywords: tuple[str, ...] | list[str], text: str, relative: str) -> list[str]:
    payload = read_json(root / relative, default=[])
    records = normalize_collection(payload.get("items") if isinstance(payload, dict) else payload)
    matches = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        label = str(record.get("id") or record.get("title") or record.get("description") or f"item:{index}")
        if has_text_match(text, keywords, [json.dumps(record, ensure_ascii=False)]):
            matches.append(label)
    return unique(matches)


def match_graph_nodes(root: Path, keywords: tuple[str, ...] | list[str], text: str) -> list[str]:
    graph = read_json(root / "30_state" / "story_graph.json", default={})
    matches = []
    for group in ("entities", "events"):
        for record in normalize_collection(graph.get(group) if isinstance(graph, dict) else []):
            if not isinstance(record, dict):
                continue
            label = str(record.get("name") or record.get("title") or record.get("id") or "")
            if has_text_match(text, keywords, [label, json.dumps(record, ensure_ascii=False)]):
                matches.append(label or str(record.get("id")))
    return unique(matches)


def match_future_cards(root: Path, keywords: tuple[str, ...] | list[str], text: str) -> list[str]:
    current = current_chapter(root)
    matches = []
    for path in sorted((root / "20_outline" / "chapter_cards").glob("*.json")):
        number = parse_chapter_number(path)
        if number is not None and number <= current:
            continue
        payload = read_json(path, default={})
        if has_text_match(text, keywords, [json.dumps(payload, ensure_ascii=False), path.stem]):
            matches.append(path.stem)
    return unique(matches)


def has_text_match(source_text: str, keywords: tuple[str, ...] | list[str], candidates: list[str]) -> bool:
    haystack = "\n".join(candidates)
    if not haystack.strip():
        return False
    if any(candidate and candidate in source_text for candidate in candidates):
        return True
    lowered = haystack.lower()
    return any(keyword.lower() in lowered for keyword in keywords if len(keyword) >= 2)


def format_research_markdown(payload: dict[str, Any], content: str) -> str:
    lines = [
        f"# {payload['title']}",
        "",
        f"- ID: {payload['id']}",
        f"- Status: {payload['status']}",
        f"- Source type: {payload['source_type']}",
        f"- Credibility: {payload.get('credibility', 'unknown')}",
        "",
        "## Sources",
        "",
    ]
    for source in payload.get("sources", []):
        lines.append(f"- {source.get('title') or source.get('url') or source.get('path')} ({source.get('credibility', 'unknown')})")
    lines.extend(["", "## Content", "", content.strip(), ""])
    return "\n".join(lines)


def format_search_content(query: str, sources: list[dict[str, Any]], network_status: str) -> str:
    lines = [
        f"Query: {query}",
        f"Network status: {network_status}",
        "",
        "Results:",
    ]
    for index, source in enumerate(sources, start=1):
        lines.extend(
            [
                f"{index}. {source.get('title')}",
                f"   URL: {source.get('url')}",
                f"   Credibility: {source.get('credibility', 'unknown')}",
                f"   Summary: {source.get('summary', '')}",
            ]
        )
    return "\n".join(lines)


def format_impact_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Research Impact: {payload.get('title')}",
        "",
        f"- Research item: {payload['research_item_id']}",
        f"- Status: {payload.get('status')}",
        f"- Generated at: {payload['generated_at']}",
        f"- Keywords: {', '.join(payload.get('keywords', [])) or 'none'}",
        "",
    ]
    labels = {
        "characters": "Affected Characters",
        "chapters": "Affected Chapters",
        "foreshadowing": "Affected Foreshadowing",
        "graph_nodes": "Affected Graph Nodes",
        "future_cards": "Affected Future Chapter Cards",
    }
    impacts = payload.get("impacts", {})
    for key, title in labels.items():
        lines.extend([f"## {title}", ""])
        lines.extend([f"- {item}" for item in impacts.get(key, [])] or ["- None"])
        lines.append("")
    lines.extend(["## Recommended Actions", ""])
    lines.extend([f"- {item}" for item in payload.get("recommended_actions", [])] or ["- None"])
    lines.append("")
    return "\n".join(lines)


def recommended_actions(impacts: dict[str, list[str]]) -> list[str]:
    actions = ["Promote only after author/agent review confirms this belongs to canon."]
    if impacts.get("characters"):
        actions.append("Review character state and relationships before using this in future chapters.")
    if impacts.get("chapters") or impacts.get("future_cards"):
        actions.append("Check affected chapter cards and summaries for contradiction.")
    if impacts.get("graph_nodes"):
        actions.append("Run graph check after promotion.")
    return actions


def upsert_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").lstrip("\ufeff").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("id") != record.get("id"):
                records.append(item)
    records.append(record)
    atomic_write_text(path, "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records))


def upsert_by_id(items: list[Any], new_item: dict[str, Any]) -> None:
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("id") == new_item.get("id"):
            merged = dict(item)
            merged.update(new_item)
            items[index] = merged
            return
    items.append(new_item)


def current_chapter(root: Path) -> int:
    state = read_json(root / "30_state" / "novel_state.json", default={})
    if isinstance(state, dict):
        try:
            return int(state.get("current_chapter") or state.get("last_finalized_chapter") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def stable_research_id(source_type: str, external_id: str, content: str) -> str:
    digest = hashlib.sha256(f"{source_type}\n{external_id}\n{content}".encode("utf-8")).hexdigest()[:12]
    return f"research_{digest}"


def extract_keywords(text: str) -> list[str]:
    terms: set[str] = set()
    lowered = text.lower()
    for token in re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{1,}", lowered):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) >= 2:
                terms.add(token)
            if len(token) > 2:
                for index in range(0, len(token) - 1):
                    terms.add(token[index : index + 2])
        else:
            terms.add(token)
    return sorted(terms, key=lambda item: (-len(item), item))


def extract_title(text: str, *, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return summarize(first, 48) if first else fallback


def summarize(text: str, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def strip_research_markdown(text: str) -> str:
    marker = "## Content"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text.strip()


def clean_html(value: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", value)).strip()


def infer_credibility(sources: list[dict[str, Any]]) -> str:
    values = {str(source.get("credibility") or "unknown") for source in sources}
    if "user_provided" in values:
        return "user_provided"
    if "reference" in values:
        return "reference"
    if "unverified" in values:
        return "unverified"
    return "unknown"


def parse_chapter_number(path: Path) -> int | None:
    numeric = re.search(r"(\d{1,5})", path.stem)
    return int(numeric.group(1)) if numeric else None
    match = re.search(r"(?:ch|chapter[_-]?|第)?0*(\d{1,5})", path.stem, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def normalize_collection(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def normalize_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value]
    return [value]


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")


def read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except json.JSONDecodeError:
        return default


def estimate_words(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
