from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .generator import ExplorerInputError, validate_cli_output_path, write_json, write_site
from .candidate_fragments import apply_candidate_fragments
from .continuity import update_review_state
from .navigation import apply_navigation
from .node_review import (
    NODE_REVIEW_OBJECT_KEY,
    PRODUCTION_NODE_REVIEW_TRUST_POLICY,
    NodeReviewTrustPolicy,
    apply_node_review,
    import_node_review_from_github,
    verify_node_review_materials,
)
from .review_mapping import import_review_mapping, publish_reviewed_explorer
from .source_scanner import attach_python_source_facts, scan_python_source_facts
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
    build.add_argument("--node-review-decision", type=Path)
    build.add_argument("--node-review-source", type=Path)
    build.add_argument("--web-assets", type=Path, required=True)
    build.add_argument("--output-directory", type=Path, required=True)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    github_api: Callable[[str], dict[str, Any]] = _gh_api,
    node_review_trust_policy: NodeReviewTrustPolicy = PRODUCTION_NODE_REVIEW_TRUST_POLICY,
) -> int:
    args = _parser().parse_args(argv)
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
        source, decision = import_node_review_from_github(
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
    review_state_path = None
    if args.review_state is not None:
        review_state_path = (
            args.review_state
            if args.review_state.is_absolute()
            else args.repository / args.review_state
        ).resolve()
    output = validate_cli_output_path(
        args.repository, args.output_directory, directory=True,
        inputs=tuple(
            path for path in (
                args.snapshot, args.mapping, args.candidate, args.review_mapping, args.review_source,
                args.navigation, args.web_assets, review_state_path,
                args.node_review_decision, args.node_review_source,
                *args.candidate_fragment
            ) if path is not None
        ),
        output_files=("explorer-data.json", "explorer-data.js", "index.html", "t4-state.js", "app.js", "styles.css"),
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
    has_node_review_target = any(
        drilldown.get("parent_module_id") == NODE_REVIEW_OBJECT_KEY["parent_module_id"]
        and any(
            child.get("id") == NODE_REVIEW_OBJECT_KEY["node_id"]
            for child in drilldown.get("children", [])
        )
        for drilldown in data.get("navigation", {}).get("drilldowns", [])
    )
    if has_node_review_target:
        if (args.node_review_decision is None) != (args.node_review_source is None):
            raise ExplorerInputError(
                "Node Review Decision and source receipt must be supplied together"
            )
        verified_review = None
        if args.node_review_decision is not None and args.node_review_source is not None:
            verified_review = verify_node_review_materials(
                data,
                _object(args.node_review_decision),
                _object(args.node_review_source),
                trust_policy=node_review_trust_policy,
            )
        data = apply_node_review(data, verified_review=verified_review)
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
    write_site(data, output, args.web_assets)
    relation = data["architecture"]["relationships"][0]
    print(f"site={output / 'index.html'}")
    print(f"review_status={relation['review_status']}")
    print(f"source_continuity={relation['source_continuity']['status']}")
    print(f"implementation_revision={data['implementation']['revision']}")
    return 0


__all__ = ["main"]
