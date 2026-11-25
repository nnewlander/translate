from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .generator import ExplorerInputError, validate_cli_output_path, write_json, write_site
from .candidate_fragments import apply_candidate_fragments
from .continuity import update_review_state
from .node_review_continuity import (
    _evaluate_node_review_state,
    _update_node_review_state,
)
from .node_review_v2_continuity import (
    evaluate_node_review_v2_state,
    update_node_review_v2_state,
)
from .navigation import apply_navigation
from .node_review import (
    _NodeReviewTrustPolicy,
    _PRODUCTION_NODE_REVIEW_TRUST_POLICY,
    _import_node_review_from_github,
    _project_legacy_node_review_v1,
    _verify_node_review_material_integrity,
    _verify_node_review_materials,
)
from .node_review_packet import (
    ALLOWED_RESULTS,
    build_node_review_packet,
    write_node_review_draft,
    write_node_review_packet,
)
from .node_review_definitions import (
    build_node_review_packets,
    project_node_reviews,
    stable_node_review_key,
    validate_node_review_definitions,
    write_node_review_packets,
)
from .node_review_pilot import preflight_node_review_anchor_candidate
from .review_mapping import import_review_mapping, publish_reviewed_explorer
from .source_scanner import attach_python_source_facts, scan_python_source_facts
from .view_definitions import (
    apply_view_definition_documents,
    discover_view_definition_paths,
)
from .review_source import (
    TRUSTED_COMMENT_ID,
    TRUSTED_ISSUE_NUMBER,
    TRUSTED_REPOSITORY,
    fetch_github_review_source,
    review_source_sha256,
    verify_trusted_review_source,
    _gh_api,
    _gh_preflight,
)


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExplorerInputError(f"Invalid JSON input: {path}") from error
    if not isinstance(value, dict):
        raise ExplorerInputError(f"Expected a JSON object: {path}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import and publish Architecture Explorer T3 review data")
    commands = parser.add_subparsers(dest="command", required=True)
    review = commands.add_parser("import-review", help="fetch the fixed review from GitHub API and create trusted materials")
    review.add_argument("--repository", type=Path, required=True)
    review.add_argument("--candidate", type=Path, required=True)
    review.add_argument("--github-repository", default=TRUSTED_REPOSITORY)
    review.add_argument("--issue", type=int, default=TRUSTED_ISSUE_NUMBER)
    review.add_argument("--comment-id", type=int, default=TRUSTED_COMMENT_ID)
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--source-output", type=Path, required=True)
    node_review = commands.add_parser(
        "import-node-review",
        help="fetch one pinned T8 Owner/admin review and create versioned materials",
    )
    node_review.add_argument("--repository", type=Path, required=True)
    node_review.add_argument("--explorer-data", type=Path, required=True)
    node_review.add_argument("--issue", type=int, required=True)
    node_review.add_argument("--comment-id", type=int, required=True)
    node_review.add_argument("--decision-output", type=Path, required=True)
    node_review.add_argument("--source-output", type=Path, required=True)
    packet = commands.add_parser(
        "prepare-node-review",
        help="generate the exact historical worker Decision v1 packet",
    )
    packet.add_argument("--repository", type=Path, required=True)
    packet.add_argument("--explorer-data", type=Path, required=True)
    packet.add_argument("--output-directory", type=Path, required=True)
    packet.add_argument("--result", choices=ALLOWED_RESULTS)
    packet.add_argument("--comment-id", type=int)
    packet.add_argument("--draft", action="store_true")
    packets = commands.add_parser(
        "prepare-node-reviews",
        help="generate isolated deterministic packets from multiple review definitions",
    )
    packets.add_argument("--repository", type=Path, required=True)
    packets.add_argument("--explorer-data", type=Path, required=True)
    packets.add_argument("--review-definitions", type=Path, required=True)
    packets.add_argument("--output-directory", type=Path, required=True)
    preflight = commands.add_parser(
        "preflight-node-review",
        help=(
            "read one final GitHub review and emit only an untrusted, "
            "uninstalled anchor candidate"
        ),
    )
    preflight.add_argument("--repository", type=Path, required=True)
    preflight.add_argument("--explorer-data", type=Path, required=True)
    preflight.add_argument("--issue", type=int, required=True)
    preflight.add_argument("--comment-id", type=int, required=True)
    preflight.add_argument("--source-output", type=Path, required=True)
    preflight.add_argument("--anchor-candidate-output", type=Path, required=True)
    update = commands.add_parser(
        "update-review-state",
        help="explicitly assess and atomically update the T5-Lite Git review state",
    )
    update.add_argument("--repository", type=Path, required=True)
    update.add_argument("--candidate", type=Path, required=True)
    update.add_argument("--review-mapping", type=Path, required=True)
    update.add_argument("--review-source", type=Path, required=True)
    update.add_argument("--review-state", type=Path, required=True)
    update.add_argument("--assessed-at", required=True)
    node_state = commands.add_parser(
        "update-node-review-state",
        help="explicitly assess and atomically update the T8 Node Review State",
    )
    node_state.add_argument("--repository", type=Path, required=True)
    node_state.add_argument("--explorer-data", type=Path, required=True)
    node_state.add_argument("--node-review-decision", type=Path, required=True)
    node_state.add_argument("--node-review-source", type=Path, required=True)
    node_state.add_argument("--node-review-state", type=Path, required=True)
    node_state.add_argument("--assessed-at", required=True)
    node_state.add_argument("--node-review-definition", type=Path)
    build = commands.add_parser("build", help="build the offline reviewed relationship site")
    build.add_argument("--repository", type=Path, required=True)
    build.add_argument("--snapshot", type=Path, required=True)
    build.add_argument("--mapping", type=Path, required=True)
    build.add_argument("--candidate", type=Path, required=True)
    build.add_argument("--review-mapping", type=Path, required=True)
    build.add_argument("--review-source", type=Path)
    build.add_argument("--review-state", type=Path)
    build.add_argument("--navigation", type=Path)
    build.add_argument("--candidate-fragment", type=Path, action="append", default=[])
    build.add_argument("--view-definition", type=Path, action="append", default=[])
    build.add_argument("--node-review-decision", type=Path)
    build.add_argument("--node-review-source", type=Path)
    build.add_argument("--node-review-state", type=Path)
    build.add_argument("--node-review-definitions", type=Path)
    build.add_argument(
        "--legacy-node-review-v1",
        action="store_true",
        help="explicitly enable the fixed Decision v1 compatibility adapter",
    )
    build.add_argument(
        "--node-review-material-directory", type=Path, action="append", default=[]
    )
    build.add_argument("--web-assets", type=Path, required=True)
    build.add_argument("--output-directory", type=Path, required=True)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    github_api: Callable[[str], dict[str, Any]] = _gh_api,
) -> int:
    return _main(
        argv,
        github_api=github_api,
        node_review_trust_policy=_PRODUCTION_NODE_REVIEW_TRUST_POLICY,
    )


def _main(
    argv: list[str] | None = None,
    *,
    github_api: Callable[[str], dict[str, Any]] = _gh_api,
    node_review_trust_policy: _NodeReviewTrustPolicy,
) -> int:
    args = _parser().parse_args(argv)
    if args.command == "prepare-node-reviews":
        definitions = validate_node_review_definitions(_object(args.review_definitions))
        relative_files = tuple(
            f"{stable_node_review_key(definition['object_key']).replace('::', '__')}/{filename}"
            for definition in definitions
            for filename in ("review-packet.json", "review-packet.md")
        )
        output = validate_cli_output_path(
            args.repository,
            args.output_directory,
            directory=True,
            inputs=(args.explorer_data, args.review_definitions),
            output_files=relative_files,
        )
        for definition in definitions:
            directory = output / stable_node_review_key(definition["object_key"]).replace("::", "__")
            if (directory / "github-comment.txt").exists() or (
                directory / "github-comment-draft.txt"
            ).exists():
                raise ExplorerInputError(
                    "Neutral multi-object Node Review output contains a stale comment"
                )
        packets_by_key = build_node_review_packets(
            _object(args.explorer_data), definitions
        )
        written = write_node_review_packets(packets_by_key, output)
        for key, (packet_path, report_path) in written.items():
            print(f"node_review_object={key}")
            print(f"node_review_packet={packet_path}")
            print(f"node_review_report={report_path}")
            print(f"node_review_packet_sha256={packets_by_key[key]['packet_sha256']}")
        return 0
    if args.command == "preflight-node-review":
        source_output = validate_cli_output_path(
            args.repository,
            args.source_output,
            directory=False,
            inputs=(args.explorer_data,),
        )
        candidate_output = validate_cli_output_path(
            args.repository,
            args.anchor_candidate_output,
            directory=False,
            inputs=(args.explorer_data,),
        )
        if source_output == candidate_output:
            raise ExplorerInputError(
                "Node Review preflight source and anchor candidate must be different files"
            )
        if github_api is _gh_api:
            _gh_preflight()
        source, candidate = preflight_node_review_anchor_candidate(
            _object(args.explorer_data),
            issue_number=args.issue,
            comment_id=args.comment_id,
            api=github_api,
        )
        write_json(source_output, source)
        write_json(candidate_output, candidate)
        print(f"node_review_preflight_source={source_output}")
        print(f"node_review_anchor_candidate={candidate_output}")
        print("node_review_anchor_trust=untrusted")
        print("node_review_anchor_installation=uninstalled")
        return 0
    if args.command == "prepare-node-review":
        if (args.result is None) != (args.comment_id is None):
            raise ExplorerInputError(
                "Node Review result and comment ID must be supplied together"
            )
        if args.comment_id is not None and args.comment_id <= 0:
            raise ExplorerInputError("Node Review comment ID must be a positive integer")
        if args.draft and args.result is not None:
            raise ExplorerInputError(
                "Node Review draft and final result are mutually exclusive"
            )
        output_files = ["review-packet.json", "review-packet.md"]
        if args.result is not None:
            output_files.append("github-comment.txt")
        if args.draft:
            output_files.append("github-comment-draft.txt")
        output = validate_cli_output_path(
            args.repository,
            args.output_directory,
            directory=True,
            inputs=(args.explorer_data,),
            output_files=tuple(output_files),
        )
        stale_final = (output / "github-comment.txt").exists()
        stale_draft = (output / "github-comment-draft.txt").exists()
        if args.result is None and stale_final:
            raise ExplorerInputError(
                "Neutral Node Review output contains a stale GitHub comment; "
                "use a clean output directory"
            )
        if not args.draft and stale_draft:
            raise ExplorerInputError(
                "Node Review output contains a stale GitHub comment draft; "
                "use a clean output directory"
            )
        if args.draft and stale_final:
            raise ExplorerInputError(
                "Node Review draft output contains a stale final GitHub comment; "
                "use a clean output directory"
            )
        packet = build_node_review_packet(_object(args.explorer_data))
        packet_path, report_path, comment_path = write_node_review_packet(
            packet,
            output,
            result=args.result,
            comment_id=args.comment_id,
        )
        draft_path = write_node_review_draft(packet, output) if args.draft else None
        print(f"node_review_packet={packet_path}")
        print(f"node_review_report={report_path}")
        if comment_path is not None:
            print(f"node_review_comment={comment_path}")
        if draft_path is not None:
            print(f"node_review_draft={draft_path}")
        print(f"node_review_packet_sha256={packet['packet_sha256']}")
        return 0
    if args.command == "import-review":
        output = validate_cli_output_path(
            args.repository, args.output, directory=False,
            inputs=(args.candidate,),
        )
        source_output = validate_cli_output_path(
            args.repository, args.source_output, directory=False, inputs=(args.candidate,)
        )
        if source_output == output:
            raise ExplorerInputError("Review mapping and source receipt outputs must be different files")
        source = fetch_github_review_source(
            args.github_repository,
            args.issue,
            args.comment_id,
        )
        source_errors = verify_trusted_review_source(source)
        if source_errors:
            raise ExplorerInputError(f"Live GitHub review does not match the pinned trusted source: {source_errors}")
        mapping = import_review_mapping(_object(args.candidate), source["comment"], source["permission"])
        if mapping["validation"]["status"] != "valid":
            raise ExplorerInputError(f"Live GitHub review decision is invalid: {mapping['validation']['errors']}")
        write_json(source_output, source)
        write_json(output, mapping)
        print(f"review_mapping={output}")
        print(f"review_source={source_output}")
        print(f"review_source_sha256={review_source_sha256(source)}")
        print(f"validation={mapping['validation']['status']}")
        return 0 if mapping["validation"]["status"] == "valid" else 2
    if args.command == "import-node-review":
        decision_output = validate_cli_output_path(
            args.repository,
            args.decision_output,
            directory=False,
            inputs=(args.explorer_data,),
        )
        source_output = validate_cli_output_path(
            args.repository,
            args.source_output,
            directory=False,
            inputs=(args.explorer_data,),
        )
        if source_output == decision_output:
            raise ExplorerInputError(
                "Node Review Decision and source receipt outputs must be different files"
            )
        if github_api is _gh_api:
            _gh_preflight()
        source, decision = _import_node_review_from_github(
            _object(args.explorer_data),
            issue_number=args.issue,
            comment_id=args.comment_id,
            trust_policy=node_review_trust_policy,
            api=github_api,
        )
        write_json(source_output, source)
        write_json(decision_output, decision)
        print(f"node_review_decision={decision_output}")
        print(f"node_review_source={source_output}")
        print(f"node_review_source_sha256={review_source_sha256(source)}")
        return 0
    if args.command == "update-review-state":
        state_path = validate_cli_output_path(
            args.repository,
            args.review_state,
            directory=False,
            inputs=(args.candidate, args.review_mapping, args.review_source),
        )
        state = update_review_state(
            args.repository,
            _object(args.candidate),
            _object(args.review_mapping),
            _object(args.review_source),
            state_path,
            assessed_at=args.assessed_at,
        )
        print(f"review_state={state_path}")
        print(f"review_status={state['status']}")
        print(f"source_continuity={state['continuity_status']}")
        return 0
    if args.command == "update-node-review-state":
        state_path = validate_cli_output_path(
            args.repository,
            args.node_review_state,
            directory=False,
            inputs=tuple(path for path in (
                args.explorer_data,
                args.node_review_decision,
                args.node_review_source,
                args.node_review_definition,
            ) if path is not None),
        )
        explorer = _object(args.explorer_data)
        try:
            explorer["source_facts"] = scan_python_source_facts(args.repository)
        except ExplorerInputError as error:
            explorer["_node_review_scan_error"] = (
                "missing" if "missing" in str(error).lower() else "parse-failure"
            )
        decision_input = _object(args.node_review_decision)
        source_input = _object(args.node_review_source)
        if decision_input.get("schema") == "architecture-explorer.node-review-decision.v2":
            if args.node_review_definition is None:
                raise ExplorerInputError(
                    "Node Review Decision v2 state update requires a Review Definition"
                )
            definitions = validate_node_review_definitions(
                _object(args.node_review_definition)
            )
            decision_key = stable_node_review_key(decision_input["object_key"])
            matches = [
                definition
                for definition in definitions
                if stable_node_review_key(definition["object_key"]) == decision_key
            ]
            if len(matches) != 1:
                raise ExplorerInputError(
                    "Node Review Decision v2 has no unique Review Definition"
                )
            state = update_node_review_v2_state(
                explorer,
                matches[0],
                decision_input,
                source_input,
                state_path,
                assessed_at=args.assessed_at,
                trust_policy=node_review_trust_policy,
            )
        else:
            state = _update_node_review_state(
                explorer,
                decision_input,
                source_input,
                state_path,
                assessed_at=args.assessed_at,
                trust_policy=node_review_trust_policy,
            )
        print(f"node_review_state={state_path}")
        print(f"node_review_status={state['state']}")
        print(f"node_review_continuity={state['continuity']}")
        return 0
    review_state_path = None
    if args.review_state is not None:
        review_state_path = (
            args.review_state
            if args.review_state.is_absolute()
            else args.repository / args.review_state
        ).resolve()
    node_review_state_path = None
    if args.node_review_state is not None:
        node_review_state_path = (
            args.node_review_state
            if args.node_review_state.is_absolute()
            else args.repository / args.node_review_state
        ).resolve()
    view_definition_paths = tuple(args.view_definition) or discover_view_definition_paths(
        args.repository
    )
    output = validate_cli_output_path(
        args.repository, args.output_directory, directory=True,
        inputs=tuple(
            path for path in (
                args.snapshot, args.mapping, args.candidate, args.review_mapping, args.review_source,
                args.navigation, args.web_assets, review_state_path,
                args.node_review_decision, args.node_review_source,
                node_review_state_path,
                 args.node_review_definitions,
                 *args.node_review_material_directory,
                 *view_definition_paths,
                *args.candidate_fragment
            ) if path is not None
        ),
        output_files=("explorer-data.json", "explorer-data.js", "index.html", "t4-state.js", "app.js", "styles.css"),
    )
    if args.node_review_definitions is not None and args.legacy_node_review_v1:
        raise ExplorerInputError(
            "Review Definitions and the Decision v1 compatibility adapter are mutually exclusive"
        )
    if args.node_review_definitions is None:
        if args.node_review_material_directory:
            raise ExplorerInputError(
                "Node Review material directories require Review Definitions"
            )
        legacy_material_requested = any(
            value is not None
            for value in (
                args.node_review_decision,
                args.node_review_source,
                args.node_review_state,
            )
        )
        if legacy_material_requested and not args.legacy_node_review_v1:
            raise ExplorerInputError(
                "Decision v1 materials require the explicit --legacy-node-review-v1 adapter"
            )
    if args.node_review_definitions is not None:
        if (args.node_review_decision is None) != (args.node_review_source is None):
            raise ExplorerInputError(
                "Node Review Decision and source receipt must be supplied together"
            )
        if args.node_review_state is not None and args.node_review_decision is None:
            raise ExplorerInputError(
                "Node Review State requires a Decision and source receipt"
            )
    elif args.legacy_node_review_v1:
        if (args.node_review_decision is None) != (args.node_review_source is None):
            raise ExplorerInputError(
                "Node Review Decision and source receipt must be supplied together"
            )
        if args.node_review_state is not None and args.node_review_decision is None:
            raise ExplorerInputError(
                "Node Review State requires a Decision and source receipt"
            )
    data = publish_reviewed_explorer(
        args.repository,
        args.snapshot,
        args.mapping,
        _object(args.candidate),
        _object(args.review_mapping),
        _object(args.review_source) if args.review_source is not None else None,
        review_state_path=review_state_path,
    )
    data = attach_python_source_facts(data, scan_python_source_facts(args.repository))
    data["generation_inputs"]["review_mapping"] = {
        "path": args.review_mapping.name,
        "sha256": hashlib.sha256(args.review_mapping.read_bytes()).hexdigest(),
    }
    if args.review_source is not None:
        data["generation_inputs"]["review_source"] = {
            "path": args.review_source.name,
            "sha256": hashlib.sha256(args.review_source.read_bytes()).hexdigest(),
        }
    if args.navigation is not None:
        data = apply_navigation(data, _object(args.navigation))
        data["generation_inputs"]["navigation"] = {
            "path": args.navigation.name,
            "sha256": hashlib.sha256(args.navigation.read_bytes()).hexdigest(),
        }
    if args.candidate_fragment:
        data = apply_candidate_fragments(
            data,
            [_object(path) for path in args.candidate_fragment],
        )
        data["generation_inputs"]["candidate_fragments"] = [
            {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in args.candidate_fragment
        ]
    if view_definition_paths:
        data = apply_view_definition_documents(
            data, [_object(path) for path in view_definition_paths]
        )
        data["generation_inputs"]["view_definitions"] = [
            {
                "path": (
                    path.relative_to(args.repository).as_posix()
                    if path.is_relative_to(args.repository)
                    else path.name
                ),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in view_definition_paths
        ]
    if args.node_review_definitions is not None:
        definitions = validate_node_review_definitions(
            _object(args.node_review_definitions)
        )
        materials_by_key: dict[str, dict[str, Any]] = {}
        material_inputs: list[tuple[Path, Path, Path | None]] = []
        if args.node_review_decision is not None and args.node_review_source is not None:
            material_inputs.append(
                (args.node_review_decision, args.node_review_source, node_review_state_path)
            )
        for directory in args.node_review_material_directory:
            decision_path = directory / "decision.json"
            source_path = directory / "source.json"
            state_path = directory / "state.json"
            if not decision_path.is_file() or not source_path.is_file():
                raise ExplorerInputError(
                    f"Node Review material directory is incomplete: {directory}"
                )
            material_inputs.append(
                (decision_path, source_path, state_path if state_path.is_file() else None)
            )
        material_generation_inputs: dict[str, dict[str, Any]] = {}
        for decision_path, source_path, state_path in material_inputs:
            decision_input = _object(decision_path)
            source_input = _object(source_path)
            decision_key = stable_node_review_key(
                {
                    "kind": decision_input.get("object_key", {}).get("kind"),
                    "node_id": decision_input.get("object_key", {}).get("node_id"),
                }
            )
            if decision_key in materials_by_key:
                raise ExplorerInputError(
                    f"Duplicate Node Review material object: {decision_key}"
                )
            if decision_input.get("schema") == "architecture-explorer.node-review-decision.v2":
                state = evaluate_node_review_v2_state(decision_input, state_path)
            else:
                state = _evaluate_node_review_state(decision_input, state_path)
                state["object_key"] = deepcopy(decision_input["object_key"])
            materials_by_key[decision_key] = {
                "decision": decision_input,
                "source": source_input,
                "state": state,
            }
            material_generation_inputs[decision_key] = {
                "decision": {
                    "path": decision_path.name,
                    "sha256": hashlib.sha256(decision_path.read_bytes()).hexdigest(),
                },
                "source": {
                    "path": source_path.name,
                    "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                },
            }
            if state_path is not None:
                material_generation_inputs[decision_key]["state"] = {
                    "path": state_path.name,
                    "sha256": hashlib.sha256(state_path.read_bytes()).hexdigest(),
                }
        data = project_node_reviews(
            data,
            definitions,
            materials_by_key,
            trust_policy=node_review_trust_policy,
        )
        data["generation_inputs"]["node_review_definitions"] = {
            "path": args.node_review_definitions.name,
            "sha256": hashlib.sha256(args.node_review_definitions.read_bytes()).hexdigest(),
        }
        if material_generation_inputs:
            data["generation_inputs"]["node_reviews"] = material_generation_inputs
    elif args.legacy_node_review_v1:
        decision = None
        review_source = None
        node_review_state = None
        if args.node_review_decision is not None and args.node_review_source is not None:
            decision, review_source = _verify_node_review_material_integrity(
                _object(args.node_review_decision),
                _object(args.node_review_source),
                trust_policy=node_review_trust_policy,
            )
            node_review_state = _evaluate_node_review_state(
                decision,
                node_review_state_path,
            )
            if (
                node_review_state["status"] == "current"
                and node_review_state["continuity"] == "exact-match"
            ):
                decision, review_source = _verify_node_review_materials(
                    data,
                    decision,
                    review_source,
                    trust_policy=node_review_trust_policy,
                )
        data = _project_legacy_node_review_v1(
            data,
            decision=decision,
            review_source=review_source,
            node_review_state=node_review_state,
        )
        if args.node_review_decision is not None and args.node_review_source is not None:
            data["generation_inputs"]["node_review"] = {
                "decision": {
                    "path": args.node_review_decision.name,
                    "sha256": hashlib.sha256(args.node_review_decision.read_bytes()).hexdigest(),
                },
                "source": {
                    "path": args.node_review_source.name,
                    "sha256": hashlib.sha256(args.node_review_source.read_bytes()).hexdigest(),
                },
            }
            if node_review_state_path is not None and node_review_state_path.is_file():
                data["generation_inputs"]["node_review"]["state"] = {
                    "path": node_review_state_path.name,
                    "sha256": hashlib.sha256(node_review_state_path.read_bytes()).hexdigest(),
                }
    write_site(data, output, args.web_assets)
    relation = data["architecture"]["relationships"][0]
    print(f"site={output / 'index.html'}")
    print(f"review_status={relation['review_status']}")
    print(f"source_continuity={relation['source_continuity']['status']}")
    print(f"implementation_revision={data['implementation']['revision']}")
    return 0


__all__ = ["main"]
