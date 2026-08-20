"""Character-expression contracts, chapter packets, and deterministic diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable

from longform_engine.storage import apply_transaction, atomic_write_text


CHARACTER_EXPRESSION_SCHEMA = "character_expression_profile_v1"
CHARACTER_REVIEW_SCHEMA = "character_expression_review_v1"
EXPRESSION_PROFILE_FIELDS = {
    "narrative_distance": {"close", "medium", "distant"},
    "expression_mode": {"externalized", "balanced", "interiorized"},
    "description_density": {"minimal", "selective", "rich"},
    "dialogue_mode": {"sparse", "balanced", "dialogue_forward"},
    "voice_separation": {"subtle", "clear", "heightened"},
    "ensemble_mode": {"protagonist", "dual", "ensemble"},
}
CHARACTER_CONTRACT_STRING_FIELDS = (
    "character_id",
    "perception_bias",
    "decision_bias",
    "speech_register",
    "physical_presence",
    "private_wants",
    "contradictions",
)
CHARACTER_CONTRACT_LIST_FIELDS = (
    "conversation_tactics",
    "emotional_leaks",
    "social_masks",
    "contrast_with",
)
CHARACTER_REVIEW_DIMENSIONS = (
    "voice_fit",
    "swapability",
    "character_as_function",
    "embodied_presence",
    "narrator_over_explains",
    "dialogue_as_exposition",
)


@dataclass(frozen=True)
class VoiceSampleApprovalResult:
    approved_by: str
    sample_count: int
    profile_file: str
    transaction_report: str


def approve_voice_samples(
    root: Path,
    *,
    file_path: str | Path,
    approved_by: str,
) -> VoiceSampleApprovalResult:
    """Approve exact final-manuscript spans as bounded voice references."""

    if approved_by != "human":
        raise ValueError("voice sample approval requires approved_by=human.")
    root = root.expanduser().resolve()
    approval_file = Path(file_path)
    if not approval_file.is_absolute():
        approval_file = root / approval_file
    approval_file = approval_file.expanduser().resolve()
    try:
        approval_file.relative_to(root)
    except ValueError as exc:
        raise ValueError("voice sample approval file must live under the project root.") from exc
    payload = read_json(approval_file, {})
    if not isinstance(payload, dict) or set(payload) != {"schema", "source_hashes", "samples"}:
        raise ValueError("voice sample approval must contain schema, source_hashes, and samples only.")
    if payload.get("schema") != "character_voice_sample_approval_v1":
        raise ValueError("schema must be character_voice_sample_approval_v1.")
    source_hashes = payload.get("source_hashes")
    samples = payload.get("samples")
    if not isinstance(source_hashes, dict) or not isinstance(samples, list) or not samples:
        raise ValueError("source_hashes and at least one sample are required.")

    profile_path = root / "10_bible" / "character_expression.json"
    profile = read_json(profile_path, {})
    errors = validate_character_expression_profile(
        profile,
        character_ids=[
            str(item.get("id"))
            for item in read_json(root / "10_bible" / "characters.json", [])
            if isinstance(item, dict) and item.get("id")
        ],
    )
    if errors:
        raise ValueError("character expression profile is invalid: " + "; ".join(errors))
    contracts = {
        str(item["character_id"]): item
        for item in profile["character_expression_contracts"]
        if isinstance(item, dict) and item.get("character_id")
    }
    approved_examples: list[tuple[str, dict[str, Any]]] = []
    expected_sample_fields = {"character_id", "source_path", "start", "end", "excerpt", "polarity", "note"}
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict) or set(sample) != expected_sample_fields:
            raise ValueError(f"samples[{index}] must contain exactly: {', '.join(sorted(expected_sample_fields))}.")
        character_id = str(sample.get("character_id") or "")
        if character_id not in contracts:
            raise ValueError(f"samples[{index}].character_id is not declared by the expression profile.")
        source_path = str(sample.get("source_path") or "")
        if not source_path.startswith("40_manuscript/final/"):
            raise ValueError(f"samples[{index}].source_path must be a finalized chapter.")
        source = (root / source_path).resolve()
        try:
            source.relative_to((root / "40_manuscript" / "final").resolve())
        except ValueError as exc:
            raise ValueError(f"samples[{index}].source_path escapes the final manuscript lane.") from exc
        if not source.is_file() or source_hashes.get(source_path) != sha256(source.read_bytes()).hexdigest():
            raise ValueError(f"samples[{index}] source hash is missing or stale.")
        text = source.read_text(encoding="utf-8").lstrip("\ufeff")
        start, end = sample.get("start"), sample.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end > len(text):
            raise ValueError(f"samples[{index}] span is outside the finalized chapter.")
        excerpt = str(sample.get("excerpt") or "")
        if text[start:end] != excerpt:
            raise ValueError(f"samples[{index}].excerpt does not match the finalized chapter span.")
        if sample.get("polarity") not in {"positive", "negative"}:
            raise ValueError(f"samples[{index}].polarity must be positive or negative.")
        note = str(sample.get("note") or "").strip()
        if not note:
            raise ValueError(f"samples[{index}].note must be non-empty.")
        approved_examples.append(
            (
                character_id,
                {
                    "polarity": sample["polarity"],
                    "text": excerpt,
                    "note": note,
                    "approved": True,
                },
            )
        )

    with apply_transaction(
        root,
        command="character samples-approve",
        source_paths=(approval_file,),
        touched_paths=(profile_path,),
        metadata={"approved_by": approved_by, "sample_count": len(approved_examples)},
    ) as transaction:
        for character_id, example in approved_examples:
            examples = contracts[character_id]["voice_examples"]
            if not any(
                isinstance(current, dict)
                and current.get("polarity") == example["polarity"]
                and current.get("text") == example["text"]
                for current in examples
            ):
                examples.append(example)
        write_character_expression_profile(root, profile)
    return VoiceSampleApprovalResult(
        approved_by=approved_by,
        sample_count=len(approved_examples),
        profile_file=profile_path.relative_to(root).as_posix(),
        transaction_report=transaction.report_file.relative_to(root).as_posix(),
    )


def validate_character_expression_profile(
    payload: Any,
    *,
    character_ids: Iterable[str] = (),
) -> list[str]:
    """Validate the canonical project-level expression contract."""

    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["character expression profile must be a JSON object."]
    allowed = {"schema", "narrative_expression_profile", "character_expression_contracts"}
    missing = allowed - set(payload)
    extra = set(payload) - allowed
    if missing:
        errors.append("missing fields: " + ", ".join(sorted(missing)))
    if extra:
        errors.append("unknown fields: " + ", ".join(sorted(extra)))
    if payload.get("schema") != CHARACTER_EXPRESSION_SCHEMA:
        errors.append(f"schema must be {CHARACTER_EXPRESSION_SCHEMA}.")

    profile = payload.get("narrative_expression_profile")
    if not isinstance(profile, dict):
        errors.append("narrative_expression_profile must be an object.")
    else:
        if set(profile) != set(EXPRESSION_PROFILE_FIELDS):
            errors.append(
                "narrative_expression_profile must contain exactly: "
                + ", ".join(sorted(EXPRESSION_PROFILE_FIELDS))
                + "."
            )
        for field, values in EXPRESSION_PROFILE_FIELDS.items():
            if profile.get(field) not in values:
                errors.append(f"narrative_expression_profile.{field} must be one of: {', '.join(sorted(values))}.")

    known_ids = {str(item) for item in character_ids if str(item).strip()}
    contracts = payload.get("character_expression_contracts")
    if not isinstance(contracts, list) or not contracts:
        errors.append("character_expression_contracts must contain at least one character contract.")
        return errors
    seen: set[str] = set()
    allowed_contract = {
        *CHARACTER_CONTRACT_STRING_FIELDS,
        *CHARACTER_CONTRACT_LIST_FIELDS,
        "voice_examples",
    }
    for index, contract in enumerate(contracts):
        prefix = f"character_expression_contracts[{index}]"
        if not isinstance(contract, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        missing_contract = allowed_contract - set(contract)
        extra_contract = set(contract) - allowed_contract
        if missing_contract:
            errors.append(f"{prefix} missing fields: {', '.join(sorted(missing_contract))}.")
        if extra_contract:
            errors.append(f"{prefix} has unknown fields: {', '.join(sorted(extra_contract))}.")
        character_id = str(contract.get("character_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{1,79}", character_id):
            errors.append(f"{prefix}.character_id must be a stable id.")
        elif character_id in seen:
            errors.append(f"{prefix}.character_id is duplicated: {character_id}.")
        else:
            seen.add(character_id)
        if known_ids and character_id not in known_ids:
            errors.append(f"{prefix}.character_id must reference 10_bible/characters.json.")
        for field in CHARACTER_CONTRACT_STRING_FIELDS[1:]:
            if not isinstance(contract.get(field), str) or not str(contract.get(field)).strip():
                errors.append(f"{prefix}.{field} must be a non-empty string.")
        for field in CHARACTER_CONTRACT_LIST_FIELDS:
            values = contract.get(field)
            if not isinstance(values, list) or not values or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                errors.append(f"{prefix}.{field} must be a non-empty string list.")
        examples = contract.get("voice_examples")
        if not isinstance(examples, list):
            errors.append(f"{prefix}.voice_examples must be a list.")
        else:
            for example_index, example in enumerate(examples):
                example_prefix = f"{prefix}.voice_examples[{example_index}]"
                if not isinstance(example, dict) or set(example) != {"polarity", "text", "note", "approved"}:
                    errors.append(f"{example_prefix} must contain polarity, text, note, and approved only.")
                    continue
                if example.get("polarity") not in {"positive", "negative"}:
                    errors.append(f"{example_prefix}.polarity must be positive or negative.")
                if not isinstance(example.get("text"), str) or not example["text"].strip():
                    errors.append(f"{example_prefix}.text must be a non-empty string.")
                if not isinstance(example.get("note"), str) or not example["note"].strip():
                    errors.append(f"{example_prefix}.note must be a non-empty string.")
                if not isinstance(example.get("approved"), bool):
                    errors.append(f"{example_prefix}.approved must be boolean.")
    missing_characters = sorted(known_ids - seen)
    if missing_characters:
        errors.append("character expression contracts do not cover: " + ", ".join(missing_characters) + ".")
    return errors


def character_expression_readiness(root: Path) -> tuple[bool, list[str]]:
    path = root / "10_bible" / "character_expression.json"
    payload = read_json(path, {})
    characters = read_json(root / "10_bible" / "characters.json", [])
    character_ids = [
        str(item.get("id"))
        for item in characters
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ] if isinstance(characters, list) else []
    errors = validate_character_expression_profile(payload, character_ids=character_ids)
    if not path.is_file():
        errors.insert(0, "10_bible/character_expression.json has not been explicitly applied.")
    return not errors, errors


def write_character_expression_profile(root: Path, payload: dict[str, Any]) -> Path:
    path = root / "10_bible" / "character_expression.json"
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def build_character_expression_packet(
    root: Path,
    *,
    chapter_number: int,
    card: dict[str, Any],
    tcs: dict[str, Any],
    persist: bool = False,
) -> dict[str, Any]:
    """Compile the smallest character-specific packet needed by a chapter writer."""

    characters = read_json(root / "10_bible" / "characters.json", [])
    relationships = read_json(root / "10_bible" / "relationships.json", [])
    expression = read_json(root / "10_bible" / "character_expression.json", {})
    character_rows = [item for item in characters if isinstance(item, dict)] if isinstance(characters, list) else []
    by_id = {str(item.get("id")): item for item in character_rows if item.get("id")}
    requested = [str(item) for item in card.get("featured_character_ids") or [] if str(item).strip()]
    pov = str(card.get("pov_character_id") or "").strip()
    if pov:
        requested.insert(0, pov)
    # TCS contains the current state of historical characters, not the cast contract
    # for this chapter. Only the chapter card can promote a character into the
    # full expression packet; background state remains available through RAG/TCS.
    if not requested and character_rows:
        requested.append(str(character_rows[0].get("id") or ""))
    featured = dedupe(item for item in requested if item in by_id)
    if len(featured) > 6:
        raise ValueError(
            "Character expression packet cannot fit all required featured characters: "
            f"{', '.join(featured)}; split the scene or revise the chapter card before regenerating."
        )
    if not pov and featured:
        pov = featured[0]

    contracts = expression.get("character_expression_contracts") if isinstance(expression, dict) else []
    contract_by_id = {
        str(item.get("character_id")): item
        for item in contracts or []
        if isinstance(item, dict) and item.get("character_id")
    }
    scene_wants = card.get("scene_wants") if isinstance(card.get("scene_wants"), dict) else {}
    voice_state = card.get("voice_state") if isinstance(card.get("voice_state"), dict) else {}
    memory_rows = [
        payload
        for path in sorted((root / "60_rag" / "memory" / "characters").glob("*.json"))
        if isinstance((payload := read_json(path, {})), dict)
        and str(payload.get("character_id") or payload.get("id") or "").strip()
    ]
    memory_by_id = {
        str(item.get("character_id") or item.get("id")): item
        for item in memory_rows
        if isinstance(item, dict)
    }
    selected_contracts: list[dict[str, Any]] = []
    approved_samples: list[dict[str, Any]] = []
    for character_id in featured:
        character = by_id[character_id]
        contract = contract_by_id.get(character_id, {})
        approved = [
            example
            for example in contract.get("voice_examples") or []
            if isinstance(example, dict) and example.get("approved") is True
        ]
        for example in approved:
            if len(approved_samples) >= 2:
                break
            approved_samples.append({"character_id": character_id, **example})
        selected_contracts.append(
            {
                "character_id": character_id,
                "name": character.get("name", ""),
                "chapter_scene_want": str(scene_wants.get(character_id) or character.get("goal") or ""),
                "private_pressure": str(
                    (memory_by_id.get(character_id) or {}).get("current_pressure")
                    or contract.get("private_wants")
                    or character.get("flaw")
                    or ""
                ),
                "voice_state": str(voice_state.get(character_id) or "baseline"),
                "allowed_change": str((memory_by_id.get(character_id) or {}).get("allowed_voice_change") or "none unless caused in-scene"),
                "perception_bias": contract.get("perception_bias", ""),
                "decision_bias": contract.get("decision_bias", ""),
                "speech_register": contract.get("speech_register", ""),
                "conversation_tactics": list(contract.get("conversation_tactics") or []),
                "emotional_leaks": list(contract.get("emotional_leaks") or []),
                "physical_presence": contract.get("physical_presence", ""),
                "social_masks": list(contract.get("social_masks") or []),
                "contradictions": contract.get("contradictions", ""),
                "relationship_context": relationship_context(relationships, character_id, featured),
            }
        )
    packet = {
        "schema": "character_expression_packet_v1",
        "chapter_number": chapter_number,
        "pov_character_id": pov,
        "featured_character_ids": featured,
        "narrative_expression_profile": (
            expression.get("narrative_expression_profile", {}) if isinstance(expression, dict) else {}
        ),
        "characterization_focus": list(card.get("characterization_focus") or []),
        "relationship_move": str(card.get("relationship_move") or card.get("relationship_impact") or ""),
        "embodiment_strategy": str(
            card.get("embodiment_strategy")
            or "Use selective action, perception, body response, or subtext; do not append a generic appearance paragraph."
        ),
        "summary_scene_policy": str(
            card.get("summary_scene_policy")
            or "Summarize transitions; dramatize irreversible choices, relationship changes, and paid costs."
        ),
        "contracts": selected_contracts,
        "approved_voice_samples": approved_samples,
        "avoid_repetition": dedupe(
            str(item)
            for contract in selected_contracts
            for item in contract.get("emotional_leaks") or []
        )[:6],
        "source": "10_bible/character_expression.json",
    }
    if persist:
        path = root / "50_workbench" / "character_packets" / f"ch{chapter_number:03d}.json"
        atomic_write_text(path, json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
    return packet


def character_expression_diagnostics(text: str, *, character_names: Iterable[str] = ()) -> dict[str, Any]:
    """Return descriptive scene and dialogue evidence without enforcing literary quotas."""

    compact_chars = len(re.sub(r"\s+", "", text)) or 1
    utterances = extract_dialogue_utterances(text, character_names=character_names)
    attributed = [item for item in utterances if item["speaker"] != "unknown"]
    speakers: dict[str, list[dict[str, Any]]] = {}
    for item in attributed:
        speakers.setdefault(str(item["speaker"]), []).append(item)
    speaker_profiles = {
        speaker: dialogue_speaker_profile(items)
        for speaker, items in sorted(speakers.items())
    }
    body_terms = ("手", "指", "肩", "背", "眼", "眉", "呼吸", "喉", "脚", "膝", "颈", "脸", "掌心", "步")
    interior_terms = ("想", "意识到", "明白", "犹豫", "记得", "害怕", "希望", "怀疑", "不愿", "宁可", "后悔")
    explanation_terms = ("这意味着", "也就是说", "原因是", "显然", "由此可见", "不难看出", "他知道自己", "她知道自己")
    quoted_chars = sum(len(str(item["text"])) for item in utterances)
    quote_marks = sum(text.count(mark) for mark in ('"', "'", "“", "”", "‘", "’"))
    exposition_dialogue = [
        item for item in utterances
        if len(str(item["text"])) >= 18
        and any(token in str(item["text"]) for token in ("因为", "所以", "这意味着", "也就是说", "根据", "证据", "规定", "换句话说"))
    ]
    swapability = dialogue_swapability_risk(speaker_profiles)
    evidence_status = (
        "unknown"
        if not utterances
        else "insufficient_evidence"
        if len(speaker_profiles) < 2
        else "observed"
    )
    risks: list[dict[str, Any]] = []
    if len(speaker_profiles) >= 2 and swapability >= 0.72:
        risks.append(
            {
                "code": "dialogue_swapability_risk",
                "severity": "P2",
                "message": "attributed speakers have unusually similar sentence and speech-act profiles",
                "score": swapability,
            }
        )
    if len(utterances) >= 4 and len(exposition_dialogue) / len(utterances) >= 0.5:
        risks.append(
            {
                "code": "dialogue_as_exposition_risk",
                "severity": "P2",
                "message": "at least half of dialogue segments explain facts directly instead of applying social pressure",
                "ratio": round(len(exposition_dialogue) / len(utterances), 4),
            }
        )
    return {
        "schema": "character_expression_diagnostics_v1",
        "dialogue_mark_density": round(quote_marks / compact_chars, 4),
        "dialogue_char_ratio": round(quoted_chars / compact_chars, 4),
        "dialogue_segments": len(utterances),
        "attributed_dialogue_segments": len(attributed),
        "attribution_coverage": round(len(attributed) / max(1, len(utterances)), 4),
        "speaker_profiles": speaker_profiles,
        "swapability_evidence_status": evidence_status,
        "swapability_risk": swapability if evidence_status == "observed" else None,
        "dialogue_exposition_ratio": round(len(exposition_dialogue) / max(1, len(utterances)), 4),
        "embodiment_term_density": round(sum(text.count(term) for term in body_terms) / compact_chars, 4),
        "interiority_term_density": round(sum(text.count(term) for term in interior_terms) / compact_chars, 4),
        "narrator_explanation_hits": sum(text.count(term) for term in explanation_terms),
        "risks": risks,
        "quota_policy": "diagnostic_only; active story facets and scene intent decide acceptable density",
    }


def extract_dialogue_utterances(text: str, *, character_names: Iterable[str]) -> list[dict[str, Any]]:
    names = sorted({str(name) for name in character_names if str(name).strip()}, key=len, reverse=True)
    pattern = re.compile(
        r"[\u201c\"\u300c]([^\u201d\"\u300d\n]{1,500})[\u201d\"\u300d]"
        r"|[\u2018']([^\u2019'\n]{1,500})[\u2019']"
    )
    utterances: list[dict[str, Any]] = []
    speech_verbs = ("说", "问", "道", "答", "喊", "提醒", "反驳", "低声", "冷声", "笑", "开口", "喝道")
    for match in pattern.finditer(text):
        utterance = match.group(1) or match.group(2) or ""
        before = text[max(0, match.start() - 48):match.start()]
        after = text[match.end():min(len(text), match.end() + 32)]
        speaker = "unknown"
        candidates: list[tuple[int, str]] = []
        for name in names:
            before_index = before.rfind(name)
            if before_index >= 0 and any(verb in before[before_index:] for verb in speech_verbs):
                candidates.append((len(before) - before_index, name))
            after_index = after.find(name)
            if after_index >= 0 and any(verb in after[: after_index + len(name) + 4] for verb in speech_verbs):
                candidates.append((48 + after_index, name))
        if candidates:
            speaker = min(candidates, key=lambda item: item[0])[1]
        utterances.append(
            {
                "speaker": speaker,
                "text": utterance.strip(),
                "start": match.start(),
                "end": match.end(),
            }
        )
    return utterances


def dialogue_speaker_profile(items: list[dict[str, Any]]) -> dict[str, Any]:
    texts = [str(item.get("text") or "") for item in items]
    lengths = [len(re.sub(r"\s+", "", text)) for text in texts]
    question = sum(1 for text in texts if "?" in text or "？" in text)
    imperative = sum(1 for text in texts if any(token in text for token in ("别", "快", "立刻", "必须", "给我", "请", "住手")))
    correction = sum(1 for text in texts if any(token in text for token in ("不对", "不是", "错了", "应该", "等等", "慢着")))
    terms = re.findall(r"[\u4e00-\u9fff]{2,4}|[A-Za-z]{3,}", " ".join(texts))
    frequencies: dict[str, int] = {}
    for term in terms:
        frequencies[term] = frequencies.get(term, 0) + 1
    top_terms = [item[0] for item in sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))[:8]]
    return {
        "utterance_count": len(texts),
        "avg_utterance_chars": round(sum(lengths) / max(1, len(lengths)), 2),
        "question_ratio": round(question / max(1, len(texts)), 4),
        "imperative_ratio": round(imperative / max(1, len(texts)), 4),
        "correction_ratio": round(correction / max(1, len(texts)), 4),
        "domain_lexicon": top_terms,
    }


def dialogue_swapability_risk(profiles: dict[str, dict[str, Any]]) -> float:
    eligible = [profile for profile in profiles.values() if int(profile.get("utterance_count") or 0) >= 2]
    if len(eligible) < 2:
        return 0.0
    similarities: list[float] = []
    for left_index, left in enumerate(eligible):
        for right in eligible[left_index + 1:]:
            length_similarity = 1 - min(
                1.0,
                abs(float(left["avg_utterance_chars"]) - float(right["avg_utterance_chars"]))
                / max(1.0, float(left["avg_utterance_chars"]), float(right["avg_utterance_chars"])),
            )
            acts = (
                1 - abs(float(left["question_ratio"]) - float(right["question_ratio"])),
                1 - abs(float(left["imperative_ratio"]) - float(right["imperative_ratio"])),
                1 - abs(float(left["correction_ratio"]) - float(right["correction_ratio"])),
            )
            left_terms, right_terms = set(left["domain_lexicon"]), set(right["domain_lexicon"])
            lexical_similarity = len(left_terms & right_terms) / max(1, len(left_terms | right_terms))
            similarities.append((length_similarity + sum(acts) + lexical_similarity) / 5)
    return round(sum(similarities) / max(1, len(similarities)), 4)


def relationship_context(relationships: Any, character_id: str, featured: list[str]) -> list[dict[str, str]]:
    if not isinstance(relationships, list):
        return []
    selected: list[dict[str, str]] = []
    featured_ids = set(featured)
    for relation in relationships:
        if not isinstance(relation, dict):
            continue
        source, target = str(relation.get("source_id") or ""), str(relation.get("target_id") or "")
        if character_id not in {source, target} or not {source, target}.issubset(featured_ids):
            continue
        selected.append(
            {
                "relationship_id": str(relation.get("id") or ""),
                "other_character_id": target if source == character_id else source,
                "type": str(relation.get("type") or ""),
                "stage": str(relation.get("stage") or ""),
            }
        )
    if len(selected) > 4:
        relationship_ids = ", ".join(item["relationship_id"] or "<unnamed>" for item in selected)
        raise ValueError(
            "Character expression packet cannot fit every active featured relationship for "
            f"{character_id}: {relationship_ids}; narrow the featured cast or relationship focus."
        )
    return selected


def dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = str(value).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
