"""Unify two or more resumable runs into a single target run.

Merges checkpoints, summaries, manifests and API response files from the
source runs into a single destination run directory. The resulting run is
resume-compatible with ``scripts/collect_videos_from_terms_resumable.py``
because the fingerprint is preserved and ``seen_video_ids`` / ``per_term``
reflect the union of what was already collected.

Example:
    uv run python scripts/unify_runs.py \
        --source data/raw/runs/47c2dc39cc834344b6b9579b10395d9a \
        --source data/raw/runs/48eab29522d5490796e12fc21ec76ed6 \
        --target factcheck_unified_q2 \
        --storage-root ./data/raw
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

CHECKPOINT_NAME = "terms_collection_checkpoint.json"
SUMMARY_NAME = "terms_collection_summary.json"
MANIFEST_NAME = "manifest.jsonl"


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Unifica múltiplas runs (mesmo fingerprint) em um único diretório "
            "de run retomável."
        )
    )
    parser.add_argument(
        "--source",
        required=True,
        action="append",
        help="Diretório de run de origem (repita para múltiplas origens).",
    )
    parser.add_argument(
        "--target",
        required=True,
        help="Nome da run unificada (ex.: factcheck_unified_q2).",
    )
    parser.add_argument(
        "--storage-root",
        default="./data/raw",
        help="Raiz de armazenamento onde o diretório runs/ vive.",
    )
    parser.add_argument(
        "--keep-credits",
        action="store_true",
        help=(
            "Preservar credit_state somado das runs de origem. Por padrão "
            "o crédito é zerado para permitir retomar a coleta."
        ),
    )
    parser.add_argument(
        "--skip-data-copy",
        action="store_true",
        help="Não copiar os arquivos de resposta (apenas mesclar checkpoint).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Sobrescrever diretório de destino se ele já existir.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file as dict."""
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def merge_ordered_unique(lists: list[list[str]]) -> list[str]:
    """Concatenate lists preserving first-occurrence order and dropping duplicates."""
    merged: list[str] = []
    seen: set[str] = set()
    for items in lists:
        for value in items:
            if value in seen:
                continue
            seen.add(value)
            merged.append(value)
    return merged


def copy_endpoint_data(src_root: Path, dst_root: Path) -> tuple[int, int]:
    """Copy response files from source into destination, skipping pre-existing paths.

    Returns a tuple (copied, skipped) of file counts.
    """
    copied = 0
    skipped = 0
    for sub in ("endpoint=search", "endpoint=videos"):
        src = src_root / sub
        if not src.exists():
            continue
        for item in src.rglob("*"):
            if not item.is_file():
                continue
            relative = item.relative_to(src_root)
            destination = dst_root / relative
            if destination.exists():
                skipped += 1
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)
            copied += 1
    return copied, skipped


def merge_manifests(src_roots: list[Path], dst_root: Path) -> int:
    """Concatenate manifests from source runs into destination, sorted by timestamp."""
    entries: list[dict[str, Any]] = []
    for root in src_roots:
        manifest = root / MANIFEST_NAME
        if not manifest.exists():
            continue
        for raw_line in manifest.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            entries.append(json.loads(raw_line))
    entries.sort(key=lambda entry: entry.get("timestamp") or "")
    destination = dst_root / MANIFEST_NAME
    with destination.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return len(entries)


def pick_most_advanced(checkpoints: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the checkpoint that processed the most terms globally."""
    return max(
        checkpoints,
        key=lambda cp: (cp["next_term_index"], len(cp["seen_video_ids"])),
    )


def sum_credit_state(checkpoints: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum credit usage across all source runs."""
    total = 0
    per_endpoint: dict[str, int] = {}
    for checkpoint in checkpoints:
        state = checkpoint.get("credit_state", {})
        total += int(state.get("total_spent", 0))
        for endpoint, spent in state.get("per_endpoint", {}).items():
            per_endpoint[endpoint] = per_endpoint.get(endpoint, 0) + int(spent)
    return {"total_spent": total, "per_endpoint": per_endpoint}


def build_unified_checkpoint(
    *,
    run_id: str,
    checkpoints: list[dict[str, Any]],
    keep_credits: bool,
) -> dict[str, Any]:
    """Build the merged checkpoint payload."""
    fingerprints = {cp["config"]["fingerprint"] for cp in checkpoints}
    if len(fingerprints) != 1:
        raise SystemExit(
            f"Runs com fingerprints distintos ({fingerprints}). Unificação inviável."
        )

    advanced = pick_most_advanced(checkpoints)
    per_term = list(advanced["per_term"])
    seen_video_ids = merge_ordered_unique(
        [checkpoint["seen_video_ids"] for checkpoint in checkpoints]
    )

    credit_state = (
        sum_credit_state(checkpoints)
        if keep_credits
        else {"total_spent": 0, "per_endpoint": {}}
    )

    started_at = min(cp["started_at"] for cp in checkpoints)
    updated_at = max(
        cp.get("updated_at", cp["started_at"]) for cp in checkpoints
    )
    finished_at = max(
        (cp.get("finished_at") or cp.get("updated_at", cp["started_at"]))
        for cp in checkpoints
    )

    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": "stopped",
        "started_at": started_at,
        "updated_at": updated_at,
        "finished_at": finished_at,
        "config": dict(advanced["config"]),
        "credit_state": credit_state,
        "request_seq": 0,
        "seen_video_ids": seen_video_ids,
        "per_term": per_term,
        "active_term": None,
        "next_term_index": advanced["next_term_index"],
        "stop_reason": advanced.get("stop_reason"),
        "resume_supported": True,
    }


def build_summary(checkpoint: dict[str, Any], source_names: list[str]) -> dict[str, Any]:
    """Build a public summary from a merged checkpoint."""
    return {
        "run_id": checkpoint["run_id"],
        "started_at": checkpoint["started_at"],
        "finished_at": checkpoint["finished_at"],
        "updated_at": checkpoint["updated_at"],
        "duration_seconds": 0.0,
        "terms_total": checkpoint["config"]["terms_total"],
        "terms_processed": len(checkpoint["per_term"]),
        "limit_reached": False,
        "stop_reason": checkpoint["stop_reason"],
        "status": checkpoint["status"],
        "next_term_index": checkpoint["next_term_index"],
        "resume_supported": True,
        "checkpoint_file": CHECKPOINT_NAME,
        "unique_videos_global": len(checkpoint["seen_video_ids"]),
        "credit_state": checkpoint["credit_state"],
        "per_term": checkpoint["per_term"],
        "active_term": None,
        "summary_file": SUMMARY_NAME,
        "unified_from": source_names,
    }


def main() -> None:
    """Run the unification CLI."""
    args = parse_args()

    source_roots = [Path(src).expanduser().resolve() for src in args.source]
    if len(source_roots) < 2:
        raise SystemExit("Informe pelo menos duas runs de origem via --source.")

    storage_root = Path(args.storage_root).expanduser().resolve()
    target_root = storage_root / "runs" / args.target

    checkpoints: list[dict[str, Any]] = []
    for root in source_roots:
        checkpoint_path = root / CHECKPOINT_NAME
        if not checkpoint_path.exists():
            raise SystemExit(f"Checkpoint ausente em: {checkpoint_path}")
        checkpoints.append(load_json(checkpoint_path))

    if target_root.exists():
        if not args.overwrite:
            raise SystemExit(
                f"Diretório de destino já existe: {target_root}. "
                "Use --overwrite para sobrescrever."
            )
        shutil.rmtree(target_root)

    target_root.mkdir(parents=True)

    merged_checkpoint = build_unified_checkpoint(
        run_id=args.target,
        checkpoints=checkpoints,
        keep_credits=args.keep_credits,
    )

    copied = skipped = manifest_lines = 0
    if not args.skip_data_copy:
        for root in source_roots:
            c, s = copy_endpoint_data(root, target_root)
            copied += c
            skipped += s
        manifest_lines = merge_manifests(source_roots, target_root)

    atomic_write_json(target_root / CHECKPOINT_NAME, merged_checkpoint)
    atomic_write_json(
        target_root / SUMMARY_NAME,
        build_summary(merged_checkpoint, [root.name for root in source_roots]),
    )

    terms_total = merged_checkpoint["config"]["terms_total"]
    next_idx = merged_checkpoint["next_term_index"]
    remaining = terms_total - next_idx

    print("Unificação concluída.")
    print(f"  destino:              {target_root}")
    print(f"  fontes:               {[r.name for r in source_roots]}")
    print(f"  terms_processed:      {len(merged_checkpoint['per_term'])} / {terms_total}")
    print(f"  next_term_index:      {next_idx}  (restam {remaining} termos)")
    print(f"  unique_videos_global: {len(merged_checkpoint['seen_video_ids'])}")
    print(f"  credit_state:         {merged_checkpoint['credit_state']}")
    if not args.skip_data_copy:
        print(f"  arquivos copiados:    {copied}  (ignorados/duplicados: {skipped})")
        print(f"  entradas manifest:    {manifest_lines}")

    terms_file = merged_checkpoint["config"]["terms_file"]
    search_pages = merged_checkpoint["config"]["search_pages"]
    region_code = merged_checkpoint["config"]["region_code"]

    print()
    print("Para retomar a coleta a partir da run unificada:")
    print(
        "  uv run python scripts/collect_videos_from_terms_resumable.py \\\n"
        f"      --terms-file {terms_file} \\\n"
        f"      --run-id {args.target} \\\n"
        f"      --storage-root {args.storage_root} \\\n"
        f"      --search-pages {search_pages} \\\n"
        f"      --region-code {region_code}"
    )


if __name__ == "__main__":
    main()
