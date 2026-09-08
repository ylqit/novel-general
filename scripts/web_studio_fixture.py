"""Run a real Studio against explicitly labelled fixtures for browser testing.

Fixtures are not model output or literary evidence. No production gate is bypassed
in a user project. This launcher never starts an Agent job by itself.
"""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longform_engine import __version__  # noqa: E402
from longform_engine.agent_jobs import CodexAgentJobManager  # noqa: E402
from longform_engine.storage import atomic_write_text  # noqa: E402
from longform_engine.workspace_studio import WorkspaceStudioHTTPServer, WorkspaceStudioService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--seed-reader-fixture", action="store_true")
    parser.add_argument("--seed-review-fixture", action="store_true")
    parser.add_argument("--seed-literary-fixture", action="store_true")
    parser.add_argument("--seed-fanfiction-fixture", action="store_true")
    parser.add_argument("--large-catalogue", action="store_true", help="Seed 400 explicitly synthetic reading chapters.")
    parser.add_argument("--codex-executable", type=Path)
    args = parser.parse_args()
    if sum((args.seed_reader_fixture, args.seed_review_fixture, args.seed_literary_fixture, args.seed_fanfiction_fixture)) > 1:
        parser.error("Seed one clearly identified fixture kind per run")
    if not args.workspace.is_absolute() or not args.evidence.is_absolute():
        parser.error("Use explicit absolute isolated workspace and evidence directories")
    if args.large_catalogue and not args.seed_reader_fixture:
        parser.error("Large catalogue requires a new labelled reader fixture")
    if args.codex_executable and (not args.codex_executable.is_absolute() or not args.codex_executable.is_file()):
        parser.error("Codex executable must be an existing absolute local file")
    service = WorkspaceStudioService(args.workspace, create=True, rehearsal_run_id=args.run_id,
        agent_jobs=CodexAgentJobManager(codex_command=[str(args.codex_executable)]) if args.codex_executable else None)
    if args.seed_reader_fixture:
        from tests.test_current_planning_context import approved_project
        folder = args.workspace / "reader-fixture"
        if folder.exists():
            parser.error("Fixture already exists; use a new workspace to preserve earlier evidence")
        config, root = approved_project(folder)
        atomic_write_text(root / "00_governance/execution_origin.json", json.dumps({
            "schema": "execution_origin_v1", "kind": "automated_rehearsal", "simulated_human": True,
            "run_id": args.run_id, "fixture": True, "formal_literary_eligible": False,
        }, ensure_ascii=False, indent=2))
        for n in (range(1, 401) if args.large_catalogue else (1, 2, 30, 31)):
            # Synthetic long Chinese prose exclusively exercises layout and anchors.
            prose = f"# 第 {n} 章 阅读夹具\n\n" + "\n\n".join(
                f"测试段落 {i}：陆照按住炉沿，𠮷字用于检查补充字符的位置。这里是界面隔离测试材料，不是试写小说。" for i in range(1, 35)) + "\n"
            atomic_write_text(root / f"40_manuscript/final/ch{n:03d}.md", prose)
        skeleton_path = root / "20_outline/volume_skeletons.json"
        skeletons = json.loads(skeleton_path.read_text(encoding="utf-8"))
        skeletons["items"] = [skeletons["items"][0], {**skeletons["items"][0], "volume_id": "volume:002", "order": 2, "title": "第二卷 · 阅读夹具", "chapter_range": [31, 60]}]
        if args.large_catalogue:
            skeletons["items"].extend({**skeletons["items"][0], "volume_id": f"volume:{index:03d}", "order": index,
                "title": f"第 {index} 卷 · 大目录夹具", "chapter_range": [(index - 1) * 30 + 1, index * 30]}
                for index in range(3, 15))
        atomic_write_text(skeleton_path, json.dumps(skeletons, ensure_ascii=False, indent=2))
        basis_path = root / "30_state/planning_basis.json"
        basis = json.loads(basis_path.read_text(encoding="utf-8"))
        for row in basis["source_files"]:
            if row["path"] == "20_outline/volume_skeletons.json":
                row["sha256"] = sha256(skeleton_path.read_bytes()).hexdigest()
        atomic_write_text(basis_path, json.dumps(basis, ensure_ascii=False, indent=2))
        historical = "# 第 1 章 阅读夹具\n\n旧版本夹具：陆照尚未说明伤势。这不是试写文学证据。\n".encode("utf-8")
        archive = root / "70_runtime/artifacts/chapters/ch001.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("_audit/manifest.json", json.dumps({"schema": "chapter_artifact_archive_v3", "chapter_number": 1,
                "entries": [{"path": "50_workbench/candidate_blobs/browser-fixture.md", "member": "_audit/blobs/browser-fixture",
                             "sha256": sha256(historical).hexdigest()}]}))
            handle.writestr("_audit/blobs/browser-fixture", historical)
        service.import_project(config.path)
    if args.seed_review_fixture:
        from tests.test_story_architecture_v050 import seed_candidate
        folder = args.workspace / "review-fixture"
        if folder.exists():
            parser.error("Review fixture already exists; preserve it and choose a new workspace")
        config, root, _ = seed_candidate(folder, complete_human=False)
        atomic_write_text(root / "00_governance/execution_origin.json", json.dumps({
            "schema": "execution_origin_v1", "kind": "automated_rehearsal", "simulated_human": True,
            "run_id": args.run_id, "fixture": True, "formal_literary_eligible": False,
        }, ensure_ascii=False, indent=2))
        service.import_project(config.path)
    if args.seed_literary_fixture:
        from tests.test_fanfiction_literary_trial import seed_closed_literary_fixture
        folder = args.workspace / "literary-fixture"
        if folder.exists():
            parser.error("Literary fixture already exists; preserve earlier evidence")
        config, root = seed_closed_literary_fixture(folder)
        atomic_write_text(root / "00_governance/execution_origin.json", json.dumps({
            "schema": "execution_origin_v1", "kind": "automated_rehearsal", "simulated_human": True,
            "run_id": args.run_id, "fixture": True, "formal_literary_eligible": False,
        }, ensure_ascii=False, indent=2))
        service.import_project(config.path)
    if args.seed_fanfiction_fixture:
        from tests.test_current_planning_context import approved_project
        from longform_engine.semantic_protocols import build_semantic_document
        from tests.test_fanfiction_contracts import approve, semantic_claim
        import yaml

        from pytest import MonkeyPatch
        from tests.test_fanfiction_source_library import (
            approved_library_item, apply_project_canon, complete_project_pack, project_config,
        )

        baseline_folder = args.workspace / "fanfiction-approved-fixture"
        if baseline_folder.exists():
            parser.error("Approved baseline fixture exists; preserve it and choose a new workspace")
        baseline_folder.mkdir(parents=True)
        # This helper only changes the library environment. The source import,
        # evidence approval, coverage and Canon use the real domain validators.
        with MonkeyPatch.context() as environment:
            _work, item, _source = approved_library_item(baseline_folder, environment)
            baseline_config, baseline_root = project_config(baseline_folder)
            atomic_write_text(baseline_root / "00_governance/execution_origin.json", json.dumps({
                "schema": "execution_origin_v1", "kind": "automated_rehearsal", "simulated_human": True,
                "run_id": args.run_id, "fixture": True, "formal_literary_eligible": False}, ensure_ascii=False))
            complete_project_pack(baseline_config, baseline_folder, item)
            apply_project_canon(baseline_config, baseline_root, interpretation="林舟先确认自己的选择，只同意边界明确的验证。")
            baseline_config.data["project"]["title"] = "原著基线 · 正常批准协议夹具"
            atomic_write_text(baseline_config.path, yaml.safe_dump(baseline_config.data, allow_unicode=True, sort_keys=False))
            service.import_project(baseline_config.path)
        # Continue serving the same isolated source library for reference reads.
        os.environ["LONGFORM_SOURCE_LIBRARY"] = str((baseline_folder / "用户资料/原著资料库").resolve())
        for topology, title in (("fixed_host", "固定宿主"), ("fusion_world", "世界融合"), ("sequential_worlds", "顺序诸天")):
            folder = args.workspace / f"fanfiction-{topology}-fixture"
            if folder.exists():
                parser.error("Fanfiction reading fixture exists; preserve it and choose a new workspace")
            config, root = approved_project(folder)
            config.data["project"]["title"] = f"{title} · 资料阅读夹具"
            config.data["creation"]["mode"] = "fanfiction"
            config.data["fanfiction"] = {"continuity_mode": "crossover", "sources": [
                {"source_id": key, "title": label, "creator": "协议测试", "canon_cutoff": "合成资料第一卷",
                 "allowed_elements": ["characters", "abilities", "world"], "rights_status": "unverified",
                 "commercial_intent": False, "platform_policy_url": ""}
                for key, label in (("host", "山门世界"), ("guest", "来访世界"))]}
            atomic_write_text(config.path, yaml.safe_dump(config.data, allow_unicode=True, sort_keys=False))
            atomic_write_text(root / "00_governance/execution_origin.json", json.dumps({
                "schema": "execution_origin_v1", "kind": "automated_rehearsal", "simulated_human": True,
                "run_id": args.run_id, "fixture": True, "formal_literary_eligible": False}, ensure_ascii=False))
            for name, heading, body, extensions in (
                ("source_canon", "隔离夹具原著基线", "山门世界的术法需要消耗本地灵材；来访世界的火息不能替代修为。资料为原创合成夹具，不代表任何真实原著。", {"continuity_mode": "crossover", "source_ids": ["host", "guest"]}),
                ("fanfiction_bible", "隔离夹具跨界路线", "带入的火息须接触实物后激活；污染会误导，过用伤及经脉；在本地主场可被隔热封印反制。补充火息需要重新接触残留灵材。", {"crossover": {"topology": topology, "host_source_id": "host", "volume_ids": ["volume:001"]}, "adapter": {"source_id": "guest", "host_source_id": "host", "payload_kinds": ["ability"], "volume_ids": ["volume:001"], "from_chapter": 1, "to_chapter": 30}}),
            ):
                claims = []
                if topology == "sequential_worlds" and name == "fanfiction_bible":
                    for key, statement in (("injury", "换卷仍要处理右手伤势；正文解除后不继续限制。"),
                                           ("debt", "换卷承接欠同伴的一次搬运，不能用离开原世界消除。"),
                                           ("enemy", "计划让追踪者进入下一卷，只有正文发生才能计作敌对事实。")):
                        claim = semantic_claim(f"route:{key}", "跨卷延续后果", extensions={"volume_ids": ["volume:002"]})
                        claim["statement"] = statement
                        claims.append(claim)
                document = build_semantic_document(document_id=f"fixture:{topology}:{name}", document_type="同人资料阅读夹具", title=heading,
                    scope={"kind": "project", "project": root.name}, continuity="跨界资料展示", body=body,
                    claims=claims, evidence_references=[],
                    extensions={**extensions, "fixture": True, "validation_scope": "reading_only"})
                if claims:
                    document = approve(document)
                atomic_write_text(root / f"10_bible/fanfiction/{name}.json", json.dumps(document, ensure_ascii=False, indent=2))
            final_text = "# 第一章 界面夹具\n\n陆照试用火息后伤了右手，收起工具，请同伴代为搬运。\n\n这是一份合成阅读材料，不是小说试写。\n"
            atomic_write_text(root / "40_manuscript/final/ch001.md", final_text)
            if topology == "sequential_worlds":
                # Explicit synthetic preparation, not a simulated production pass.
                skeleton_path = root / "20_outline/volume_skeletons.json"
                skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
                first = skeleton["items"][0]
                skeleton["items"] = [{**first, "chapter_range": [1, 1], "title": "山门世界"},
                                     {**first, "volume_id": "volume:002", "order": 2, "chapter_range": [2, 30], "title": "来访世界"}]
                atomic_write_text(skeleton_path, json.dumps(skeleton, ensure_ascii=False))
                basis_path = root / "30_state/planning_basis.json"
                basis = json.loads(basis_path.read_text(encoding="utf-8"))
                for source in basis["source_files"]:
                    if source["path"] == "20_outline/volume_skeletons.json":
                        source["sha256"] = sha256(skeleton_path.read_bytes()).hexdigest()
                atomic_write_text(basis_path, json.dumps(basis, ensure_ascii=False))
                for number, changes in ((1, {"injury": "右手伤势尚未恢复。", "debt": "仍欠同伴一次搬运。"}),
                                        (2, {"injury": "右手伤势已恢复，行动限制解除。"})):
                    text = f"# 第 {number} 章 跨卷后果夹具\n\n" + "\n\n".join(changes.values()) + "\n\n这是合成协议材料，不是小说试写。\n"
                    final_path = root / f"40_manuscript/final/ch{number:03d}.md"
                    atomic_write_text(final_path, text)
                    atomic_write_text(root / f"30_state/semantic_ledger/ch{number:03d}.json", json.dumps({
                        "schema": "chapter_semantic_bundle_v1", "chapter_number": number, "canonical": True,
                        "source": {"path": final_path.relative_to(root).as_posix(), "sha256": sha256(final_path.read_bytes()).hexdigest()},
                        "world_deltas": [{"fact_id": f"route:{key}", "value": value,
                            "evidence": {"start": text.index(value), "end": text.index(value) + len(value), "excerpt": value}}
                            for key, value in changes.items()]}, ensure_ascii=False))
            service.import_project(config.path)
    server = WorkspaceStudioHTTPServer(service, port=0)
    args.evidence.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.evidence / "server.json", json.dumps({
        "pid": os.getpid(), "run_id": args.run_id, "engine_version": __version__, "bootstrap_url": server.bootstrap_url,
        "base_url": f"http://127.0.0.1:{server.port}", "workspace": str(args.workspace),
        "fixture": args.seed_reader_fixture or args.seed_review_fixture or args.seed_literary_fixture or args.seed_fanfiction_fixture or any(
            (args.workspace / f"{kind}-fixture/novel/project.yaml").is_file()
            for kind in ("reader", "review", "literary", "fanfiction-fixed_host", "fanfiction-fusion_world", "fanfiction-sequential_worlds")),
        "automated_rehearsal": True,
        "codex_runtime": service.agent_jobs.runtime_status(),
    }, ensure_ascii=False, indent=2))
    print(f"Studio test server listening on 127.0.0.1:{server.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
