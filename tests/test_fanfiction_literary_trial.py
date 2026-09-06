import copy
import io
import json
from pathlib import Path
import zipfile

import pytest

from longform_engine.config import load_project_config
from longform_engine.fanfiction_literary_trial import (
    STAGES, TRIAL_DIRECTORY, aggregate_literary_trial, collect_literary_sample,
    create_literary_trial, export_literary_pack, literary_reviewer_state,
    literary_trial_status, register_literary_reviewer, resolve_literary_issues,
    save_literary_review,
)
from tests.test_orchestration import seed_project, open_book, passing_draft_text


def test_abstract_source_adapter_fixtures_cover_distinct_rule_shapes():
    fixture_file = Path(__file__).parent / "fixtures" / "fanfiction_source_adapter_archetypes.json"
    payload = json.loads(fixture_file.read_text(encoding="utf-8"))

    assert payload["schema"] == "fanfiction_source_adapter_archetype_fixture_v1"
    assert len(payload["fixtures"]) == 8
    assert len({item["fixture_id"] for item in payload["fixtures"]}) == 8
    assert all(len(item["dimensions"]) >= 5 for item in payload["fixtures"])
    all_dimensions = {dimension for item in payload["fixtures"] for dimension in item["dimensions"]}
    assert {
        "activation_condition",
        "irreversible_death",
        "system_boundary",
        "knowledge_decay",
        "travel_time",
        "soul",
        "long_timescale",
        "backlash",
    } <= all_dimensions



@pytest.fixture(scope="module")
def closed_project(tmp_path_factory):
    import yaml
    from longform_engine.orchestration import continue_write, submit_agent_draft, finalize_chapter
    from tests.project_fixtures import approve_story_candidate, complete_unified_semantic_lifecycle

    directory = tmp_path_factory.mktemp("current-literary-source")
    config = seed_project(directory)
    open_book(config)
    root = directory / "novel"
    config.data["quality"]["semantic_review_milestones"] = [1]
    config.data["quality"]["semantic_pacing"]["review_mode"] = "required"
    config.path.write_text(yaml.safe_dump(config.data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    continue_write(config, chapter_number=1)
    candidate = root / "50_workbench/agent_drafts/ch001.codex.md"
    candidate.write_text(passing_draft_text(), encoding="utf-8")
    submit_agent_draft(config, chapter_number=1, file_path=candidate, agent="codex")
    approve_story_candidate(root, config)
    finalize_chapter(config, chapter_number=1, approved_by="human")
    complete_unified_semantic_lifecycle(root, config, 1)
    return load_project_config(root / "project.yaml"), root


def trial_fixture(closed_project, monkeypatch, trial_id):
    config, root = closed_project
    # A one-chapter protocol fixture, never a real 3/10/20-chapter literary acceptance.
    monkeypatch.setitem(STAGES, "opening", 1)
    create_literary_trial(config, trial_id=trial_id, stage="opening", samples=[
        {"config_path": str(config.path), "chapter_start": 1, "chapter_end": 1}])
    return root


def test_closed_current_protocol_evidence_and_anonymous_independent_ratings(closed_project, monkeypatch):
    root = trial_fixture(closed_project, monkeypatch, "independent")
    sample = collect_literary_sample(closed_project[0], 1, 1)
    assert len(sample["evidence"]) >= 25
    assert "chapter_cards" not in json.dumps(sample)
    scores = [2, 4, 5]
    submitted = []
    for index, naturalness in enumerate(scores):
        reviewer = f"reader-{index}"
        registration = register_literary_reviewer(root, "independent", reviewer)
        token = registration["review_token"]
        state = literary_reviewer_state(root, "independent", reviewer, token=token)
        assert not {"private_mapping", "project_root", "submissions", "other_reviews"} & state.keys()
        assert str(root) not in json.dumps(state)
        draft = state["draft"]
        draft.update(human_instance_id=f"human-{index}", independence_confirmed=True,
                     attestation_note="Protocol-only simulated reviewer; no literary claim.")
        draft["entries"][0]["scores"] = {m: 4 for m in draft["entries"][0]["scores"]}
        draft["entries"][0]["scores"]["prose_naturalness"] = naturalness
        with pytest.raises(ValueError, match="access denied"):
            save_literary_review(root, "independent", reviewer, draft, expected_sha256=state["draft_sha256"], token="wrong")
        save_literary_review(root, "independent", reviewer, draft, expected_sha256=state["draft_sha256"], token=token)
        with pytest.raises(ValueError, match="stale review draft"):
            save_literary_review(root, "independent", reviewer, draft, expected_sha256=state["draft_sha256"], token=token)
        current = literary_reviewer_state(root, "independent", reviewer, token=token)
        save_literary_review(root, "independent", reviewer, draft, expected_sha256=current["draft_sha256"], submit=True, token=token)
        result = save_literary_review(root, "independent", reviewer, draft, expected_sha256=current["draft_sha256"], submit=True, token=token)
        assert result["idempotent"]
        changed = copy.deepcopy(draft)
        changed["entries"][0]["scores"]["prose_naturalness"] = 5
        with pytest.raises(ValueError, match="immutable"):
            save_literary_review(root, "independent", reviewer, changed, expected_sha256=current["draft_sha256"], token=token)
        submitted.append(draft)
    report = aggregate_literary_trial(root, "independent")
    assert report["status"] == "pending_resolution"
    assert report["entries"][0]["scores"]["prose_naturalness"]["median"] == 4
    report = resolve_literary_issues(root, "independent", submission_sha256=report["submission_sha256"],
        decisions={i["id"]: {"outcome": "acknowledged", "reason": "Keep the full disagreement.",
                             "follow_up": "Observe next diagnostic sample."} for i in report["issues"]}, decided_by="human-chair")
    assert report["status"] == "passed"
    assert report["formal_acceptance"] is False
    assert report["entries"][0]["scores"]["prose_naturalness"]["scores"] == scores
    with zipfile.ZipFile(io.BytesIO(export_literary_pack(root, "independent"))) as archive:
        assert set(archive.namelist()) == {"manifest.json", "entry-1/ch001.md"}
        assert all("private" not in p and "reviewer" not in p for p in archive.namelist())
    with pytest.raises(ValueError, match="already exists"):
        create_literary_trial(closed_project[0], trial_id="independent", stage="opening", samples=[
            {"config_path": str(closed_project[0].path), "chapter_start": 1, "chapter_end": 1}])


def test_literary_rejects_unclosed_sources_and_changed_final(closed_project, monkeypatch):
    config, root = closed_project
    root = trial_fixture(closed_project, monkeypatch, "currentness")
    final = root / "40_manuscript/final/ch001.md"
    original = final.read_bytes()
    try:
        final.write_bytes(original + "改动".encode())
        with pytest.raises(ValueError, match="closure"):
            aggregate_literary_trial(root, "currentness")
        assert next(t for t in literary_trial_status(root)["trials"] if t["trial_id"] == "currentness")["status"] == "stale"
    finally:
        final.write_bytes(original)
    with pytest.raises((ValueError, OSError)):
        create_literary_trial(config, trial_id="unclosed", stage="opening", samples=[
            {"config_path": str(config.path), "chapter_start": 2, "chapter_end": 2}])
    assert not (root / TRIAL_DIRECTORY / "unclosed").exists()


@pytest.mark.parametrize("filename", ["semantic_review_result.json", "semantic_pacing_result.json"])
def test_literary_requires_bound_original_independent_results(closed_project, filename):
    config, root = closed_project
    path = root / "50_workbench/gate_artifacts/ch001" / filename
    original = path.read_bytes()
    try:
        path.unlink()
        with pytest.raises(FileNotFoundError):
            collect_literary_sample(config, 1, 1)
        changed = json.loads(original)
        changed["verdict"] = "repair"
        path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError, match="stale|invalid"):
            collect_literary_sample(config, 1, 1)
    finally:
        path.write_bytes(original)


def test_review_evidence_core_metrics_and_distinct_human_identity(closed_project, monkeypatch):
    root = trial_fixture(closed_project, monkeypatch, "findings")
    for index in range(2):
        register_literary_reviewer(root, "findings", f"reader-{index}")
        state = literary_reviewer_state(root, "findings", f"reader-{index}")
        draft = state["draft"]
        draft.update(human_instance_id="same-human", independence_confirmed=True, attestation_note="Test fixture.")
        draft["entries"][0]["scores"] = {m: 4 for m in draft["entries"][0]["scores"]}
        if index:
            with pytest.raises(ValueError, match="distinct human"):
                save_literary_review(root, "findings", "reader-1", draft, expected_sha256=state["draft_sha256"], submit=True)
        else:
            entry = draft["entries"][0]
            entry["findings"] = [{"chapter_number": 1, "start": 0, "end": 2, "text": "伪造",
                "metric": "prose_naturalness", "severity": "major", "note": "Must bind real text."}]
            with pytest.raises(ValueError, match="passage"):
                save_literary_review(root, "findings", "reader-0", draft, expected_sha256=state["draft_sha256"], submit=True)
            entry["findings"][0]["text"] = state["chapters"][0]["body"][:2]
            entry["scores"]["prose_naturalness"] = None
            with pytest.raises(ValueError, match="scores"):
                save_literary_review(root, "findings", "reader-0", draft, expected_sha256=state["draft_sha256"], submit=True)
            entry["scores"]["prose_naturalness"] = 4
            save_literary_review(root, "findings", "reader-0", draft, expected_sha256=state["draft_sha256"], submit=True)


def test_workspace_literary_actions_use_project_scope_and_reviewer_capability(closed_project, monkeypatch):
    from longform_engine.workspace_studio import WorkspaceStudioService, WorkspaceStudioError
    config, root = closed_project
    monkeypatch.setitem(STAGES, "opening", 1)
    service = WorkspaceStudioService(root.parent)
    project_id = service._project_id(config.path)
    service.literary_action(project_id, "create", {"trial_id": "web", "stage": "opening", "samples": [
        {"project_id": project_id, "chapter_start": 1, "chapter_end": 1}]})
    registration = service.literary_action(project_id, "reviewer-add", {"trial_id": "web", "reviewer_id": "reader"})
    state = service.literary_review_state(project_id, "web", "reader", registration["review_token"])
    assert state["reviewer_id"] == "reader"
    with pytest.raises(WorkspaceStudioError):
        service.literary_review_state("project_" + "0" * 20, "web", "reader", registration["review_token"])
    with pytest.raises(WorkspaceStudioError, match="凭证"):
        service.literary_review_state(project_id, "web", "reader", "")
    with pytest.raises(WorkspaceStudioError, match="项目 ID"):
        service.literary_action(project_id, "create", {"trial_id": "escape", "stage": "opening", "samples": [
            {"config_path": str(config.path), "chapter_start": 1, "chapter_end": 1}]})
    assert service.literary_state(project_id)["trials"] == literary_trial_status(root)["trials"]


def test_literary_http_routes_csrf_anonymous_access_and_exports(closed_project, monkeypatch):
    import threading
    from longform_engine.workspace_studio import WorkspaceStudioService, WorkspaceStudioHTTPServer
    from tests.test_workspace_studio import _request
    from longform_engine.cli import main
    config, root = closed_project
    trial_fixture(closed_project, monkeypatch, "transport")
    assert main(["literary", "status", str(config.path), "--json"]) == 0
    registration = register_literary_reviewer(root, "transport", "reader")
    service = WorkspaceStudioService(root.parent)
    project_id = service._project_id(config.path)
    server = WorkspaceStudioHTTPServer(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _, headers, _ = _request(server, "GET", f"/?token={server.bootstrap_token}")
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        api = f"/api/projects/{project_id}/literary"
        headers = {"Origin": f"http://127.0.0.1:{server.port}",
                   "X-Studio-CSRF": server.csrf_token, "X-Literary-Reviewer": registration["review_token"]}
        status, _, body = _request(server, "GET", api + "/transport/review/reader", headers=headers)
        assert status == 200
        assert _request(server, "GET", api, headers=headers)[0] == 403
        assert _request(server, "GET", "/api/workspace", headers=headers)[0] == 403
        assert _request(server, "GET", api, headers={"Cookie": cookie, **headers})[0] == 403
        state = json.loads(body)
        assert str(root) not in json.dumps(state)
        payload = {"trial_id": "transport", "reviewer_id": "reader", "draft": state["draft"],
                   "expected_sha256": state["draft_sha256"]}
        bad_headers = {k: v for k, v in headers.items() if k != "X-Studio-CSRF"}
        assert _request(server, "POST", api + "/save", headers=bad_headers, payload=payload)[0] == 403
        assert _request(server, "POST", api + "/save", headers=headers, payload=payload)[0] == 200
        wrong = dict(headers, **{"X-Literary-Reviewer": "wrong"})
        assert _request(server, "GET", api + "/transport/review/reader", headers=wrong)[0] == 403
        status, response_headers, page = _request(server, "GET", f"/projects/{project_id}/literary/review/transport/reader", headers=headers)
        assert status == 200
        assert b"X-Studio-CSRF" in page
        assert "Content-Security-Policy" in response_headers
        status, response_headers, content = _request(server, "GET", api + "/transport/export-pack", headers={"Cookie": cookie})
        assert status == 200 and response_headers["Content-Type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            assert "private_mapping.json" not in archive.namelist()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("invalid", [[], {"entries": [None]}, {"human_instance_id": []},
    {"independence_confirmed": "true"}, {"entries": [{"blind_id": "entry-1", "scores": [], "findings": [], "notes": ""}]}])
def test_invalid_rating_types_are_rejected_before_writing(closed_project, monkeypatch, invalid):
    from longform_engine.fanfiction_literary_trial import validate_literary_review
    root = trial_fixture(closed_project, monkeypatch, "typed-" + str(len(list((closed_project[1] / TRIAL_DIRECTORY).glob("typed-*")))))
    trial_id = sorted((root / TRIAL_DIRECTORY).glob("typed-*"))[-1].name
    register_literary_reviewer(root, trial_id, "reader")
    state = literary_reviewer_state(root, trial_id, "reader")
    draft = {**state["draft"], **invalid} if isinstance(invalid, dict) else invalid
    with pytest.raises(ValueError):
        validate_literary_review(state["manifest"], draft, complete=False, public_root=root / TRIAL_DIRECTORY / trial_id / "public")
    assert not (root / TRIAL_DIRECTORY / trial_id / "drafts").exists()


def test_trial_creation_rolls_back_and_exports_only_bound_public_files(closed_project, monkeypatch):
    from longform_engine import fanfiction_literary_trial as domain
    config, root = closed_project
    monkeypatch.setitem(STAGES, "opening", 1)
    original_writer = domain._write_object
    def fail_private(path, payload):
        if path.name == "private_mapping.json":
            raise OSError("injected trial publication failure")
        original_writer(path, payload)
    with monkeypatch.context() as patch:
        patch.setattr(domain, "_write_object", fail_private)
        with pytest.raises(OSError, match="injected"):
            create_literary_trial(config, trial_id="rolled-back", stage="opening", samples=[
                {"config_path": str(config.path), "chapter_start": 1, "chapter_end": 1}])
    assert not (root / TRIAL_DIRECTORY / "rolled-back").exists()
    assert not list((root / TRIAL_DIRECTORY).glob(".creating-*"))
    trial_fixture(closed_project, monkeypatch, "export-boundary")
    (root / TRIAL_DIRECTORY / "export-boundary/public/unbound-private-note.txt").write_text("Must not export", encoding="utf-8")
    with zipfile.ZipFile(io.BytesIO(export_literary_pack(root, "export-boundary"))) as archive:
        assert "unbound-private-note.txt" not in archive.namelist()
    assert collect_literary_sample(config, 1, 1)["chapters"][0]["sha256"]


def test_serious_findings_require_resolution_without_score_changes(closed_project, monkeypatch):
    from longform_engine.fanfiction_literary_trial import record_literary_effort
    config, root = closed_project
    trial_fixture(closed_project, monkeypatch, "serious")
    for index in range(3):
        reviewer = f"reader-{index}"
        register_literary_reviewer(root, "serious", reviewer)
        state = literary_reviewer_state(root, "serious", reviewer)
        draft = state["draft"]
        draft.update(human_instance_id=f"person-{index}", independence_confirmed=True, attestation_note="Synthetic protocol fixture.")
        entry = draft["entries"][0]
        entry["scores"] = {metric: 4 for metric in entry["scores"]}
        if index == 0:
            entry["findings"] = [{"chapter_number": 1, "start": 0, "end": 2,
                "text": state["chapters"][0]["body"][:2], "metric": "character_agency",
                "severity": "major", "note": "An evidence-span fixture, not a literary judgment."}]
        save_literary_review(root, "serious", reviewer, draft, expected_sha256=state["draft_sha256"], submit=True)
    report = aggregate_literary_trial(root, "serious")
    assert report["status"] == "pending_resolution"
    decisions = {item["id"]: {"outcome": "needs_revision", "reason": "Confirmed in the quoted scene.",
                 "follow_up": "Revise the scene and create a new trial."} for item in report["issues"]}
    resolved = resolve_literary_issues(root, "serious", submission_sha256=report["submission_sha256"],
                                     decisions=decisions, decided_by="human-chair")
    assert resolved["status"] == "needs_revision"
    assert resolved["entries"] == report["entries"]
    record_literary_effort(config, chapter_number=1, human_review_minutes=12, human_edit_minutes=None)
    assert aggregate_literary_trial(root, "serious")["author_effort"][0] == {
        "blind_id": "entry-1", "chapter_number": 1, "human_review_minutes": 12, "human_edit_minutes": None,
        "context_estimated_units": collect_literary_sample(config, 1, 1)["chapters"][0]["process_observations"]["context_estimated_units"],
        "repair_attempts": None, "blocking_findings_observed": None}
    resolution_path = root / TRIAL_DIRECTORY / "serious/resolution.json"
    resolution = json.loads(resolution_path.read_text(encoding="utf-8"))
    next(iter(resolution["decisions"].values())).update(outcome="not_substantiated", reason="")
    resolution_path.write_text(json.dumps(resolution), encoding="utf-8")
    with pytest.raises(ValueError, match="decision needs"):
        aggregate_literary_trial(root, "serious")


def test_literary_rejects_changed_editorial_result_even_when_verdict_still_passes(closed_project):
    config, root = closed_project
    aggregate = json.loads((root / "50_workbench/editorial_reviews/ch001.aggregate.json").read_text(encoding="utf-8"))
    result_path = root / aggregate["accepted_results"][0]
    original = result_path.read_bytes()
    try:
        result_path.write_bytes(original + b"\n")
        with pytest.raises(ValueError, match="stale literary evidence"):
            collect_literary_sample(config, 1, 1)
    finally:
        result_path.write_bytes(original)


def test_literary_evidence_survives_verified_chapter_archive(closed_project, monkeypatch):
    from longform_engine.artifacts import write_chapter_archive, verify_single_archive
    config, root = closed_project
    trial_fixture(closed_project, monkeypatch, "archived-evidence")
    before = collect_literary_sample(config, 1, 1)
    # Exercise the real archive writer/reader without changing the two-chapter retention policy.
    paths = sorted({root / item["path"] for item in before["evidence"] if item["path"].startswith("50_workbench/")})
    archive, manifest = write_chapter_archive(root, 1, paths)
    assert not verify_single_archive(root, archive, json.loads(manifest.read_text(encoding="utf-8")))
    for path in paths:
        path.unlink()
    assert collect_literary_sample(config, 1, 1) == before
    assert aggregate_literary_trial(root, "archived-evidence")["current"]
