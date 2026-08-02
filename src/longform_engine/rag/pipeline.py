"""Local RAG build, query, and context assembly."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import re

from longform_engine.config import ConfigDocument
from longform_engine.db import sync_database
from longform_engine.db.sqlite_index import connect, database_path
from longform_engine.graph import retrieve_graph
from longform_engine.models import cosine_similarity, embed_text_with_provider, ensure_models_ready, rerank_pair
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.vectorstore import VectorQuery
from longform_engine.vectorstore import query as query_vector_store
from longform_engine.vectorstore import record_from_embedding
from longform_engine.vectorstore import sync_records as sync_vector_store


FORBIDDEN_SEMANTIC_SOURCE_FRAGMENTS = ("agent_drafts", "research_" + "inbox", "40_manuscript/draft")


@dataclass(frozen=True)
class RagBuildStats:
    """Counts produced by RAG chunk construction."""

    chapters: int
    chunks: int
    output_dir: str
    embeddings: int = 0


@dataclass(frozen=True)
class RagHit:
    """A query hit with explainable scoring details."""

    id: str
    chapter_number: int | None
    chunk_index: int
    score: float
    text: str
    keywords: tuple[str, ...]
    source_path: str | None
    reasons: tuple[str, ...]
    entities: tuple[str, ...] = ()
    events: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    foreshadow_refs: tuple[str, ...] = ()
    conflict_level: str = "unknown"
    semantic_score: float = 0.0
    rerank_score: float = 0.0
    consistency_reason: str = ""
    source_reason: str = ""
    memory_type: str = ""
    graph_score: float = 0.0
    hop_distance: int = 0
    path_reason: str = ""
    evidence_span: str = ""


@dataclass(frozen=True)
class RagQueryResult:
    """Query result persisted to cache and returned to CLI."""

    query: str
    hits: tuple[RagHit, ...]
    cache_file: str


@dataclass(frozen=True)
class RagContextResult:
    """Context document generation result."""

    query: str
    chapter_number: int | None
    context_file: str
    hit_count: int


def build_chunks(
    config: ConfigDocument,
    *,
    max_chars: int | None = None,
    overlap_chars: int | None = None,
    with_embeddings: bool = False,
    chapter_numbers: Iterable[int] | None = None,
    sync_index: bool = True,
) -> RagBuildStats:
    """Build paragraph-aware RAG chunks from all or selected finalized chapters."""

    root = resolve_project_root(config)
    final_dir = root / "40_manuscript" / "final"
    chunks_dir = root / "60_rag" / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    configured_max = int(config.data.get("rag", {}).get("chunk_max_chars", 900))
    configured_overlap = int(config.data.get("rag", {}).get("chunk_overlap_chars", 120))
    max_chars = max_chars or configured_max
    overlap_chars = overlap_chars if overlap_chars is not None else configured_overlap

    chapter_count = 0
    chunk_count = 0
    active_final_chapters: set[int] = set()
    all_final_paths = sorted([*final_dir.glob("*.md"), *final_dir.glob("*.txt")])
    selected_chapters = {int(value) for value in chapter_numbers} if chapter_numbers is not None else None
    final_paths = [
        path
        for path in all_final_paths
        if selected_chapters is None or parse_chapter_number(path) in selected_chapters
    ]
    for path in final_paths:
        chapter_number = parse_chapter_number(path) or chapter_count + 1
        active_final_chapters.add(chapter_number)
        text = safe_read_text(path)
        title = extract_title(text, path)
        chapter_meta = build_chapter_metadata(root, chapter_number, text, title)
        chunks = []
        for index, chunk_text in enumerate(split_text(text, max_chars=max_chars, overlap_chars=overlap_chars)):
            keywords = extract_keywords(chunk_text, title)
            chunk_meta = dict(chapter_meta)
            chunk_meta.update(
                {
                    "source": relative_path(root, path),
                    "builder": "paragraph_aware_v2",
                    "source_eligibility": "final_manuscript",
                }
            )
            chunks.append(
                {
                    "id": f"ch{chapter_number:03d}:{index}",
                    "chapter_number": chapter_number,
                    "chunk_index": index,
                    "title": title,
                    "text": chunk_text,
                    "keywords": keywords,
                    "word_count": estimate_words(chunk_text),
                    "token_estimate": max(1, estimate_words(chunk_text) // 2),
                    "metadata": chunk_meta,
                }
            )
            chunk_count += 1

        if chunks:
            payload = {
                "chapter_number": chapter_number,
                "title": title,
                "source_path": relative_path(root, path),
                "chunks": chunks,
                "updated_at": utc_now(),
            }
            atomic_write_text(chunks_dir / f"ch{chapter_number:03d}.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            chapter_count += 1

    if selected_chapters is None:
        active_final_chapters = {
            number
            for path in all_final_paths
            if (number := parse_chapter_number(path)) is not None
        }
        remove_stale_final_chunks(chunks_dir, active_final_chapters)
    if sync_index:
        sync_database(config)
    embedding_count = 0
    if with_embeddings:
        embedding_count = build_embeddings(config)
    return RagBuildStats(chapters=chapter_count, chunks=chunk_count, output_dir=str(chunks_dir), embeddings=embedding_count)


def query(
    config: ConfigDocument,
    query_text: str,
    *,
    top_k: int | None = None,
    candidate_pool: int | None = None,
    semantic: bool = False,
    chapter_number: int | None = None,
) -> RagQueryResult:
    """Run a lightweight SQLite hybrid query over chunk text, keywords, and metadata."""

    if not query_text.strip():
        raise ValueError("RAG query cannot be empty.")

    if not database_path(config).exists():
        sync_database(config)
    rag_config = config.data.get("rag", {})
    top_k = top_k or int(rag_config.get("top_k", 12))
    candidate_pool = candidate_pool or int(rag_config.get("candidate_pool_size", 40))
    hits = retrieve_hits(config, query_text, top_k=top_k, candidate_pool=candidate_pool, semantic=semantic, chapter_number=chapter_number)
    cache_file = write_query_cache(config, query_text, hits)
    return RagQueryResult(query=query_text, hits=tuple(hits), cache_file=str(cache_file))


def build_context(
    config: ConfigDocument,
    *,
    chapter_number: int | None = None,
    query_text: str | None = None,
    top_k: int | None = None,
    semantic: bool = False,
) -> RagContextResult:
    """Build the next chapter context document from RAG hits and project state."""

    root = resolve_project_root(config)
    if query_text is None:
        title = config.data["project"]["title"]
        query_text = f"{title} 第{chapter_number or '下一'}章 主线 人物 伏笔 节奏"
    result = query(config, query_text, top_k=top_k, semantic=semantic, chapter_number=chapter_number)

    context_path = root / "60_rag" / "context" / "next_plot_context.md"
    lines = [
        "# Next Plot Context",
        "",
        f"- Query: {query_text}",
        f"- Target chapter: {chapter_number if chapter_number is not None else 'unknown'}",
        f"- Semantic mode: {'enabled' if semantic else 'disabled'}",
        f"- Generated at: {utc_now()}",
        "",
        "## Recent Chapters",
        "",
        *format_recent_chapters(config, chapter_number=chapter_number),
        "",
        "## Retrieval Hits",
        "",
    ]

    if result.hits:
        for hit in result.hits:
            lines.extend(
                [
                    f"### {hit.id}",
                    "",
                    f"- Chapter: {hit.chapter_number if hit.chapter_number is not None else 'unknown'}",
                    f"- Score: {hit.score:.3f}",
                    f"- Semantic score: {hit.semantic_score:.3f}",
                    f"- Rerank score: {hit.rerank_score:.3f}",
                    f"- Consistency reason: {hit.consistency_reason or 'n/a'}",
                    f"- Source: {hit.source_path or 'unknown'}",
                    f"- Source reason: {hit.source_reason or 'retrieval candidate'}",
                    f"- Memory type: {hit.memory_type or 'chunk'}",
                    f"- Reasons: {', '.join(hit.reasons) if hit.reasons else 'metadata match'}",
                    f"- Keywords: {', '.join(hit.keywords) if hit.keywords else 'none'}",
                    f"- Entities: {', '.join(hit.entities) if hit.entities else 'none'}",
                    f"- Events: {', '.join(hit.events) if hit.events else 'none'}",
                    f"- Locations: {', '.join(hit.locations) if hit.locations else 'none'}",
                    f"- Foreshadow refs: {', '.join(hit.foreshadow_refs) if hit.foreshadow_refs else 'none'}",
                    f"- Conflict level: {hit.conflict_level}",
                    "",
                    trim_text(hit.text, 700),
                    "",
                ]
            )
    else:
        lines.extend(["No retrieval hits yet.", ""])

    lines.extend(
        [
            "## Temporal Context State",
            "",
            *format_tcs(config, chapter_number=chapter_number),
            "",
            "## Story State",
            "",
            *format_story_state(config),
            "",
            "## Relationship Snippets",
            "",
            *format_relation_snippets(config, result.hits),
            "",
            "## Graph Facts",
            "",
            *format_graph_facts(config, result.hits),
            "",
            "## Unresolved Threads",
            "",
            *format_unresolved_threads(config),
            "",
            "## Forbidden Repeats",
            "",
            *format_forbidden_repeats(config, result.hits),
            "",
            "## Usage Notes",
            "",
            "- 只能把以上内容作为已审核上下文使用。",
            "- 未 promote 的 research inbox 资料不能视为 canon。",
            "- 若上下文与章节卡冲突，先执行影响分析或改纲流程。",
            "",
        ]
    )
    atomic_write_text(context_path, "\n".join(lines))
    return RagContextResult(
        query=query_text,
        chapter_number=chapter_number,
        context_file=str(context_path),
        hit_count=len(result.hits),
    )


def retrieve_hits(
    config: ConfigDocument,
    query_text: str,
    *,
    top_k: int,
    candidate_pool: int,
    semantic: bool = False,
    chapter_number: int | None = None,
) -> list[RagHit]:
    """Score candidate chunks from SQLite using coarse metadata and fine text rerank."""

    db_path = database_path(config)
    if not db_path.exists():
        sync_database(config)
    terms = extract_query_terms(query_text)
    lower_query = query_text.lower()
    rag_config = config.data.get("rag", {})
    keyword_weight = float(rag_config.get("keyword_weight", 0.25))
    metadata_weight = float(rag_config.get("metadata_weight", 0.20))
    semantic_weight = float(rag_config.get("semantic_weight", 0.55))

    semantic_status = ensure_models_ready(config, allow_download=True, require_reranker=False) if semantic else None
    embedding_only_rerank = bool(
        semantic_status
        and semantic_status.embedding_loadable
        and not semantic_status.reranker_loadable
        and semantic_status.profile != "local-hash"
    )
    query_vector = embed_text_with_provider(config, query_text) if semantic else []
    root = resolve_project_root(config)
    ledger_scores = semantic_ledger_route_scores(root, query_text, chapter_number=chapter_number)
    vector_hits = (
        query_vector_store(
            config,
            VectorQuery(
                vector=tuple(query_vector),
                top_k=max(candidate_pool * 2, top_k),
                owner_types=("chapter_chunk", "scene_memory", "chapter_memory", "arc_memory", "character_memory"),
                max_chapter=(chapter_number - 1) if chapter_number and chapter_number > 1 else None,
            ),
        )
        if semantic
        else []
    )
    rows = load_chunk_candidates(
        db_path,
        terms=terms,
        vector_owner_ids={
            hit.owner_id for hit in vector_hits if hit.owner_type == "chapter_chunk"
        },
        ledger_chapters=set(ledger_scores),
        candidate_pool=candidate_pool,
        semantic=semantic,
        chapter_number=chapter_number,
    )
    chapter_vector_scores = {
        hit.owner_id: float(hit.score)
        for hit in vector_hits
        if hit.owner_type == "chapter_chunk"
    }
    lexical_fallback_active = semantic and not vector_hits
    coarse_scored: list[tuple[float, float, Any, dict[str, Any], tuple[str, ...], str]] = []
    for row in rows:
        metadata = loads_json(row["metadata_json"], default={})
        source_path = str(row["source_path"] or "")
        if not is_allowed_rag_source(config, source_path, metadata):
            continue
        text = str(row["text"])
        keywords = tuple(str(item) for item in loads_json(row["keywords_json"], default=[]))
        haystack = " ".join([text, " ".join(keywords), json.dumps(metadata, ensure_ascii=False)]).lower()
        exact = 1.0 if lower_query and lower_query in haystack else 0.0
        term_overlap = score_term_overlap(terms, haystack)
        keyword_overlap = score_term_overlap(terms, " ".join(keywords).lower())
        metadata_overlap = score_term_overlap(terms, json.dumps(metadata, ensure_ascii=False).lower())
        entity_overlap = score_term_overlap(terms, " ".join(normalize_strings(metadata.get("entities"))).lower())
        event_overlap = score_term_overlap(terms, " ".join(normalize_strings(metadata.get("events"))).lower())
        location_overlap = score_term_overlap(terms, " ".join(normalize_strings(metadata.get("locations"))).lower())
        foreshadow_overlap = score_term_overlap(terms, " ".join(normalize_strings(metadata.get("foreshadow_refs"))).lower())
        coarse = (
            exact * 3.0
            + term_overlap * 1.0
            + keyword_overlap * 1.2
            + metadata_overlap * 0.8
            + entity_overlap * 2.8
            + event_overlap * 2.0
            + location_overlap * 1.5
            + foreshadow_overlap * 1.6
        )
        semantic_coarse = chapter_vector_scores.get(str(row["id"]), 0.0) if semantic else 0.0
        if semantic and semantic_coarse > 0:
            coarse += semantic_coarse * 2.0
        ledger_score = ledger_scores.get(int(row["chapter_number"] or 0), 0.0)
        if ledger_score > 0:
            coarse += ledger_score * 2.4
        if coarse <= 0:
            continue
        coarse_scored.append((coarse, semantic_coarse, row, metadata, keywords, haystack))

    coarse_scored.sort(key=lambda item: (-item[0], -(item[2]["chapter_number"] or 0), item[2]["chunk_index"]))
    selected = coarse_scored[: max(candidate_pool, top_k)]

    hits: list[RagHit] = []
    max_chapter = max((int(row["chapter_number"] or 0) for _score, _semantic_coarse, row, _metadata, _keywords, _haystack in selected), default=0)
    for coarse, semantic_coarse, row, metadata, keywords, haystack in selected:
        text = str(row["text"])
        source_path = str(row["source_path"] or "")
        exact = 1.0 if lower_query and lower_query in haystack else 0.0
        term_overlap = score_term_overlap(terms, text.lower())
        keyword_overlap = score_term_overlap(terms, " ".join(keywords).lower())
        metadata_overlap = score_term_overlap(terms, json.dumps(metadata, ensure_ascii=False).lower())
        summary_overlap = score_term_overlap(terms, str(metadata.get("summary") or "").lower())
        entity_overlap = score_term_overlap(terms, " ".join(normalize_strings(metadata.get("entities"))).lower())
        event_overlap = score_term_overlap(terms, " ".join(normalize_strings(metadata.get("events"))).lower())
        location_overlap = score_term_overlap(terms, " ".join(normalize_strings(metadata.get("locations"))).lower())
        foreshadow_overlap = score_term_overlap(terms, " ".join(normalize_strings(metadata.get("foreshadow_refs"))).lower())
        recency = (int(row["chapter_number"] or 0) / max_chapter) if max_chapter else 0.0
        semantic_candidate_text = " ".join([text, json.dumps(metadata, ensure_ascii=False)])
        semantic_score = semantic_coarse if semantic else 0.0

        score = (
            semantic_weight * max(exact, term_overlap, summary_overlap)
            + keyword_weight * keyword_overlap
            + metadata_weight * metadata_overlap
            + entity_overlap * 0.45
            + event_overlap * 0.35
            + location_overlap * 0.25
            + foreshadow_overlap * 0.30
            + recency * 0.05
            + min(coarse, 6.0) * 0.04
        )
        consistency_reason = ""
        if semantic:
            consistency_score, consistency_reason = consistency_score_for_candidate(config, query_text, semantic_candidate_text, metadata)
            model_rerank = rerank_pair(config, query_text, semantic_candidate_text, fallback_score=max(semantic_score, semantic_coarse, consistency_score))
            score = score * 0.45 + max(semantic_score, semantic_coarse) * 0.30 + model_rerank * 0.15 + consistency_score * 0.10
        reasons = explain_reasons(
            exact,
            term_overlap,
            keyword_overlap,
            metadata_overlap,
            summary_overlap=summary_overlap,
            entity_overlap=entity_overlap,
            event_overlap=event_overlap,
            location_overlap=location_overlap,
            foreshadow_overlap=foreshadow_overlap,
        )
        if semantic and semantic_score > 0:
            reasons.append("semantic vector similarity")
        if ledger_scores.get(int(row["chapter_number"] or 0), 0.0) > 0:
            reasons.append("semantic ledger routed chapter")
        if lexical_fallback_active:
            reasons.append("warning: explicit lexical fallback; vector candidates unavailable")
        if embedding_only_rerank:
            reasons.append("warning: embedding-only semantic rerank")
        if score <= 0:
            continue
        hits.append(
            RagHit(
                id=str(row["id"]),
                chapter_number=row["chapter_number"],
                chunk_index=int(row["chunk_index"]),
                score=round(score, 6),
                text=text,
                keywords=keywords,
                source_path=source_path,
                reasons=tuple(reasons),
                entities=tuple(normalize_strings(metadata.get("entities"))),
                events=tuple(normalize_strings(metadata.get("events"))),
                locations=tuple(normalize_strings(metadata.get("locations"))),
                foreshadow_refs=tuple(normalize_strings(metadata.get("foreshadow_refs"))),
                conflict_level=str(metadata.get("conflict_level") or "unknown"),
                semantic_score=round(max(semantic_score, semantic_coarse), 6),
                rerank_score=round(score, 6),
                consistency_reason=consistency_reason,
                source_reason="final manuscript chunk",
                memory_type="chunk",
                evidence_span=str(metadata.get("evidence_span") or ""),
            )
        )

    if semantic:
        hits.extend(
            retrieve_memory_hits(
                config,
                query_text,
                vector_hits=vector_hits,
                top_k=max(candidate_pool, top_k),
                embedding_only_rerank=embedding_only_rerank,
            )
        )
        if chapter_number:
            hits.extend(retrieve_graph_rag_hits(config, query_text, chapter_number=chapter_number, query_vector=query_vector, top_k=max(candidate_pool, top_k)))

    hits.sort(key=lambda item: (-item.score, -(item.chapter_number or 0), item.chunk_index))
    return hits[:top_k]


def load_chunk_candidates(
    db_path: Path,
    *,
    terms: list[str],
    vector_owner_ids: set[str],
    ledger_chapters: set[int],
    candidate_pool: int,
    semantic: bool,
    chapter_number: int | None,
) -> list[Any]:
    """Load bounded ANN, lexical, and recent candidates for semantic retrieval."""

    columns = (
        "id, chapter_number, chunk_index, text, keywords_json, "
        "source_path, metadata_json"
    )
    rows_by_id: dict[str, Any] = {}
    vector_ids = sorted(vector_owner_ids)
    max_chapter = chapter_number - 1 if chapter_number and chapter_number > 1 else None
    with connect(db_path) as conn:
        if vector_ids:
            placeholders = ",".join("?" for _ in vector_ids)
            for row in conn.execute(
                f"""
                SELECT {columns}
                FROM chapter_chunks
                WHERE id IN ({placeholders})
                """,
                vector_ids,
            ).fetchall():
                rows_by_id[str(row["id"])] = row

        routed_chapters = sorted(
            chapter
            for chapter in ledger_chapters
            if max_chapter is None or chapter <= max_chapter
        )
        if routed_chapters:
            placeholders = ",".join("?" for _ in routed_chapters)
            for row in conn.execute(
                f"""
                SELECT {columns}
                FROM chapter_chunks
                WHERE chapter_number IN ({placeholders})
                ORDER BY chapter_number DESC, chunk_index ASC
                """,
                routed_chapters,
            ).fetchall():
                rows_by_id[str(row["id"])] = row

        recent_params: list[Any] = []
        recent_where = ""
        if max_chapter is not None:
            recent_where = "WHERE chapter_number <= ?"
            recent_params.append(max_chapter)
        for row in conn.execute(
            f"""
            SELECT {columns}
            FROM chapter_chunks
            {recent_where}
            ORDER BY COALESCE(chapter_number, 0) DESC, chunk_index ASC
            LIMIT ?
            """,
            [*recent_params, max(candidate_pool, 1)],
        ).fetchall():
            rows_by_id[str(row["id"])] = row

        lexical_terms = [term for term in terms if term][:6]
        if lexical_terms:
            term_clauses = []
            term_params: list[Any] = []
            for term in lexical_terms:
                term_clauses.append(
                    "(text LIKE ? OR keywords_json LIKE ? OR metadata_json LIKE ?)"
                )
                pattern = f"%{term}%"
                term_params.extend((pattern, pattern, pattern))
            chapter_clause = ""
            lexical_params: list[Any] = []
            if max_chapter is not None:
                chapter_clause = "chapter_number <= ? AND "
                lexical_params.append(max_chapter)
            lexical_params.extend(term_params)
            lexical_params.append(max(candidate_pool, 1))
            for row in conn.execute(
                f"""
                SELECT {columns}
                FROM chapter_chunks
                WHERE {chapter_clause}({" OR ".join(term_clauses)})
                ORDER BY COALESCE(chapter_number, 0) DESC, chunk_index ASC
                LIMIT ?
                """,
                lexical_params,
            ).fetchall():
                rows_by_id[str(row["id"])] = row

    return sorted(
        rows_by_id.values(),
        key=lambda row: (-(int(row["chapter_number"] or 0)), int(row["chunk_index"])),
    )


def build_embeddings(config: ConfigDocument) -> int:
    """Build deterministic semantic embeddings for canonical RAG/memory rows."""

    root = resolve_project_root(config)
    sync_database(config)
    model_status = ensure_models_ready(config, allow_download=True, require_reranker=True)
    model_name = model_status.embedding_model if model_status.status == "ready" else model_status.fallback or "local-hash"
    metadata_dir = root / "60_rag" / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    output = metadata_dir / "embeddings.jsonl"
    existing = {
        str(item.get("id")): item
        for item in iter_jsonl(output)
        if isinstance(item, dict) and item.get("id")
    }
    records: list[dict[str, Any]] = []
    db_path = database_path(config)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, chapter_number, text, source_path, metadata_json
            FROM chapter_chunks
            ORDER BY COALESCE(chapter_number, 0), chunk_index
            """,
        ).fetchall()
        for row in rows:
            metadata = loads_json(row["metadata_json"], default={})
            source_path = str(row["source_path"] or "")
            if not is_allowed_rag_source(config, source_path, metadata):
                continue
            vector_text = " ".join([str(row["text"]), json.dumps(metadata, ensure_ascii=False)])
            record_id = f"embedding:{row['id']}"
            vector, content_hash = reusable_embedding(config, existing.get(record_id), vector_text, model_name)
            records.append(
                {
                    "id": record_id,
                    "owner_type": "chapter_chunk",
                    "owner_id": str(row["id"]),
                    "chapter_number": row["chapter_number"],
                    "source_path": source_path,
                    "model": model_name,
                    "content_hash": content_hash,
                    "vector": vector,
                    "updated_at": utc_now(),
                }
            )

    if not is_memory_globally_stale(root):
        for path in sorted((root / "60_rag" / "memory" / "scenes").glob("*.json")):
            payload = read_json(path, default={})
            if not is_allowed_memory_payload(root, payload):
                continue
            records.append(embedding_record_for_memory(config, root, path, payload, owner_type="scene_memory", model=model_name, existing=existing))
        for owner_type, directory in (
            ("chapter_memory", root / "60_rag" / "memory" / "chapters"),
            ("arc_memory", root / "60_rag" / "memory" / "arcs"),
            ("character_memory", root / "60_rag" / "memory" / "characters"),
            ("style_memory", root / "60_rag" / "memory" / "style"),
        ):
            for path in sorted(directory.glob("*.json")):
                payload = read_json(path, default={})
                if owner_type != "style_memory" and not is_allowed_memory_payload(root, payload):
                    continue
                if owner_type == "style_memory" and not is_allowed_style_memory_payload(payload):
                    continue
                records.append(embedding_record_for_memory(config, root, path, payload, owner_type=owner_type, model=model_name, existing=existing))

    atomic_write_text(output, "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records))
    sync_database(config)
    vector_records = [record_from_embedding(record) for record in records]
    sync_vector_store(config, [record for record in vector_records if record is not None])
    return len(records)


def embedding_record_for_memory(
    config: ConfigDocument,
    root: Path,
    path: Path,
    payload: dict[str, Any],
    *,
    owner_type: str,
    model: str,
    existing: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    text = " ".join(
        [
            str(payload.get("summary") or ""),
            str(payload.get("text") or ""),
            str(payload.get("name") or ""),
            json.dumps(payload.get("fingerprint") or {}, ensure_ascii=False),
            " ".join(normalize_strings(payload.get("characters"))),
            " ".join(normalize_strings(payload.get("aliases"))),
            " ".join(normalize_strings(payload.get("current_beliefs"))),
            " ".join(normalize_strings(payload.get("knowledge_scope"))),
            " ".join(normalize_strings(payload.get("forbidden_actions"))),
            " ".join(normalize_strings(payload.get("events"))),
            str(payload.get("emotion_state") or ""),
            str(payload.get("conflict_state") or ""),
        ]
    )
    owner_id = path.stem
    record_id = f"embedding:{owner_type}:{owner_id}"
    vector, content_hash = reusable_embedding(config, existing.get(record_id), text, model)
    return {
        "id": record_id,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "chapter_number": payload.get("chapter"),
        "scene_number": payload.get("scene"),
        "source_path": relative_path(root, path),
        "model": model,
        "content_hash": content_hash,
        "vector": vector,
        "updated_at": utc_now(),
    }


def reusable_embedding(
    config: ConfigDocument,
    existing: dict[str, Any] | None,
    text: str,
    model: str,
) -> tuple[list[float], str]:
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if (
        isinstance(existing, dict)
        and existing.get("model") == model
        and existing.get("content_hash") == content_hash
        and isinstance(existing.get("vector"), list)
    ):
        return [float(item) for item in existing["vector"]], content_hash
    return embed_text_with_provider(config, text), content_hash


def retrieve_memory_hits(
    config: ConfigDocument,
    query_text: str,
    *,
    vector_hits: list[Any],
    top_k: int,
    embedding_only_rerank: bool = False,
) -> list[RagHit]:
    """Hydrate precomputed vector candidates from canonical memory files."""

    root = resolve_project_root(config)
    if is_memory_globally_stale(root):
        return []
    hits: list[RagHit] = []
    owner_labels = {
        "scene_memory": "scene",
        "chapter_memory": "chapter",
        "arc_memory": "arc",
        "character_memory": "character",
    }
    if not vector_hits:
        return retrieve_memory_lexical_fallback(config, query_text, top_k=top_k)
    for vector_hit in vector_hits:
        memory_type = owner_labels.get(str(vector_hit.owner_type))
        if not memory_type:
            continue
        path = root / str(vector_hit.source_path)
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        payload = read_json(path, default={})
        if not is_allowed_memory_payload(root, payload):
            continue
        text = memory_text(payload)
        semantic_score = float(vector_hit.score)
        term_score = score_term_overlap(extract_query_terms(query_text), text.lower())
        consistency_score, consistency_reason = consistency_score_for_candidate(config, query_text, text, payload)
        rerank_score = rerank_pair(config, query_text, text, fallback_score=max(semantic_score, consistency_score))
        score = semantic_score * 0.55 + term_score * 0.15 + consistency_score * 0.15 + rerank_score * 0.15
        if score <= 0:
            continue
        chapter_number = as_optional_int(payload.get("chapter")) or vector_hit.chapter_number or parse_chapter_number(path)
        hits.append(
            RagHit(
                id=f"memory:{memory_type}:{path.stem}",
                chapter_number=chapter_number,
                chunk_index=as_optional_int(payload.get("scene")) or 0,
                score=round(score, 6),
                text=text,
                keywords=tuple(extract_keywords(text)),
                source_path=relative_path(root, path),
                reasons=tuple(
                    [
                        "precomputed memory vector similarity",
                        *(["warning: embedding-only semantic rerank"] if embedding_only_rerank else []),
                    ]
                ),
                entities=tuple(normalize_strings(payload.get("characters"))),
                events=tuple(normalize_strings(payload.get("events"))),
                locations=tuple(normalize_strings(payload.get("location") or payload.get("locations"))),
                foreshadow_refs=tuple(normalize_strings(payload.get("foreshadow_refs"))),
                conflict_level=str(payload.get("conflict_state") or "unknown"),
                semantic_score=round(semantic_score, 6),
                rerank_score=round(rerank_score, 6),
                consistency_reason=consistency_reason,
                source_reason="canonical narrative memory via vector store",
                memory_type=memory_type,
            )
        )
    hits.sort(key=lambda item: (-item.score, -(item.chapter_number or 0), item.chunk_index))
    return hits[:top_k]


def retrieve_memory_lexical_fallback(config: ConfigDocument, query_text: str, *, top_k: int) -> list[RagHit]:
    """Degrade safely before the first vector build without embedding every candidate."""

    root = resolve_project_root(config)
    directories = {
        "scene": root / "60_rag" / "memory" / "scenes",
        "chapter": root / "60_rag" / "memory" / "chapters",
        "arc": root / "60_rag" / "memory" / "arcs",
        "character": root / "60_rag" / "memory" / "characters",
    }
    terms = extract_query_terms(query_text)
    coarse: list[tuple[float, str, Path, dict[str, Any], str, str]] = []
    for memory_type, directory in directories.items():
        for path in sorted(directory.glob("*.json")):
            payload = read_json(path, default={})
            if not is_allowed_memory_payload(root, payload):
                continue
            text = memory_text(payload)
            term_score = score_term_overlap(terms, text.lower())
            consistency_score, reason = consistency_score_for_candidate(config, query_text, text, payload)
            score = term_score * 0.65 + consistency_score * 0.35
            if score > 0:
                coarse.append((score, memory_type, path, payload, text, reason))
    coarse.sort(key=lambda item: (-item[0], item[2].as_posix()))
    hits: list[RagHit] = []
    for coarse_score, memory_type, path, payload, text, reason in coarse[: max(top_k * 3, top_k)]:
        rerank_score = rerank_pair(config, query_text, text, fallback_score=coarse_score)
        score = coarse_score * 0.8 + rerank_score * 0.2
        hits.append(
            RagHit(
                id=f"memory:{memory_type}:{path.stem}",
                chapter_number=as_optional_int(payload.get("chapter")) or parse_chapter_number(path),
                chunk_index=as_optional_int(payload.get("scene")) or 0,
                score=round(score, 6),
                text=text,
                keywords=tuple(extract_keywords(text)),
                source_path=relative_path(root, path),
                reasons=("lexical fallback: vector store has no candidates",),
                entities=tuple(normalize_strings(payload.get("characters"))),
                events=tuple(normalize_strings(payload.get("events"))),
                locations=tuple(normalize_strings(payload.get("location") or payload.get("locations"))),
                foreshadow_refs=tuple(normalize_strings(payload.get("foreshadow_refs"))),
                conflict_level=str(payload.get("conflict_state") or "unknown"),
                semantic_score=0.0,
                rerank_score=round(rerank_score, 6),
                consistency_reason=reason,
                source_reason="canonical narrative memory lexical fallback",
                memory_type=memory_type,
            )
        )
    hits.sort(key=lambda item: (-item.score, -(item.chapter_number or 0), item.chunk_index))
    return hits[:top_k]


def retrieve_graph_rag_hits(
    config: ConfigDocument,
    query_text: str,
    *,
    chapter_number: int,
    query_vector: list[float],
    top_k: int,
) -> list[RagHit]:
    """Convert graph traversal hits into RAG hits for semantic fusion."""

    result = retrieve_graph(config, query_text=query_text, chapter_number=chapter_number, top_k=top_k)
    hits: list[RagHit] = []
    for item in result.hits:
        text = " ".join([item.label, item.path_reason, item.evidence_span, json.dumps(item.payload, ensure_ascii=False)])
        semantic_score = cosine_similarity(query_vector, embed_text_with_provider(config, text))
        consistency_score, consistency_reason = consistency_score_for_candidate(config, query_text, text, item.payload)
        rerank_score = rerank_pair(config, query_text, text, fallback_score=max(semantic_score, consistency_score, item.graph_score))
        score = item.graph_score * 0.35 + semantic_score * 0.25 + rerank_score * 0.25 + consistency_score * 0.15
        hits.append(
            RagHit(
                id=f"graph:{item.kind}:{item.id}",
                chapter_number=max(item.related_chapters) if item.related_chapters else chapter_number,
                chunk_index=item.hop_distance,
                score=round(score, 6),
                text=text,
                keywords=tuple(extract_keywords(text)),
                source_path=item.source_path,
                reasons=("graph traversal", item.path_reason),
                entities=tuple(normalize_strings(item.payload.get("participants") or [item.payload.get("source"), item.payload.get("target")])),
                events=tuple([item.label]) if item.kind == "event" else (),
                locations=tuple(normalize_strings(item.payload.get("locations") or item.payload.get("location"))),
                foreshadow_refs=tuple([item.id]) if item.kind == "foreshadowing" else (),
                conflict_level=str(item.payload.get("status") or "unknown"),
                semantic_score=round(semantic_score, 6),
                rerank_score=round(rerank_score, 6),
                consistency_reason=consistency_reason,
                source_reason="graph traversal",
                memory_type=f"graph:{item.kind}",
                graph_score=item.graph_score,
                hop_distance=item.hop_distance,
                path_reason=item.path_reason,
                evidence_span=item.evidence_span,
            )
        )
    return hits


def is_memory_globally_stale(root: Path) -> bool:
    payload = read_json(root / "60_rag" / "memory" / "stale.json", default={})
    return isinstance(payload, dict) and bool(payload.get("stale"))


def is_allowed_memory_payload(root: Path, payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if str(payload.get("status") or "canonical").lower() == "stale":
        return False
    source = str(payload.get("source_path") or "").replace("\\", "/")
    if not source.startswith("40_manuscript/final/"):
        return False
    if any(part in source for part in FORBIDDEN_SEMANTIC_SOURCE_FRAGMENTS):
        return False
    return (root / source).exists()


def is_allowed_style_memory_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and str(payload.get("memory_type") or "") == "style" and str(payload.get("status") or "canonical") != "stale"


def consistency_score_for_candidate(config: ConfigDocument, query_text: str, candidate_text: str, metadata: dict[str, Any]) -> tuple[float, str]:
    """Score narrative consistency using TCS-like and graph-like facts."""

    root = resolve_project_root(config)
    tcs = latest_tcs_payload(root)
    graph = read_json(root / "30_state" / "story_graph.json", default={})
    haystack = " ".join([candidate_text, json.dumps(metadata, ensure_ascii=False), json.dumps(tcs, ensure_ascii=False)]).lower()
    query_lower = query_text.lower()
    reasons: list[str] = []
    score = 0.0
    causal_terms = ("because", "why", "原因", "因果", "为什么", "forgive", "原谅", "救", "让步", "apology", "rescue", "concession")
    emotion_terms = ("emotion", "情绪", "恨", "信任", "恐惧", "anger", "trust", "fear")
    relationship_terms = ("relationship", "关系", "盟友", "敌", "背叛", "ally", "enemy", "betray")
    location_terms = ("location", "地点", "转移", "抵达", "离开", "arrive", "leave")
    ability_terms = ("ability", "能力", "代价", "冷却", "限制", "cost", "cooldown", "limit")
    foreshadow_terms = ("foreshadow", "伏笔", "线索", "秘密", "clue", "secret")
    if any(term in query_lower for term in causal_terms) and any(term in haystack for term in ("rescue", "save", "救", "让步", "apology", "concession", "道歉")):
        score += 0.35
        reasons.append("causal support")
    if any(term in query_lower for term in emotion_terms) and any(str(item).lower() in haystack for item in normalize_strings(tcs.get("emotion_state")) + normalize_strings(metadata.get("emotion_state"))):
        score += 0.15
        reasons.append("emotion continuity")
    if any(term in query_lower for term in relationship_terms) and graph_relationship_overlap(graph, haystack):
        score += 0.20
        reasons.append("relationship temporal support")
    if any(term in query_lower for term in location_terms) and any(str(item).lower() in haystack for item in normalize_strings(tcs.get("locations")) + normalize_strings(metadata.get("locations"))):
        score += 0.10
        reasons.append("location continuity")
    if any(term in query_lower for term in ability_terms) and any(term in haystack for term in ("cost", "cooldown", "limit", "代价", "冷却", "限制")):
        score += 0.20
        reasons.append("ability boundary")
    if any(term in query_lower for term in foreshadow_terms) and any(term in haystack for term in ("foreshadow", "clue", "secret", "伏笔", "线索", "秘密")):
        score += 0.20
        reasons.append("foreshadow state")
    return min(1.0, score), ", ".join(reasons) if reasons else "semantic similarity only"


def latest_tcs_payload(root: Path) -> dict[str, Any]:
    candidates = sorted((root / "30_state" / "tcs").glob("ch*.json"))
    for path in reversed(candidates):
        payload = read_json(path, default={})
        if isinstance(payload, dict):
            return payload
    return {}


def graph_relationship_overlap(graph: Any, haystack: str) -> bool:
    if not isinstance(graph, dict):
        return False
    for relation in normalize_records(graph.get("relationships")):
        if not isinstance(relation, dict):
            continue
        for value in (relation.get("type"), relation.get("relation"), relation.get("status"), relation.get("source"), relation.get("target")):
            if value and str(value).lower() in haystack:
                return True
    return False


def memory_text(payload: dict[str, Any]) -> str:
    return " ".join(
        [
            str(payload.get("summary") or ""),
            str(payload.get("text") or ""),
            str(payload.get("name") or ""),
            " ".join(normalize_strings(payload.get("characters"))),
            " ".join(normalize_strings(payload.get("aliases"))),
            " ".join(normalize_strings(payload.get("current_beliefs"))),
            " ".join(normalize_strings(payload.get("knowledge_scope"))),
            " ".join(normalize_strings(payload.get("forbidden_actions"))),
            " ".join(normalize_strings(payload.get("events"))),
            str(payload.get("location") or ""),
            str(payload.get("emotion_state") or ""),
            str(payload.get("conflict_state") or ""),
            " ".join(normalize_strings(payload.get("evidence"))),
        ]
    ).strip()


def write_query_cache(config: ConfigDocument, query_text: str, hits: list[RagHit]) -> Path:
    root = resolve_project_root(config)
    cache_dir = root / "60_rag" / "query_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha256(query_text.encode("utf-8")).hexdigest()[:16]
    path = cache_dir / f"{signature}.json"
    payload = {
        "id": signature,
        "query": query_text,
        "cache_signature": signature,
        "context_word_count": sum(estimate_words(hit.text) for hit in hits),
        "hits": [asdict(hit) for hit in hits],
        "updated_at": utc_now(),
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def format_recent_chapters(config: ConfigDocument, *, chapter_number: int | None) -> list[str]:
    root = resolve_project_root(config)
    summary_dir = root / "40_manuscript" / "summaries"
    final_numbers = final_chapter_numbers(root)
    summaries: list[tuple[int, Path, str]] = []
    for path in sorted(summary_dir.glob("*.md")):
        number = parse_chapter_number(path)
        if number is None:
            continue
        if number not in final_numbers:
            continue
        if chapter_number is not None and number >= chapter_number:
            continue
        summaries.append((number, path, safe_read_text(path).strip()))
    summaries = summaries[-5:]
    if not summaries:
        return ["- No chapter summaries available yet."]
    return [f"- Ch{number:03d}: {trim_text(text, 180)}" for number, _path, text in summaries]


def format_story_state(config: ConfigDocument) -> list[str]:
    root = resolve_project_root(config)
    lines: list[str] = []
    for label, path in (
        ("Novel state", root / "30_state" / "novel_state.json"),
        ("Unresolved threads", root / "30_state" / "unresolved_threads.json"),
        ("Outline anchors", root / "20_outline" / "outline_anchors.json"),
        ("Pacing history", root / "30_state" / "pacing_history.json"),
    ):
        if path.exists():
            lines.append(f"- {label}: `{relative_path(root, path)}`")
    if not lines:
        lines.append("- No state files available.")
    return lines


def format_tcs(config: ConfigDocument, *, chapter_number: int | None) -> list[str]:
    if chapter_number is None:
        return ["- No target chapter; TCS not generated."]
    root = resolve_project_root(config)
    path = root / "30_state" / "tcs" / f"ch{chapter_number:03d}.json"
    if not path.exists():
        return [f"- TCS snapshot not found: `30_state/tcs/ch{chapter_number:03d}.json`"]
    payload = read_json(path, default={})
    if not isinstance(payload, dict):
        return [f"- TCS snapshot is invalid: `{relative_path(root, path)}`"]
    return [
        f"- Source: `{relative_path(root, path)}`",
        f"- Current characters: {', '.join(normalize_strings(payload.get('current_characters'))) or 'none'}",
        f"- Locations: {', '.join(normalize_strings(payload.get('locations'))) or 'none'}",
        f"- Emotion state: {payload.get('emotion_state') or 'unknown'}",
        f"- Recent events: {', '.join(normalize_strings(payload.get('recent_events'))) or 'none'}",
        f"- Unresolved conflicts: {', '.join(normalize_strings(payload.get('unresolved_conflicts'))) or 'none'}",
        f"- Open foreshadows: {', '.join(normalize_strings(payload.get('open_foreshadows'))) or 'none'}",
        f"- Active constraints: {', '.join(normalize_strings(payload.get('active_constraints'))) or 'none'}",
    ]


def format_relation_snippets(config: ConfigDocument, hits: tuple[RagHit, ...]) -> list[str]:
    root = resolve_project_root(config)
    graph = read_json(root / "30_state" / "story_graph.json", default={})
    if not isinstance(graph, dict):
        return ["- No relation snippets available."]
    entity_names = {name for hit in hits for name in hit.entities}
    if not entity_names:
        return ["- No relation snippets available."]
    id_to_name: dict[str, str] = {}
    for entity in graph.get("entities", []):
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("id") or "")
        name = str(entity.get("name") or entity_id)
        id_to_name[entity_id] = name
    lines: list[str] = []
    for relation in graph.get("relationships", []):
        if not isinstance(relation, dict):
            continue
        source = id_to_name.get(str(relation.get("source") or relation.get("from") or ""), str(relation.get("source") or relation.get("from") or ""))
        target = id_to_name.get(str(relation.get("target") or relation.get("to") or ""), str(relation.get("target") or relation.get("to") or ""))
        if source not in entity_names and target not in entity_names:
            continue
        relation_type = relation.get("type") or relation.get("relation") or "related"
        status = relation.get("status") or "active"
        lines.append(f"- {source} -> {target}: {relation_type} ({status})")
        if len(lines) >= 8:
            break
    return lines or ["- No relation snippets available."]


def format_graph_facts(config: ConfigDocument, hits: tuple[RagHit, ...]) -> list[str]:
    root = resolve_project_root(config)
    graph = read_json(root / "30_state" / "story_graph.json", default={})
    if not isinstance(graph, dict):
        return ["- No graph facts available."]
    entity_names = {name for hit in hits for name in hit.entities}
    event_titles = {event for hit in hits for event in hit.events}
    lines: list[str] = []
    for entity in graph.get("entities", []):
        if not isinstance(entity, dict):
            continue
        name = str(entity.get("name") or entity.get("id") or "")
        if name not in entity_names:
            continue
        status = entity.get("status") or entity.get("state") or entity.get("arc_status") or "unknown"
        lines.append(f"- Entity `{name}` status: {status}")
    for event in graph.get("events", []):
        if not isinstance(event, dict):
            continue
        title = str(event.get("title") or event.get("name") or event.get("id") or "")
        if title not in event_titles:
            continue
        chapter = event.get("chapter_number") or event.get("chapter") or "unknown"
        consequences = trim_text(str(event.get("consequences") or event.get("summary") or ""), 120)
        lines.append(f"- Event ch{chapter}: {title} - {consequences}")
    return lines[:12] or ["- No graph facts available."]


def format_unresolved_threads(config: ConfigDocument) -> list[str]:
    root = resolve_project_root(config)
    threads = read_json(root / "30_state" / "unresolved_threads.json", default=[])
    lines: list[str] = []
    for item in normalize_records(threads):
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "open").lower() in {"closed", "resolved", "done"}:
            continue
        title = item.get("title") or item.get("name") or item.get("id") or "unnamed thread"
        owner = item.get("owner") or item.get("entity") or ""
        lines.append(f"- {title}" + (f" ({owner})" if owner else ""))
        if len(lines) >= 10:
            break
    return lines or ["- No unresolved thread file entries."]


def format_forbidden_repeats(config: ConfigDocument, hits: tuple[RagHit, ...]) -> list[str]:
    root = resolve_project_root(config)
    anchors = read_json(root / "20_outline" / "outline_anchors.json", default={})
    configured = config.data.get("gates", {}).get("forbidden_repeats") or config.data.get("novel", {}).get("forbidden_experience")
    lines: list[str] = []
    for item in normalize_strings(configured):
        lines.append(f"- Config: {item}")
    for anchor in normalize_records(anchors):
        if not isinstance(anchor, dict):
            continue
        for field in ("forbidden_reveals", "forbidden_repeats", "do_not_repeat"):
            for item in normalize_strings(anchor.get(field)):
                lines.append(f"- Anchor: {item}")
    seen_sources = {hit.source_path for hit in hits if hit.source_path}
    if seen_sources:
        lines.append(f"- Do not restage retrieved passages verbatim from: {', '.join(sorted(seen_sources)[:6])}")
    return dedupe_lines(lines) or ["- No forbidden repeats configured."]


def build_chapter_metadata(root: Path, chapter_number: int, text: str, title: str) -> dict[str, Any]:
    ledger_path = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
    ledger = read_json(ledger_path, default={}) if ledger_path.exists() else None
    digest = ledger.get("chapter_digest") if isinstance(ledger, dict) and isinstance(ledger.get("chapter_digest"), dict) else {}
    summary_file_text = read_summary_text(root, chapter_number)
    pending_semantic_summary = summary_file_text.startswith("Pending unified semantic extraction")
    summary = str(digest.get("summary") or "").strip() or summary_file_text
    if pending_semantic_summary:
        summary = ""
    if not summary and ledger is None and not pending_semantic_summary:
        summary = trim_text(strip_markdown_heading(text), 240)
    graph = read_json(root / "30_state" / "story_graph.json", default={})
    entities, locations = extract_graph_entities_for_text(graph, text)
    events = extract_graph_events_for_chapter(graph, chapter_number, text, title)
    foreshadow_refs = extract_foreshadow_refs(graph, chapter_number, text)
    if isinstance(ledger, dict):
        retrieval = ledger.get("retrieval") if isinstance(ledger.get("retrieval"), dict) else {}
        foreshadow_refs = dedupe_lines(
            [
                *foreshadow_refs,
                *[
                    str(item.get("thread_id"))
                    for item in normalize_records(ledger.get("foreshadow_deltas"))
                    if str(item.get("thread_id") or "")
                ],
            ]
        )
    else:
        retrieval = {}
    return {
        "title": title,
        "summary": summary,
        "entities": entities,
        "entity_ids": normalize_strings(retrieval.get("entity_ids")),
        "events": events,
        "locations": locations,
        "foreshadow_refs": foreshadow_refs,
        "semantic_tags": normalize_strings(retrieval.get("tags")),
        "semantic_focus": normalize_strings(retrieval.get("focus")),
        "semantic_ledger": f"30_state/semantic_ledger/ch{chapter_number:03d}.json" if isinstance(ledger, dict) else "",
        "conflict_level": detect_conflict_level(text),
    }


def semantic_ledger_route_scores(
    root: Path,
    query_text: str,
    *,
    chapter_number: int | None,
) -> dict[int, float]:
    """Route a query to chapters from compact semantics before hydrating final chunks."""

    terms = extract_query_terms(query_text)
    lower_query = query_text.lower().strip()
    if not terms and not lower_query:
        return {}
    max_chapter = chapter_number - 1 if chapter_number and chapter_number > 1 else None
    scores: dict[int, float] = {}
    ledger_dir = root / "30_state" / "semantic_ledger"
    for path in sorted(ledger_dir.glob("ch*.json")) if ledger_dir.exists() else []:
        candidate_chapter = parse_chapter_number(path)
        if not candidate_chapter or (max_chapter is not None and candidate_chapter > max_chapter):
            continue
        payload = read_json(path, default={})
        if not isinstance(payload, dict) or payload.get("canonical") is not True:
            continue
        routing_payload = {
            "chapter_digest": payload.get("chapter_digest"),
            "events": payload.get("events"),
            "relationship_deltas": payload.get("relationship_deltas"),
            "character_deltas": payload.get("character_deltas"),
            "foreshadow_deltas": payload.get("foreshadow_deltas"),
            "world_deltas": payload.get("world_deltas"),
            "timeline_deltas": payload.get("timeline_deltas"),
            "retrieval": payload.get("retrieval"),
        }
        haystack = json.dumps(routing_payload, ensure_ascii=False).lower()
        exact = 1.0 if lower_query and lower_query in haystack else 0.0
        overlap = score_term_overlap(terms, haystack)
        score = exact * 1.5 + overlap
        if score > 0:
            scores[candidate_chapter] = score
    return dict(sorted(scores.items(), key=lambda item: (-item[1], -item[0])))


def read_summary_text(root: Path, chapter_number: int) -> str:
    summary_dir = root / "40_manuscript" / "summaries"
    for name in (f"ch{chapter_number:03d}.md", f"chapter_{chapter_number:03d}.md", f"{chapter_number}.md"):
        path = summary_dir / name
        if path.exists():
            return strip_markdown_heading(safe_read_text(path)).strip()
    return ""


def extract_graph_entities_for_text(graph: Any, text: str) -> tuple[list[str], list[str]]:
    if not isinstance(graph, dict):
        return [], []
    entities: list[str] = []
    locations: list[str] = []
    for entity in graph.get("entities", []):
        if not isinstance(entity, dict):
            continue
        labels = [str(entity.get("name") or ""), *normalize_strings(entity.get("aliases"))]
        if not any(label and label in text for label in labels):
            continue
        name = str(entity.get("name") or entity.get("id") or "").strip()
        if not name:
            continue
        entities.append(name)
        if str(entity.get("type") or "").lower() == "location":
            locations.append(name)
    return dedupe_values(entities), dedupe_values(locations)


def extract_graph_events_for_chapter(graph: Any, chapter_number: int, text: str, title: str) -> list[str]:
    events: list[str] = []
    if isinstance(graph, dict):
        for event in graph.get("events", []):
            if not isinstance(event, dict):
                continue
            event_chapter = event.get("chapter_number") or event.get("chapter")
            event_title = str(event.get("title") or event.get("name") or event.get("id") or "").strip()
            if event_chapter == chapter_number or (event_title and event_title in text):
                events.append(event_title)
    if not events and title:
        events.append(title)
    return dedupe_values(events)


def extract_foreshadow_refs(graph: Any, chapter_number: int, text: str) -> list[str]:
    refs: list[str] = []
    if isinstance(graph, dict):
        for entity in graph.get("entities", []):
            if not isinstance(entity, dict):
                continue
            if str(entity.get("type") or "").lower() != "foreshadowing":
                continue
            labels = [str(entity.get("name") or ""), *normalize_strings(entity.get("aliases"))]
            if any(label and label in text for label in labels):
                refs.append(str(entity.get("id") or entity.get("name") or "foreshadowing"))
    if re.search(r"(omen|prophecy|secret|clue|伏笔|预兆|预感|线索|秘密)", text, re.IGNORECASE):
        refs.append(f"detected:ch{chapter_number:03d}:foreshadow_signal")
    return dedupe_values(refs)


def detect_conflict_level(text: str) -> str:
    high_markers = ("决战", "爆发", "反杀", "真相", "危机", "collapse", "betrayal", "death")
    medium_markers = ("冲突", "选择", "阻力", "代价", "追击", "threat", "choice", "cost")
    high = sum(text.lower().count(marker.lower()) for marker in high_markers)
    medium = sum(text.lower().count(marker.lower()) for marker in medium_markers)
    if high >= 2:
        return "high"
    if high or medium >= 2:
        return "medium"
    return "low"


def split_text(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    """Split text by paragraph boundaries, then by sentence-ish boundaries when needed."""

    cleaned = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not cleaned:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", cleaned) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        pieces = split_long_paragraph(paragraph, max_chars=max_chars)
        for piece in pieces:
            if not current:
                current = piece
            elif len(current) + len(piece) + 2 <= max_chars:
                current = f"{current}\n\n{piece}"
            else:
                chunks.append(current)
                current = with_overlap(current, piece, overlap_chars)
    if current:
        chunks.append(current)
    return chunks


def split_long_paragraph(paragraph: str, *, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]
    sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;])", paragraph) if item.strip()]
    pieces: list[str] = []
    current = ""
    for sentence in sentences or [paragraph]:
        if len(sentence) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars))
        elif not current:
            current = sentence
        elif len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            pieces.append(current)
            current = sentence
    if current:
        pieces.append(current)
    return pieces


def with_overlap(previous: str, next_piece: str, overlap_chars: int) -> str:
    if overlap_chars <= 0:
        return next_piece
    overlap = previous[-overlap_chars:].strip()
    if not overlap:
        return next_piece
    return f"{overlap}\n\n{next_piece}"


def normalize_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [str(item) for item in value.values() if str(item).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def normalize_records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "records", "threads", "anchors", "data"):
            if isinstance(value.get(key), list):
                return value[key]
        return list(value.values())
    return []


def dedupe_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def dedupe_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        result.append(line)
    return result


def strip_markdown_heading(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#")).strip()


def extract_keywords(text: str, title: str = "") -> list[str]:
    terms = extract_query_terms(f"{title} {text}")
    filtered = [term for term in terms if len(term) >= 2]
    return sorted(filtered, key=lambda item: (-len(item), item))[:16]


def extract_query_terms(text: str) -> list[str]:
    lowered = text.lower()
    terms: set[str] = set()
    for token in re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{1,}", lowered):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) >= 2:
                terms.add(token)
            if len(token) > 2:
                for index in range(0, len(token) - 1):
                    terms.add(token[index : index + 2])
        else:
            terms.add(token)
    return sorted(terms)


def score_term_overlap(terms: list[str], haystack: str) -> float:
    if not terms:
        return 0.0
    matched = sum(1 for term in terms if term in haystack)
    return matched / len(terms)


def explain_reasons(
    exact: float,
    term_overlap: float,
    keyword_overlap: float,
    metadata_overlap: float,
    *,
    summary_overlap: float = 0.0,
    entity_overlap: float = 0.0,
    event_overlap: float = 0.0,
    location_overlap: float = 0.0,
    foreshadow_overlap: float = 0.0,
) -> list[str]:
    reasons = []
    if exact:
        reasons.append("exact query phrase")
    if term_overlap:
        reasons.append("text term overlap")
    if keyword_overlap:
        reasons.append("keyword overlap")
    if metadata_overlap:
        reasons.append("metadata overlap")
    if summary_overlap:
        reasons.append("chapter summary overlap")
    if entity_overlap:
        reasons.append("entity overlap")
    if event_overlap:
        reasons.append("event overlap")
    if location_overlap:
        reasons.append("location overlap")
    if foreshadow_overlap:
        reasons.append("foreshadow overlap")
    return reasons


def remove_stale_final_chunks(chunks_dir: Path, active_final_chapters: set[int]) -> None:
    for path in sorted(chunks_dir.glob("ch*.json")):
        chapter_number = parse_chapter_number(path)
        if chapter_number is not None and chapter_number not in active_final_chapters:
            path.unlink()


def final_chapter_numbers(root: Path) -> set[int]:
    final_dir = root / "40_manuscript" / "final"
    numbers = set()
    for path in sorted([*final_dir.glob("*.md"), *final_dir.glob("*.txt")]):
        number = parse_chapter_number(path)
        if number is not None:
            numbers.add(number)
    return numbers


def is_allowed_rag_source(config: ConfigDocument, source_path: str, metadata: dict[str, Any]) -> bool:
    normalized = source_path.replace("\\", "/")
    if metadata.get("canon") is True:
        return normalized == "10_bible/research_canon.jsonl"
    if not normalized.startswith("40_manuscript/final/"):
        return False
    root = resolve_project_root(config)
    return (root / normalized).exists()


def parse_chapter_number(path: Path) -> int | None:
    match = re.search(r"(?:ch|chapter[_-]?|第)?0*(\d{1,5})", path.stem, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")


def extract_title(text: str, path: Path) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.stem


def estimate_words(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def trim_text(text: str, max_chars: int) -> str:
    compact = text.strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def loads_json(value: str, *, default: Any) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def as_optional_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except json.JSONDecodeError:
        return default


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
