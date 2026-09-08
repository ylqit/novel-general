"""Local, anonymous literary trials with closed-chapter evidence and independent ratings.

This is an evaluation workflow. It never creates novels or approves canonical prose.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
from pathlib import Path
import secrets
import shutil
from statistics import median
import tempfile
from typing import Any
import zipfile

from longform_engine import __version__
from longform_engine.artifacts import read_chapter_audit_artifact
from longform_engine.blind_review import clean_identifier, payload_sha256
from longform_engine.config import ConfigDocument, load_project_config
from longform_engine.storage import atomic_write_text, resolve_project_root

TRIAL_SCHEMA = "literary_trial_v3"
REVIEW_SCHEMA = "literary_review_v3"
TRIAL_DIRECTORY = "70_runtime/literary_trials"
STAGES = {"opening": 3, "sustained": 10, "formal": 20, "crossover": None, "rehearsal": None}
METRICS = {
    "prose_naturalness": "语言自然度",
    "character_voice": "人物声音",
    "character_agency": "人物自主性",
    "scene_causality": "场景因果",
    "chapter_reading_value": "章节阅读价值",
    "continued_reading_desire": "追读意愿",
    "canon_fidelity": "批准原著基线忠实度",
    "canon_recognition": "人物与原著辨识度",
    "new_reading_value": "本作新增阅读价值",
    "divergence_causality": "分歧因果",
    "in_world_cost_and_countermeasure": "跨体系代价与反制",
}
CORE_METRICS = {
    "prose_naturalness", "character_agency", "continued_reading_desire",
    "canon_fidelity", "new_reading_value",
}


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def _write_object(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def collect_literary_sample(config: ConfigDocument, start: int, end: int, *, rehearsal: bool = False) -> dict[str, Any]:
    """Verify actual closure, final, human acceptance, reviews and provenance."""
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= start + 99:
        raise ValueError("sample range must contain 1–100 consecutive chapters")
    root = resolve_project_root(config)
    from longform_engine.execution_origin import execution_origin
    origin = execution_origin(root)
    if rehearsal and origin["kind"] != "automated_rehearsal":
        raise ValueError("评测流程演练只接受持久标记的自动演练项目")
    if origin["simulated_human"] and not rehearsal:
        raise ValueError("automated_rehearsal_ineligible: 模拟人工演练不能作为真实人工文学验收样本")
    if config.path is None:
        raise ValueError("literary sample requires a persisted project config")
    bindings: list[dict[str, Any]] = []

    def evidence(chapter: int, relative: str, expected: str | None = None, *,
                 optional: bool = False, expected_text_sha256: str | None = None) -> dict[str, Any]:
        try:
            body = read_chapter_audit_artifact(root, chapter, relative)
        except FileNotFoundError:
            if optional:
                return {}
            raise
        digest = sha256(body).hexdigest()
        if expected is not None and digest != expected:
            raise ValueError(f"stale literary evidence: {relative}")
        if expected_text_sha256 is not None:
            # Pacing's existing application contract hashes read_text() output.
            # Preserve that contract while binding the actual archived bytes too.
            normalized = body.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            if sha256(normalized.encode("utf-8")).hexdigest() != expected_text_sha256:
                raise ValueError(f"stale literary evidence: {relative}")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError(f"invalid literary evidence: {relative}")
        bindings.append({"chapter_number": chapter, "path": relative, "sha256": digest})
        return value

    from longform_engine.chapter_contract import chapter_contract_hash
    from longform_engine.planning.contracts import validate_volume_skeletons
    skeletons = evidence(start, "20_outline/volume_skeletons.json")
    skeleton_errors: list[str] = []
    validate_volume_skeletons(skeletons, skeleton_errors)
    if skeleton_errors:
        raise ValueError("literary volume ranges invalid: " + ";".join(skeleton_errors))
    chapters: list[dict[str, Any]] = []
    for chapter in range(start, end + 1):
        final_path = f"40_manuscript/final/ch{chapter:03d}.md"
        body = (root / final_path).read_bytes()
        digest = sha256(body).hexdigest()
        closure = evidence(chapter, f"30_state/chapter_closures/ch{chapter:03d}.json")
        if (closure.get("schema") != "chapter_closure_v2" or closure.get("chapter_number") != chapter
                or closure.get("approved_by") != "human" or closure.get("final_sha256") != digest):
            raise ValueError(f"chapter {chapter} has no current human-approved closure")
        ledger = evidence(chapter, f"30_state/semantic_ledger/ch{chapter:03d}.json", closure.get("semantic_ledger_sha256", ""))
        if (ledger.get("schema") != "chapter_semantic_bundle_v1" or ledger.get("canonical") is not True
                or ledger.get("source", {}).get("sha256") != digest):
            raise ValueError(f"chapter {chapter} semantic evidence does not bind final")
        finalization = evidence(chapter, f"40_manuscript/final/ch{chapter:03d}.finalization.json")
        if (finalization.get("final_sha256") != digest or finalization.get("approved_by") != "human"
                or finalization.get("gate_passed") is not True or finalization.get("gate_waived") is True):
            raise ValueError(f"chapter {chapter} needs a passed, unwaived production gate")
        gate = evidence(chapter, str(finalization.get("gate_result") or ""))
        if gate.get("passed") is not True or gate.get("source_sha256") != digest or gate.get("failures"):
            raise ValueError(f"chapter {chapter} gate evidence is not current")
        review = finalization.get("human_story_review", {})
        revision = finalization.get("human_author_revision", {})
        if (review.get("schema") != "human_story_review_finalization_binding_v4"
                or revision.get("schema") != "human_author_revision_finalization_binding_v4"
                or review.get("candidate_sha256") != digest or revision.get("revision_candidate_sha256") != digest):
            raise ValueError(f"chapter {chapter} requires current human revision and acceptance")
        decision = evidence(chapter, review.get("decision_file", ""), review.get("decision_sha256", ""))
        if (decision.get("schema") != "human_story_review_v7" or decision.get("decision") != "accept"
                or decision.get("approved_by") != "human" or decision.get("candidate_sha256") != digest):
            raise ValueError(f"chapter {chapter} human acceptance is invalid")
        evidence(chapter, f"20_outline/chapter_intents/ch{chapter:03d}.json", review.get("human_chapter_intent_sha256", ""))
        evidence(chapter, f"20_outline/plot_nodes/ch{chapter:03d}.json", review.get("plot_node_table_sha256", ""))
        basis = evidence(chapter, f"50_workbench/writing_tasks/ch{chapter:03d}.basis.json")
        if (basis.get("schema") != "chapter_story_brief_basis_v4"
                or basis.get("basis_sha256") != review.get("story_brief_basis_sha256")
                or payload_sha256({k: v for k, v in basis.items() if k != "basis_sha256"}) != basis.get("basis_sha256")):
            raise ValueError(f"chapter {chapter} author brief basis is stale")
        for file_field, hash_field in (("record_file", "record_sha256"), ("validation_file", "validation_sha256"),
                                      ("final_lock_file", "final_lock_sha256")):
            evidence(chapter, revision.get(file_field, ""), revision.get(hash_field, ""))
        bundle_hash = review.get("review_bundle_sha256", "")
        bundle = evidence(chapter, "50_workbench/human_story_reviews/bundles/"
                          f"ch{chapter:03d}.{digest[:12]}.{bundle_hash[:12]}.review_bundle.json", bundle_hash)
        if (bundle.get("schema") != "human_review_bundle_v2" or bundle.get("candidate_sha256") != digest
                or bundle.get("blocking_finding_ids")
                or not set(bundle.get("required_reviews", [])) <= set(bundle.get("completed_reviews", []))):
            raise ValueError(f"chapter {chapter} independent review barrier is incomplete")
        contract = evidence(chapter, f"20_outline/chapter_contracts/ch{chapter:03d}.json")
        contract_digest = chapter_contract_hash({k: v for k, v in contract.items() if k != "chapter_contract_hash"})
        if contract.get("chapter_contract_hash") != contract_digest or review.get("chapter_contract_sha256") != contract_digest:
            raise ValueError(f"chapter {chapter} reviewed contract is stale")
        volumes = [v for v in skeletons["items"] if v["chapter_range"][0] <= chapter <= v["chapter_range"][1]]
        if len(volumes) != 1:
            raise ValueError(f"chapter {chapter} has no unique approved volume")
        for name, stage in bundle.get("review_stages", {}).items():
            if stage.get("required") and (not stage.get("complete") or stage.get("need_human")):
                raise ValueError(f"chapter {chapter} required {name} review is incomplete")
        for stage_name, prefix in (("semantic", "semantic_review"), ("pacing", "semantic_pacing")):
            if not bundle.get("review_stages", {}).get(stage_name, {}).get("required"):
                continue
            directory = f"50_workbench/gate_artifacts/ch{chapter:03d}"
            validation = evidence(chapter, f"{directory}/{prefix}_validation.json")
            result_path = f"{directory}/{prefix}_result.json"
            if stage_name == "semantic":
                application = evidence(chapter, f"{directory}/semantic_review_application.json")
                result = evidence(chapter, result_path, expected_text_sha256=application.get("result_sha256", ""))
                review_context = evidence(chapter, f"{directory}/semantic_review_context.json",
                                          expected_text_sha256=application.get("context_sha256", ""))
                bound = (application.get("schema") == "semantic_review_application_v2"
                         and application.get("source_hash") == digest
                         and application.get("result_file") == result_path
                         and application.get("payload") == result)
            else:
                applied = gate.get("semantic_pacing", {})
                review_context = evidence(chapter, f"{directory}/semantic_pacing_task.json",
                                          expected_text_sha256=applied.get("context_sha256", ""))
                result = evidence(chapter, result_path, expected_text_sha256=applied.get("result_sha256", ""))
                bound = (applied.get("source_sha256") == digest
                         and validation.get("provenance", {}).get("source_sha256") == digest)
            bound = bound and review_context.get("story_brief_binding", {}).get("story_brief_basis_sha256") == review.get("story_brief_basis_sha256")
            if (not bound or validation.get("ok") is not True or validation.get("subject") != result_path
                    or result.get("schema") != "evidence_review_v2"
                    or result.get("verdict") not in {"pass", "conditional_pass"}
                    or any(str(f.get("severity", "")).upper() in {"P0", "P1"} for f in result.get("findings", []))):
                raise ValueError(f"chapter {chapter} {stage_name} review evidence is stale or invalid")
        editorial = evidence(chapter, f"50_workbench/editorial_reviews/ch{chapter:03d}.aggregate.json")
        if (editorial.get("source_sha256") != digest or editorial.get("need_human")
                or not editorial.get("expected_roles")
                or set(editorial["expected_roles"]) != set(editorial.get("accepted_roles", []))):
            raise ValueError(f"chapter {chapter} editorial aggregate is incomplete or stale")
        accepted_roles = []
        for result_path in editorial.get("accepted_results", []):
            validation = evidence(chapter, result_path.removesuffix(".json") + ".validation.json")
            normalized = validation.get("provenance", {}).get("normalized", {})
            result = evidence(chapter, result_path, normalized.get("source_result_sha256", ""))
            if validation.get("ok") is not True or result.get("verdict") not in {"pass", "conditional_pass"}:
                raise ValueError(f"chapter {chapter} independent review validation failed")
            role = validation.get("provenance", {}).get("role_id", "")
            context = evidence(chapter, f"50_workbench/editorial_reviews/agent_tasks/ch{chapter:03d}/{role}.context.json")
            normalized = validation.get("provenance", {}).get("normalized", {})
            if (context.get("chapter_contract_hash") != contract_digest
                    or context.get("context_digest_hash") != normalized.get("context_digest_hash")
                    or context.get("story_brief_binding", {}).get("story_brief_basis_sha256") != review.get("story_brief_basis_sha256")
                    or context.get("role_id") != role or role in accepted_roles
                    or any(str(f.get("severity", "")).upper() in {"P0", "P1"} for f in result.get("findings", []))):
                raise ValueError(f"chapter {chapter} independent review context binding is invalid")
            accepted_roles.append(role)
        if set(accepted_roles) != set(editorial["expected_roles"]):
            raise ValueError(f"chapter {chapter} independent review results are missing")
        if bundle.get("review_stages", {}).get("payoff", {}).get("required"):
            validation = evidence(chapter, f"50_workbench/quality_reviews/ch{chapter:03d}.reader_payoff.validation.json")
            provenance = validation.get("provenance", {})
            if validation.get("ok") is not True or provenance.get("source_hash") != digest or not provenance.get("passed"):
                raise ValueError(f"chapter {chapter} reader-value review is stale")
            evidence(chapter, str(validation.get("subject", "")), provenance.get("review_hash", ""))
        writing = evidence(chapter, f"50_workbench/writing_tasks/ch{chapter:03d}.json", optional=True)
        attempts = evidence(chapter, f"50_workbench/repair_plans/ch{chapter:03d}/attempts.json", optional=True)
        if attempts and (attempts.get("schema") != "repair_attempts_v1" or not isinstance(attempts.get("submitted_rounds"), list)):
            raise ValueError(f"chapter {chapter} repair effort evidence is invalid")
        process = {
            "context_estimated_units": writing.get("context_plan", {}).get("estimated_units"),
            "repair_attempts": len(attempts["submitted_rounds"]) if attempts else None,
            "blocking_findings_observed": None,
        }
        reading_basis: dict[str, Any] = {}
        if config.data.get("creation", {}).get("mode") == "fanfiction":
            context = evidence(chapter, f"50_workbench/fanfiction_context/ch{chapter:03d}.json",
                               closure.get("fanfiction_context_sha256", ""))
            if context.get("schema") != "fanfiction_context_bundle_v3":
                raise ValueError("literary sample requires the current fanfiction context protocol")
            reading_basis = context.get("author_projection", {})
        chapters.append({"chapter_number": chapter, "path": final_path, "sha256": digest,
                         "character_count": len(body.decode("utf-8")), "volume_id": volumes[0]["volume_id"], "reading_basis": reading_basis, "process_observations": process})

    mode = config.data.get("creation", {}).get("mode", "original")
    continuity = config.data.get("fanfiction", {}).get("continuity_mode")
    route_family = None
    topology = None
    baseline: list[dict[str, Any]] = []
    if mode == "fanfiction":
        from longform_engine.fanfiction_contracts import load_current_fanfiction_documents
        documents = load_current_fanfiction_documents(config, root)
        route_family = documents.story_engine["extensions"]["route_family"]
        continuity = documents.story_engine["extensions"]["continuity_mode"]
        topology = documents.route.get("extensions", {}).get("crossover", {}).get("topology")
        for path in documents.paths.values():
            baseline.append({"path": path.relative_to(root).as_posix(), "sha256": sha256(path.read_bytes()).hexdigest()})
    applicable = list(METRICS)[:6]
    if mode == "fanfiction":
        applicable += ["canon_fidelity", "canon_recognition", "new_reading_value"]
        from longform_engine.fanfiction_creative_requirements import compile_fanfiction_creative_requirements
        requirements = compile_fanfiction_creative_requirements(continuity, route_family)
        if requirements["conditional_metrics"]["divergence_causality"]:
            applicable.append("divergence_causality")
        if requirements["conditional_metrics"]["cross_system_cost_and_counterplay"]:
            applicable.append("in_world_cost_and_countermeasure")
    return {
        "config_path": str(config.path.resolve()), "project_root": str(root),
        "config_sha256": payload_sha256(config.data),
        "config_file_sha256": sha256(config.path.read_bytes()).hexdigest(),
        "creation_mode": mode, "continuity_mode": continuity, "route_family": route_family,
        "execution_origin": origin,
        "topology": topology, "chapter_start": start, "chapter_end": end, "chapters": chapters,
        "baseline": baseline, "evidence": bindings, "applicable_metrics": applicable,
        "workflow": {"writing_mode": config.data.get("writing", {}).get("mode"),
                     "model": None, "model_provider": None, "human_review_minutes": None,
                     "human_edit_minutes": None},
    }


def create_literary_trial(config: ConfigDocument, *, trial_id: str, stage: str,
                          samples: list[dict[str, Any]]) -> dict[str, Any]:
    trial_id = clean_identifier(trial_id, field="trial_id")
    if ".." in trial_id:
        raise ValueError("trial_id cannot contain consecutive dots")
    if stage not in STAGES or not isinstance(samples, list) or not 1 <= len(samples) <= 12:
        raise ValueError("choose a valid trial stage and 1–12 source projects")
    root = resolve_project_root(config)
    from longform_engine.execution_origin import execution_origin
    owner_origin = execution_origin(root)
    if stage == "rehearsal" and owner_origin["kind"] != "automated_rehearsal":
        raise ValueError("请在独立自动演练项目中验证评测流程")
    if stage != "rehearsal" and owner_origin["simulated_human"]:
        raise ValueError("automated_rehearsal_ineligible: 模拟人工演练不能组织真实人工文学验收")
    target = root / TRIAL_DIRECTORY / trial_id
    if target.exists():
        raise ValueError("trial already exists; use a new trial ID")
    records = []
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {"config_path", "chapter_start", "chapter_end"}:
            raise ValueError("sample fields must be config_path, chapter_start, chapter_end")
        record = collect_literary_sample(load_project_config(Path(sample["config_path"]).resolve()),
                                        sample["chapter_start"], sample["chapter_end"], rehearsal=stage == "rehearsal")
        if STAGES[stage] and record["chapter_end"] - record["chapter_start"] + 1 != STAGES[stage]:
            raise ValueError(f"{stage} requires {STAGES[stage]} chapters per sample")
        if stage == "crossover" and record["topology"] == "sequential_worlds" and len({c["volume_id"] for c in record["chapters"]}) < 2:
            raise ValueError("sequential-world diagnosis must span a volume transition")
        if stage == "crossover" and not record["topology"]:
            raise ValueError("crossover diagnosis requires a crossover contract")
        records.append(record)
    if len({(r["config_path"], r["chapter_start"], r["chapter_end"]) for r in records}) != len(records):
        raise ValueError("duplicate trial samples are not independent samples")
    if stage == "formal" and {r["route_family"] for r in records} != {"oc_si_progression", "canon_character_centered"}:
        raise ValueError("formal acceptance requires both fanfiction protagonist routes")
    secrets.SystemRandom().shuffle(records)
    entries = []
    mapping = {}
    for index, record in enumerate(records):
        blind_id = f"entry-{index + 1}"
        mapping[blind_id] = record
        entries.append({"blind_id": blind_id, "chapters": [
            {**{key: value for key, value in c.items() if key not in {"process_observations", "volume_id"}},
             "path": f"{blind_id}/ch{c['chapter_number']:03d}.md"} for c in record["chapters"]],
            "applicable_metrics": record["applicable_metrics"],
            "metric_applicability": {metric: {"applicable": metric in record["applicable_metrics"],
                "reason": "通用阅读指标" if metric in list(METRICS)[:6] else
                ("适用的创作合同要求" if metric in record["applicable_metrics"] else "本样本批准的创作合同不要求此项")}
                for metric in METRICS}})
    manifest = {"schema": TRIAL_SCHEMA, "trial_id": trial_id, "stage": stage, "entries": entries,
                "evaluation_kind": "simulated_protocol" if stage == "rehearsal" else "independent_human",
                "metrics": METRICS, "reviewer_count_required": 3, "score_scale": [1, 5],
                "thresholds": {m: 4 if m in CORE_METRICS else 3.5 for m in METRICS},
                "scope_note": ("自动演练：生产人工步骤和评分由测试流程模拟；仅验证协议、证据与评分复算，不构成真实人工文学或平台验收。"
                               if stage == "rehearsal" else "仅评估此匿名样本；内部阈值不代表平台通过率或市场表现。")}
    manifest["pack_hash"] = payload_sha256(manifest)
    private = {"schema": "literary_trial_provenance_v2", "trial_id": trial_id,
               "pack_hash": manifest["pack_hash"], "engine_version": __version__,
               "engine_sha256": engine_fingerprint(), "entries": mapping, "owner_execution_origin": owner_origin,
               "created_at": datetime.now(timezone.utc).isoformat()}
    private["sha256"] = payload_sha256(private)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".creating-", dir=target.parent))
    try:
        for entry in entries:
            source = mapping[entry["blind_id"]]
            for chapter, original in zip(entry["chapters"], source["chapters"], strict=True):
                content = (Path(source["project_root"]) / original["path"]).read_bytes()
                if sha256(content).hexdigest() != original["sha256"]:
                    raise ValueError("source changed while creating trial")
                path = staging / "public" / chapter["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        _write_object(staging / "public/manifest.json", manifest)
        _write_object(staging / "private_mapping.json", private)
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"trial_id": trial_id, "pack_hash": manifest["pack_hash"], "stage": stage,
            "status": "awaiting_independent_reviews", "public_manifest": str(target / "public/manifest.json")}


def engine_fingerprint() -> str:
    package = Path(__file__).parent
    from longform_engine.resources import resource_root, resource_integrity_bytes
    resources = resource_root()
    return payload_sha256({
        "code": {p.relative_to(package).as_posix(): sha256(p.read_bytes()).hexdigest()
                 for p in sorted(package.rglob("*.py"))},
        "configuration": {p.relative_to(resources).as_posix(): sha256(resource_integrity_bytes(p)).hexdigest()
                          for p in sorted((resources / "config").rglob("*")) if p.is_file()},
    })


def load_literary_trial(root: Path, trial_id: str, *, current: bool = True) -> tuple[Path, dict[str, Any]]:
    trial = root / TRIAL_DIRECTORY / clean_identifier(trial_id, field="trial_id")
    if not (trial / "public").resolve().is_relative_to(root.resolve()):
        raise ValueError("literary trial must stay inside its project")
    manifest = _read_object(trial / "public/manifest.json")
    if (manifest.get("schema") != TRIAL_SCHEMA or manifest.get("trial_id") != trial_id
            or manifest.get("stage") not in STAGES
            or manifest.get("evaluation_kind") != ("simulated_protocol" if manifest.get("stage") == "rehearsal" else "independent_human")
            or manifest.get("pack_hash") != payload_sha256({k: v for k, v in manifest.items() if k != "pack_hash"})):
        raise ValueError("literary pack protocol/hash invalid; rebuild trial")
    for entry in manifest["entries"]:
        for chapter in entry["chapters"]:
            path = (trial / "public" / chapter["path"]).resolve()
            if not path.is_relative_to((trial / "public").resolve()) or sha256(path.read_bytes()).hexdigest() != chapter["sha256"]:
                raise ValueError("literary pack body hash invalid")
    if current:
        private = _read_object(trial / "private_mapping.json")
        from longform_engine.execution_origin import execution_origin
        if (private.get("schema") != "literary_trial_provenance_v2" or private.get("pack_hash") != manifest["pack_hash"]
                or private.get("sha256") != payload_sha256({k: v for k, v in private.items() if k != "sha256"})
                or private.get("engine_version") != __version__ or private.get("engine_sha256") != engine_fingerprint()
                or private.get("owner_execution_origin") != execution_origin(root)):
            raise ValueError("stale literary provenance or engine binding")
        for record in private["entries"].values():
            fresh = collect_literary_sample(load_project_config(Path(record["config_path"])),
                                            record["chapter_start"], record["chapter_end"], rehearsal=manifest["stage"] == "rehearsal")
            if fresh != record:
                raise ValueError("stale literary sample; source/config/baseline/evidence changed")
    return trial, manifest


def register_literary_reviewer(root: Path, trial_id: str, reviewer_id: str) -> dict[str, Any]:
    trial, manifest = load_literary_trial(root, trial_id)
    reviewer_id = clean_identifier(reviewer_id, field="reviewer_id")
    if ".." in reviewer_id:
        raise ValueError("reviewer_id cannot contain consecutive dots")
    path = trial / "reviewers" / f"{reviewer_id}.json"
    if path.exists():
        raise ValueError("reviewer already registered")
    if len(list((trial / "reviewers").glob("*.json"))) >= 3:
        raise ValueError("a trial has exactly three independent reviewers")
    token = secrets.token_urlsafe(32)
    record = {"reviewer_id": reviewer_id, "token_sha256": sha256(token.encode()).hexdigest(),
              "trial_id": trial_id, "pack_hash": manifest["pack_hash"]}
    _write_object(path, record)
    return {"reviewer_id": reviewer_id, "review_token": token, "trial_id": trial_id}


def literary_reviewer_state(root: Path, trial_id: str, reviewer_id: str,
                            *, token: str | None = None) -> dict[str, Any]:
    reviewer_id = clean_identifier(reviewer_id, field="reviewer_id")
    trial = root / TRIAL_DIRECTORY / clean_identifier(trial_id, field="trial_id")
    try:
        record = _read_object(trial / "reviewers" / f"{reviewer_id}.json")
        if record.get("reviewer_id") != reviewer_id or record.get("trial_id") != trial_id:
            raise ValueError("invalid reviewer identity")
        if token is not None and not hmac.compare_digest(record["token_sha256"], sha256(token.encode()).hexdigest()):
            raise ValueError("invalid reviewer token")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise ValueError("reviewer access denied") from exc
    try:
        trial, manifest = load_literary_trial(root, trial_id)
        if record.get("pack_hash") != manifest["pack_hash"]:
            raise ValueError("reviewer binding invalid")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise ValueError("anonymous sample is stale or unavailable; contact the trial organizer") from exc
    draft_path = trial / "drafts" / f"{reviewer_id}.json"
    submitted_path = trial / "submissions" / f"{reviewer_id}.json"
    draft = _read_object(draft_path) if draft_path.exists() else {
        "schema": REVIEW_SCHEMA, "trial_id": trial_id, "pack_hash": manifest["pack_hash"],
        "reviewer_id": reviewer_id, "human_instance_id": "", "independence_confirmed": False,
        "reviewer_kind": "simulated" if manifest["stage"] == "rehearsal" else "human",
        "attestation_note": "", "review_minutes": None,
        "entries": [{"blind_id": e["blind_id"], "scores": {m: None for m in e["applicable_metrics"]},
                     "findings": [], "notes": ""} for e in manifest["entries"]]}
    return {"manifest": manifest, "reviewer_id": reviewer_id, "draft": draft,
            "draft_sha256": payload_sha256(draft), "submitted": submitted_path.exists(),
            "submission": _read_object(submitted_path) if submitted_path.exists() else None,
            "chapters": [{"blind_id": e["blind_id"], **c,
                          "body": (trial / "public" / c["path"]).read_text(encoding="utf-8")}
                         for e in manifest["entries"] for c in e["chapters"]]}


def validate_literary_review(manifest: dict[str, Any], draft: dict[str, Any], *, complete: bool,
                            public_root: Path) -> None:
    fields = {"schema", "trial_id", "pack_hash", "reviewer_id", "human_instance_id", "independence_confirmed",
              "attestation_note", "review_minutes", "entries", "reviewer_kind"}
    if not isinstance(draft, dict) or set(draft) != fields or draft["schema"] != REVIEW_SCHEMA or draft["pack_hash"] != manifest["pack_hash"] or draft["trial_id"] != manifest["trial_id"]:
        raise ValueError("review protocol or pack binding invalid")
    if draft["reviewer_kind"] != ("simulated" if manifest["stage"] == "rehearsal" else "human"):
        raise ValueError("评分来源与评测用途不一致；模拟评分不能作为真人评价")
    for field in ("reviewer_id", "human_instance_id", "attestation_note"):
        if not isinstance(draft[field], str) or draft[field] != draft[field].strip():
            raise ValueError(f"{field} must be text without surrounding whitespace")
    if not isinstance(draft["independence_confirmed"], bool):
        raise ValueError("independent-review attestation must be an explicit boolean")
    if complete and (draft["independence_confirmed"] is not True or not str(draft["attestation_note"]).strip()
                     or not str(draft["human_instance_id"]).strip()):
        raise ValueError("human identity and independent-review attestation are required")
    minutes = draft["review_minutes"]
    if minutes is not None and (type(minutes) not in (int, float) or not 0 <= minutes <= 100000):
        raise ValueError("review minutes must be non-negative or unknown")
    entries = draft["entries"]
    if not isinstance(entries, list) or len(entries) != len(manifest["entries"]) or any(not isinstance(e, dict) or not isinstance(e.get("blind_id"), str) for e in entries) or {e.get("blind_id") for e in entries} != {e["blind_id"] for e in manifest["entries"]}:
        raise ValueError("review entries must match anonymous pack exactly")
    for entry in entries:
        expected = next(e for e in manifest["entries"] if e["blind_id"] == entry["blind_id"])
        if set(entry) != {"blind_id", "scores", "findings", "notes"} or not isinstance(entry["scores"], dict) or set(entry["scores"]) != set(expected["applicable_metrics"]):
            raise ValueError("scores must cover every applicable metric; core scores cannot be not applicable")
        if not isinstance(entry["notes"], str):
            raise ValueError("review notes must be text")
        for score in entry["scores"].values():
            if score is None and not complete:
                continue
            if type(score) not in (int, float) or not 1 <= score <= 5 or score * 2 != int(score * 2):
                raise ValueError("scores must be 1–5 in half-point steps")
        if not isinstance(entry["findings"], list):
            raise ValueError("findings must be a list")
        for finding in entry["findings"]:
            if not isinstance(finding, dict) or set(finding) != {"chapter_number", "start", "end", "text", "metric", "severity", "note"}:
                raise ValueError("finding requires an exact passage, metric, severity and explanation")
            if (type(finding["chapter_number"]) is not int or any(
                not isinstance(finding[key], str) for key in ("text", "metric", "severity", "note")
            )):
                raise ValueError("finding passage and explanation must have structured types")
            chapters = [c for c in expected["chapters"] if c["chapter_number"] == finding["chapter_number"]]
            if len(chapters) != 1 or finding["metric"] not in expected["applicable_metrics"] or finding["severity"] not in {"minor", "major", "critical"} or not str(finding["note"]).strip():
                raise ValueError("finding scope or explanation invalid")
            body = (public_root / chapters[0]["path"]).read_text(encoding="utf-8")
            start, end = finding["start"], finding["end"]
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(body) or body[start:end] != finding["text"]:
                raise ValueError("finding passage does not match the anonymous chapter")


def save_literary_review(root: Path, trial_id: str, reviewer_id: str, draft: dict[str, Any], *,
                         expected_sha256: str, submit: bool = False, token: str | None = None) -> dict[str, Any]:
    state = literary_reviewer_state(root, trial_id, reviewer_id, token=token)
    trial = root / TRIAL_DIRECTORY / trial_id
    if not isinstance(draft, dict) or draft.get("reviewer_id") != reviewer_id:
        raise ValueError("cannot write another reviewer's record")
    if state["submitted"]:
        if submit and state["submission"]["review"] == draft:
            return {"submitted": True, "idempotent": True}
        raise ValueError("submitted independent scores are immutable")
    if state["draft_sha256"] != expected_sha256:
        raise ValueError("stale review draft; reload before saving")
    validate_literary_review(state["manifest"], draft, complete=submit, public_root=trial / "public")
    if submit:
        for path in (trial / "submissions").glob("*.json"):
            if _read_object(path)["review"]["human_instance_id"] == draft["human_instance_id"]:
                raise ValueError("three distinct human reviewers are required")
        record = {"review": draft, "review_sha256": payload_sha256(draft),
                  "submitted_at": datetime.now(timezone.utc).isoformat()}
        _write_object(trial / "submissions" / f"{reviewer_id}.json", record)
    else:
        _write_object(trial / "drafts" / f"{reviewer_id}.json", draft)
    return {"submitted": submit, "draft_sha256": payload_sha256(draft)}


def aggregate_literary_trial(root: Path, trial_id: str) -> dict[str, Any]:
    """Recompute every score and unresolved issue; saved reports are never authority."""
    trial, manifest = load_literary_trial(root, trial_id)
    submissions = []
    for path in sorted((trial / "submissions").glob("*.json")):
        record = _read_object(path)
        review = record["review"]
        if record["review_sha256"] != payload_sha256(review) or path.stem != review["reviewer_id"]:
            raise ValueError("review submission hash/identity invalid")
        if not (trial / "reviewers" / path.name).is_file():
            raise ValueError("unregistered literary reviewer")
        validate_literary_review(manifest, review, complete=True, public_root=trial / "public")
        submissions.append(review)
    if len({r["human_instance_id"] for r in submissions}) != len(submissions) or len(submissions) > 3:
        raise ValueError("independent human reviewer identities are not unique")
    missing = 3 - len(submissions)
    entries = []
    issues = []
    for expected in manifest["entries"]:
        reviews = [next(e for e in r["entries"] if e["blind_id"] == expected["blind_id"]) for r in submissions]
        scores = {}
        for metric in expected["applicable_metrics"]:
            values = [r["scores"][metric] for r in reviews]
            threshold = manifest["thresholds"][metric]
            scores[metric] = {"scores": values, "median": median(values) if not missing else None,
                              "threshold": threshold, "passed": not missing and median(values) >= threshold}
            if not missing and (max(values) - min(values) >= 2 or min(values) < threshold <= max(values)):
                issues.append({"id": f"{expected['blind_id']}:{metric}:disagreement", "kind": "disagreement",
                               "blind_id": expected["blind_id"], "metric": metric, "scores": values})
        for reviewer, review in zip(submissions, reviews, strict=True):
            for index, finding in enumerate(review["findings"]):
                if finding["severity"] in {"major", "critical"}:
                    issues.append({"id": f"{expected['blind_id']}:{reviewer['reviewer_id']}:{index}",
                                   "kind": "serious_finding", "blind_id": expected["blind_id"], "finding": finding})
        entries.append({"blind_id": expected["blind_id"], "scores": scores,
                        "review_notes": [{"reviewer_id": reviewer["reviewer_id"], "notes": r["notes"],
                                          "findings": r["findings"]}
                                         for reviewer, r in zip(submissions, reviews, strict=True)]})
    evidence_hash = payload_sha256(submissions)
    resolution_path = trial / "resolution.json"
    resolution = _read_object(resolution_path) if resolution_path.exists() else {}
    resolved = resolution.get("submission_sha256") == evidence_hash and resolution.get("pack_hash") == manifest["pack_hash"]
    if resolved:
        if resolution.get("schema") != "literary_resolution_v2":
            raise ValueError("literary resolution protocol invalid")
        validate_literary_resolution(issues, resolution.get("decisions"), resolution.get("decided_by"))
    unresolved = [issue for issue in issues if not resolved or issue["id"] not in resolution.get("decisions", {})]
    serious = [i for i in issues if i["kind"] == "serious_finding" and (
        not resolved or resolution.get("decisions", {}).get(i["id"], {}).get("outcome") != "not_substantiated")]
    passed = not missing and not unresolved and not serious and all(v["passed"] for e in entries for v in e["scores"].values())
    private = _read_object(trial / "private_mapping.json")
    effort = []
    for blind_id, source in private["entries"].items():
        for chapter in source["chapters"]:
            path = Path(source["project_root"]) / "70_runtime/literary_effort" / f"ch{chapter['chapter_number']:03d}.json"
            record = _read_object(path) if path.exists() else {}
            current_effort = record.get("final_sha256") == chapter["sha256"]
            effort.append({"blind_id": blind_id, "chapter_number": chapter["chapter_number"],
                           "human_review_minutes": record.get("human_review_minutes") if current_effort else None,
                           "human_edit_minutes": record.get("human_edit_minutes") if current_effort else None,
                           **chapter["process_observations"]})
    return {"schema": "literary_trial_report_v2", "trial_id": trial_id, "stage": manifest["stage"],
            "pack_hash": manifest["pack_hash"], "submission_sha256": evidence_hash,
            "status": "awaiting_reviews" if missing else "pending_resolution" if unresolved else (
                "protocol_complete" if manifest["stage"] == "rehearsal" else "passed") if passed else "needs_revision",
            "evaluation_kind": manifest["evaluation_kind"],
            "reviewers_submitted": [r["reviewer_id"] for r in submissions], "missing_reviewers": missing,
            "entries": entries, "metrics": METRICS, "author_effort": effort, "issues": issues, "unresolved_issues": unresolved,
            "resolution": resolution if resolved else None,
            "human_review_minutes": [r["review_minutes"] for r in submissions],
            "formal_acceptance": passed and manifest["stage"] == "formal",
            "scope_note": manifest["scope_note"], "current": True}


def validate_literary_resolution(issues: list[dict[str, Any]], decisions: Any, decided_by: Any) -> None:
    """Validate every evidence-bound resolution without discarding any original score."""
    if not isinstance(decided_by, str) or not decided_by.strip():
        raise ValueError("resolution requires a human decision owner")
    if not isinstance(decisions, dict) or set(decisions) != {i["id"] for i in issues}:
        raise ValueError("every serious issue and disagreement must receive an explicit decision")
    for issue in issues:
        decision = decisions[issue["id"]]
        outcomes = {"acknowledged"} if issue["kind"] == "disagreement" else {"needs_revision", "not_substantiated"}
        if not isinstance(decision, dict) or set(decision) != {"outcome", "reason", "follow_up"} or not all(isinstance(decision[key], str) for key in decision) or decision["outcome"] not in outcomes or not str(decision["reason"]).strip() or not str(decision["follow_up"]).strip():
            raise ValueError("decision needs a supported outcome, reason and follow-up; scores cannot be changed")


def resolve_literary_issues(root: Path, trial_id: str, *, submission_sha256: str,
                           decisions: dict[str, Any], decided_by: str) -> dict[str, Any]:
    report = aggregate_literary_trial(root, trial_id)
    if report["missing_reviewers"] or submission_sha256 != report["submission_sha256"] or not isinstance(decided_by, str) or not decided_by.strip():
        raise ValueError("resolve issues only after three submissions, against their exact hash")
    validate_literary_resolution(report["issues"], decisions, decided_by)
    _write_object(root / TRIAL_DIRECTORY / trial_id / "resolution.json", {
        "schema": "literary_resolution_v2", "pack_hash": report["pack_hash"],
        "submission_sha256": submission_sha256, "decisions": decisions, "decided_by": decided_by,
        "decided_at": datetime.now(timezone.utc).isoformat()})
    return aggregate_literary_trial(root, trial_id)


def export_literary_pack(root: Path, trial_id: str) -> bytes:
    import io
    trial, manifest = load_literary_trial(root, trial_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in ["manifest.json", *(c["path"] for e in manifest["entries"] for c in e["chapters"])]:
            archive.writestr(relative, (trial / "public" / relative).read_bytes())
    return buffer.getvalue()


def literary_trial_status(root: Path) -> dict[str, Any]:
    trials = []
    for path in sorted((root / TRIAL_DIRECTORY).glob("*/public/manifest.json")):
        trial_id = path.parent.parent.name
        try:
            trials.append(aggregate_literary_trial(root, trial_id))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            trials.append({"trial_id": trial_id, "status": "stale", "current": False, "reason": str(exc)})
    return {"status": "unverified" if not trials else "available", "trials": trials,
            "formal_acceptance": any(t.get("formal_acceptance") for t in trials),
            "scope_note": "无真实样本或独立人工评审时为未验证；工程测试不构成文学验收。"}


def record_literary_effort(config: ConfigDocument, *, chapter_number: int,
                          human_review_minutes: float | None, human_edit_minutes: float | None) -> dict[str, Any]:
    """Record voluntary human effort, bound to a closed final; missing means unknown."""
    for value in (human_review_minutes, human_edit_minutes):
        if value is not None and (type(value) not in (float, int) or not 0 <= value <= 100000):
            raise ValueError("human effort minutes must be non-negative or unknown")
    sample = collect_literary_sample(config, chapter_number, chapter_number)
    record = {"schema": "literary_author_effort_v1", "chapter_number": chapter_number,
              "final_sha256": sample["chapters"][0]["sha256"], "recorded_by": "human",
              "human_review_minutes": human_review_minutes, "human_edit_minutes": human_edit_minutes,
              "recorded_at": datetime.now(timezone.utc).isoformat()}
    _write_object(resolve_project_root(config) / "70_runtime/literary_effort" / f"ch{chapter_number:03d}.json", record)
    return record
