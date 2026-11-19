"""Export YRP internal pseudonymized tables and public dehydrated benchmark dataset.

Implements §6.3.6 and §8 of the YRP 6-month plan:
1) Consolidates runs from `data/raw/runs/` and takedown ledgers (`takedowns_ledger.jsonl`).
2) Generates **Internal Analytical Tables**:
   - `internal_videos.jsonl`: structured API metadata + `is_available` status.
   - `internal_comments.jsonl`: comment text and metrics with `authorChannelId` pseudonymized
     via `HMAC-SHA256` (`author_channel_id_hash`) using local salt (`YRP_PSEUDONYM_SALT`),
     stripping raw `authorChannelId` and `authorDisplayName` (LGPD / IRB compliance).
3) Generates **Public Dehydrated Release**:
   - `public_dehydrated_videos.jsonl` and `public_dehydrated_comments.jsonl`: contains
     exclusively IDs and researcher-generated provenance/annotations
     `(video_id, channel_id, comment_id, claim_cluster_id, query_id, arm, wave, split, labels)`,
     strictly excluding any video or comment where `is_available == False` (honoring content
     deletions and YouTube Terms of Service).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import hmac
import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PUBLIC_VIDEO_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "video_id",
        "channel_id",
        "comment_id",
        "claim_cluster_id",
        "query_id",
        "arm",
        "wave",
        "split",
        "annotation_labels",
    }
)

PUBLIC_COMMENT_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "comment_id",
        "parent_comment_id",
        "video_id",
        "channel_id",
        "claim_cluster_id",
        "query_id",
        "arm",
        "wave",
        "split",
        "annotation_labels",
    }
)


def iso_now() -> str:
    """Return current UTC timestamp in ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


def load_json_payload(path: Path) -> dict[str, Any]:
    """Load plain `.json` or compressed `.json.gz`."""
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def pseudonymize_author_id(author_channel_id: str | None, *, salt: str) -> str | None:
    """Pseudonymize a commenter's `authorChannelId` using HMAC-SHA256 with local salt."""
    if not author_channel_id or not str(author_channel_id).strip():
        return None
    if not salt:
        raise ValueError(
            "Salt de pseudonimização (YRP_PSEUDONYM_SALT / --salt) não pode ser vazio."
        )
    raw = str(author_channel_id).strip().encode("utf-8")
    return hmac.new(salt.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def load_takedown_ids(
    run_dirs: Sequence[Path],
    extra_ledger_paths: Sequence[Path] | None = None,
) -> tuple[set[str], set[str]]:
    """Return `(unavailable_video_ids, unavailable_channel_ids)` from all takedown ledgers."""
    unavailable_videos: set[str] = set()
    unavailable_channels: set[str] = set()

    ledger_files: list[Path] = []
    for run_dir in run_dirs:
        candidate = run_dir / "takedowns_ledger.jsonl"
        if candidate.exists():
            ledger_files.append(candidate)
    for extra in extra_ledger_paths or []:
        if extra.exists():
            ledger_files.append(extra)

    for ledger_path in ledger_files:
        for raw_line in ledger_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            row = json.loads(line)
            entity_type = str(row.get("entity_type") or "").strip()
            vid = str(row.get("video_id") or "").strip()
            cid = str(row.get("channel_id") or "").strip()
            eid = str(row.get("entity_id") or "").strip()
            if entity_type == "video" or vid:
                target = vid or eid
                if target:
                    unavailable_videos.add(target)
            elif entity_type == "channel" or cid:
                target = cid or eid
                if target:
                    unavailable_channels.add(target)

    return unavailable_videos, unavailable_channels


def load_annotations_map(annotations_file: Path | None) -> dict[str, dict[str, Any]]:
    """Load optional researcher annotations and split assignments indexed by `video_id`."""
    by_video: dict[str, dict[str, Any]] = {}
    if annotations_file is None or not annotations_file.exists():
        return by_video
    for raw_line in annotations_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        vid = str(row.get("video_id") or "").strip()
        if not vid:
            continue
        by_video[vid] = {
            "split": row.get("split"),
            "annotation_labels": row.get("annotation_labels") or row.get("labels"),
            "claim_cluster_id": row.get("claim_cluster_id"),
            "query_id": row.get("query_id"),
            "arm": row.get("arm"),
            "wave": row.get("wave"),
        }
    return by_video


def _extract_author_channel_id(snippet: Mapping[str, Any]) -> str | None:
    author_obj = snippet.get("authorChannelId")
    if isinstance(author_obj, Mapping):
        val = author_obj.get("value")
        return str(val).strip() if val else None
    if isinstance(author_obj, str) and author_obj.strip():
        return author_obj.strip()
    return None


def export_yrp_datasets(
    *,
    run_dirs: Sequence[Path],
    output_dir: Path,
    salt: str,
    extra_ledger_paths: Sequence[Path] | None = None,
    annotations_file: Path | None = None,
    default_wave: str = "T0",
    default_split: str | None = None,
) -> dict[str, Any]:
    """Consolidate runs into internal pseudonymized tables and public dehydrated files."""
    if not salt or not salt.strip():
        raise ValueError(
            "Defina YRP_PSEUDONYM_SALT ou passe --salt para pseudonimizar authorChannelId."
        )

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    unavailable_videos, unavailable_channels = load_takedown_ids(run_dirs, extra_ledger_paths)
    annotations_by_video = load_annotations_map(annotations_file)

    # 1) Index provenance from cluster_sightings.jsonl and channel_playlist_sightings.jsonl
    provenance_by_video: dict[str, dict[str, Any]] = {}
    video_metadata: dict[str, dict[str, Any]] = {}

    for run_dir in run_dirs:
        if not run_dir.exists():
            continue
        sightings_path = run_dir / "cluster_sightings.jsonl"
        if sightings_path.exists():
            for raw_line in sightings_path.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                row = json.loads(raw_line)
                vid = str(row.get("video_id") or "").strip()
                if not vid:
                    continue
                prov = provenance_by_video.setdefault(
                    vid,
                    {
                        "video_id": vid,
                        "channel_id": row.get("channel_id"),
                        "claim_cluster_id": row.get("claim_cluster_id"),
                        "query_id": row.get("query_id"),
                        "arm": row.get("arm") or "propagacao",
                        "wave": row.get("wave_type") or default_wave,
                    },
                )
                if not prov.get("channel_id") and row.get("channel_id"):
                    prov["channel_id"] = row.get("channel_id")
                if not prov.get("claim_cluster_id") and row.get("claim_cluster_id"):
                    prov["claim_cluster_id"] = row.get("claim_cluster_id")
                if not prov.get("query_id") and row.get("query_id"):
                    prov["query_id"] = row.get("query_id")

        pl_sightings_path = run_dir / "channel_playlist_sightings.jsonl"
        if pl_sightings_path.exists():
            for raw_line in pl_sightings_path.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                row = json.loads(raw_line)
                vid = str(row.get("video_id") or "").strip()
                if not vid:
                    continue
                provenance_by_video.setdefault(
                    vid,
                    {
                        "video_id": vid,
                        "channel_id": row.get("channel_id"),
                        "claim_cluster_id": None,
                        "query_id": None,
                        "arm": row.get("role") or "within_channel_control",
                        "wave": row.get("wave") or default_wave,
                    },
                )

        # 2) Read videos.list responses
        v_dir = run_dir / "endpoint=videos"
        if v_dir.exists():
            for page_file in sorted(v_dir.rglob("page_*.json*")):
                payload = load_json_payload(page_file)
                for item in payload.get("items", []):
                    if not isinstance(item, Mapping):
                        continue
                    vid = str(item.get("id") or "").strip()
                    if not vid:
                        continue
                    snippet = (
                        item.get("snippet") if isinstance(item.get("snippet"), Mapping) else {}
                    )
                    status_obj = (
                        item.get("status") if isinstance(item.get("status"), Mapping) else {}
                    )
                    stats = (
                        item.get("statistics")
                        if isinstance(item.get("statistics"), Mapping)
                        else {}
                    )
                    content_details = (
                        item.get("contentDetails")
                        if isinstance(item.get("contentDetails"), Mapping)
                        else {}
                    )
                    cid = str(snippet.get("channelId") or "").strip() or None
                    privacy = status_obj.get("privacyStatus")
                    upload_st = status_obj.get("uploadStatus")
                    if privacy == "private" or upload_st in {"rejected", "deleted"}:
                        unavailable_videos.add(vid)
                    if cid and cid in unavailable_channels:
                        unavailable_videos.add(vid)

                    video_metadata[vid] = {
                        "video_id": vid,
                        "channel_id": cid,
                        "published_at": snippet.get("publishedAt"),
                        "title": snippet.get("title"),
                        "description": snippet.get("description"),
                        "category_id": snippet.get("categoryId"),
                        "default_audio_language": snippet.get("defaultAudioLanguage"),
                        "default_language": snippet.get("defaultLanguage"),
                        "duration": content_details.get("duration"),
                        "dimension": content_details.get("dimension"),
                        "definition": content_details.get("definition"),
                        "caption": content_details.get("caption"),
                        "privacy_status": privacy,
                        "upload_status": upload_st,
                        "view_count": stats.get("viewCount"),
                        "like_count": stats.get("likeCount"),
                        "comment_count": stats.get("commentCount"),
                    }
                    prov = provenance_by_video.setdefault(
                        vid,
                        {
                            "video_id": vid,
                            "channel_id": cid,
                            "claim_cluster_id": None,
                            "query_id": None,
                            "arm": None,
                            "wave": default_wave,
                        },
                    )
                    if cid and not prov.get("channel_id"):
                        prov["channel_id"] = cid

    all_video_ids = sorted(set(provenance_by_video.keys()) | set(video_metadata.keys()))

    internal_videos_path = output_dir / "internal_videos.jsonl"
    public_videos_path = output_dir / "public_dehydrated_videos.jsonl"

    internal_videos_count = 0
    public_videos_count = 0
    excluded_takedown_videos_count = 0

    with (
        internal_videos_path.open("w", encoding="utf-8") as int_v_handle,
        public_videos_path.open("w", encoding="utf-8") as pub_v_handle,
    ):
        for vid in all_video_ids:
            prov = provenance_by_video.get(vid, {})
            meta = video_metadata.get(vid, {})
            ann = annotations_by_video.get(vid, {})
            cid = meta.get("channel_id") or prov.get("channel_id")

            is_available = vid not in unavailable_videos and (
                not cid or cid not in unavailable_channels
            )
            claim_cluster_id = ann.get("claim_cluster_id") or prov.get("claim_cluster_id")
            query_id = ann.get("query_id") or prov.get("query_id")
            arm = ann.get("arm") or prov.get("arm")
            wave = ann.get("wave") or prov.get("wave") or default_wave
            split = ann.get("split") if ann.get("split") is not None else default_split
            annotation_labels = ann.get("annotation_labels")

            internal_row = {
                "video_id": vid,
                "channel_id": cid,
                "is_available": is_available,
                "claim_cluster_id": claim_cluster_id,
                "query_id": query_id,
                "arm": arm,
                "wave": wave,
                "split": split,
                "annotation_labels": annotation_labels,
                **{k: v for k, v in meta.items() if k not in {"video_id", "channel_id"}},
            }
            int_v_handle.write(json.dumps(internal_row, ensure_ascii=False) + "\n")
            internal_videos_count += 1

            if not is_available:
                excluded_takedown_videos_count += 1
                continue

            public_row = {
                "video_id": vid,
                "channel_id": cid,
                "comment_id": None,
                "claim_cluster_id": claim_cluster_id,
                "query_id": query_id,
                "arm": arm,
                "wave": wave,
                "split": split,
                "annotation_labels": annotation_labels,
            }
            assert set(public_row.keys()) <= PUBLIC_VIDEO_ALLOWED_FIELDS
            pub_v_handle.write(json.dumps(public_row, ensure_ascii=False) + "\n")
            public_videos_count += 1

    # 3) Process commentThreads.list and comments.list
    internal_comments_path = output_dir / "internal_comments.jsonl"
    public_comments_path = output_dir / "public_dehydrated_comments.jsonl"

    internal_comments_count = 0
    public_comments_count = 0
    excluded_comments_count = 0
    seen_comment_ids: set[str] = set()
    parent_video_by_comment_id: dict[str, str] = {}

    def _write_comment_item(
        *,
        comment_id: str,
        video_id: str | None,
        parent_comment_id: str | None,
        snippet: Mapping[str, Any],
        int_handle: Any,
        pub_handle: Any,
    ) -> None:
        nonlocal internal_comments_count, public_comments_count, excluded_comments_count
        if not comment_id or comment_id in seen_comment_ids:
            return
        seen_comment_ids.add(comment_id)
        if video_id:
            parent_video_by_comment_id[comment_id] = video_id

        raw_author_id = _extract_author_channel_id(snippet)
        author_hash = pseudonymize_author_id(raw_author_id, salt=salt)

        prov = provenance_by_video.get(video_id or "", {})
        meta = video_metadata.get(video_id or "", {})
        ann = annotations_by_video.get(video_id or "", {})
        cid = meta.get("channel_id") or prov.get("channel_id")

        is_available = bool(
            video_id
            and video_id not in unavailable_videos
            and (not cid or cid not in unavailable_channels)
        )

        internal_comment = {
            "comment_id": comment_id,
            "parent_comment_id": parent_comment_id,
            "video_id": video_id,
            "channel_id": cid,
            "is_available": is_available,
            "author_channel_id_hash": author_hash,
            "text_display": snippet.get("textDisplay"),
            "text_original": snippet.get("textOriginal"),
            "like_count": snippet.get("likeCount"),
            "published_at": snippet.get("publishedAt"),
            "updated_at": snippet.get("updatedAt"),
            "claim_cluster_id": ann.get("claim_cluster_id") or prov.get("claim_cluster_id"),
            "query_id": ann.get("query_id") or prov.get("query_id"),
            "arm": ann.get("arm") or prov.get("arm"),
            "wave": ann.get("wave") or prov.get("wave") or default_wave,
            "split": ann.get("split") if ann.get("split") is not None else default_split,
            "annotation_labels": ann.get("annotation_labels"),
        }
        int_handle.write(json.dumps(internal_comment, ensure_ascii=False) + "\n")
        internal_comments_count += 1

        if not is_available:
            excluded_comments_count += 1
            return

        public_comment = {
            "comment_id": comment_id,
            "parent_comment_id": parent_comment_id,
            "video_id": video_id,
            "channel_id": cid,
            "claim_cluster_id": internal_comment["claim_cluster_id"],
            "query_id": internal_comment["query_id"],
            "arm": internal_comment["arm"],
            "wave": internal_comment["wave"],
            "split": internal_comment["split"],
            "annotation_labels": internal_comment["annotation_labels"],
        }
        assert set(public_comment.keys()) <= PUBLIC_COMMENT_ALLOWED_FIELDS
        pub_handle.write(json.dumps(public_comment, ensure_ascii=False) + "\n")
        public_comments_count += 1

    with (
        internal_comments_path.open("w", encoding="utf-8") as int_c_handle,
        public_comments_path.open("w", encoding="utf-8") as pub_c_handle,
    ):
        for run_dir in run_dirs:
            if not run_dir.exists():
                continue
            ct_dir = run_dir / "endpoint=commentThreads"
            if ct_dir.exists():
                for page_file in sorted(ct_dir.rglob("page_*.json*")):
                    payload = load_json_payload(page_file)
                    for item in payload.get("items", []):
                        if not isinstance(item, Mapping):
                            continue
                        thread_snippet = (
                            item.get("snippet") if isinstance(item.get("snippet"), Mapping) else {}
                        )
                        top_obj = (
                            thread_snippet.get("topLevelComment")
                            if isinstance(thread_snippet.get("topLevelComment"), Mapping)
                            else {}
                        )
                        top_id = str(top_obj.get("id") or item.get("id") or "").strip()
                        top_snippet = (
                            top_obj.get("snippet")
                            if isinstance(top_obj.get("snippet"), Mapping)
                            else {}
                        )
                        vid = (
                            str(
                                thread_snippet.get("videoId") or top_snippet.get("videoId") or ""
                            ).strip()
                            or None
                        )
                        _write_comment_item(
                            comment_id=top_id,
                            video_id=vid,
                            parent_comment_id=None,
                            snippet=top_snippet,
                            int_handle=int_c_handle,
                            pub_handle=pub_c_handle,
                        )
                        # Embedded replies in commentThreads
                        replies_obj = (
                            item.get("replies") if isinstance(item.get("replies"), Mapping) else {}
                        )
                        for reply in replies_obj.get("comments", []):
                            if not isinstance(reply, Mapping):
                                continue
                            rid = str(reply.get("id") or "").strip()
                            rsnippet = (
                                reply.get("snippet")
                                if isinstance(reply.get("snippet"), Mapping)
                                else {}
                            )
                            r_vid = str(rsnippet.get("videoId") or vid or "").strip() or None
                            _write_comment_item(
                                comment_id=rid,
                                video_id=r_vid,
                                parent_comment_id=str(rsnippet.get("parentId") or top_id).strip()
                                or None,
                                snippet=rsnippet,
                                int_handle=int_c_handle,
                                pub_handle=pub_c_handle,
                            )

            c_dir = run_dir / "endpoint=comments"
            if c_dir.exists():
                for page_file in sorted(c_dir.rglob("page_*.json*")):
                    payload = load_json_payload(page_file)
                    for item in payload.get("items", []):
                        if not isinstance(item, Mapping):
                            continue
                        cid_comment = str(item.get("id") or "").strip()
                        csnippet = (
                            item.get("snippet") if isinstance(item.get("snippet"), Mapping) else {}
                        )
                        parent_id = str(csnippet.get("parentId") or "").strip() or None
                        vid = str(csnippet.get("videoId") or "").strip() or (
                            parent_video_by_comment_id.get(parent_id or "") if parent_id else None
                        )
                        _write_comment_item(
                            comment_id=cid_comment,
                            video_id=vid,
                            parent_comment_id=parent_id,
                            snippet=csnippet,
                            int_handle=int_c_handle,
                            pub_handle=pub_c_handle,
                        )

    manifest_payload = {
        "schema_version": 1,
        "exported_at": iso_now(),
        "source_runs": [str(r) for r in run_dirs],
        "compliance": {
            "dehydrated_public_release": True,
            "takedowns_filtered_from_public": True,
            "author_channel_id_pseudonymized_hmac_sha256": True,
            "zero_media_downloaded": True,
        },
        "counts": {
            "internal_videos_total": internal_videos_count,
            "public_dehydrated_videos_exported": public_videos_count,
            "excluded_unavailable_videos": excluded_takedown_videos_count,
            "internal_comments_total": internal_comments_count,
            "public_dehydrated_comments_exported": public_comments_count,
            "excluded_unavailable_comments": excluded_comments_count,
        },
        "files": {
            "internal_videos": str(internal_videos_path),
            "internal_comments": str(internal_comments_path),
            "public_dehydrated_videos": str(public_videos_path),
            "public_dehydrated_comments": str(public_comments_path),
        },
    }
    manifest_path = output_dir / "export_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_payload


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Consolida runs em tabelas internas (authorChannelId pseudonimizado via HMAC-SHA256) "
            "e artefato público dehydrated (apenas IDs e rótulos, filtrando takedowns)."
        )
    )
    parser.add_argument(
        "--source-run",
        action="append",
        default=[],
        help="Diretório de run ou run_id em --storage-root (repita para múltiplas runs).",
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=Path("./data/raw"),
        help="Raiz onde runs/ está localizado (padrão: ./data/raw).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./data/processed/yrp_export"),
        help="Diretório de saída para tabelas internas e release dehydrated.",
    )
    parser.add_argument(
        "--salt",
        default=os.getenv("YRP_PSEUDONYM_SALT", ""),
        help="Salt local para HMAC-SHA256 de authorChannelId (ou YRP_PSEUDONYM_SALT no .env).",
    )
    parser.add_argument(
        "--takedowns-ledger",
        type=Path,
        action="append",
        default=[],
        help="Caminho adicional opcional para takedowns_ledger.jsonl.",
    )
    parser.add_argument(
        "--annotations-file",
        type=Path,
        default=None,
        help="Arquivo JSONL opcional mapeando video_id para split e annotation_labels.",
    )
    parser.add_argument(
        "--default-wave",
        default="T0",
        help="Onda padrão quando não especificada no sighting (padrão: T0).",
    )
    parser.add_argument(
        "--default-split",
        default=None,
        help="Split padrão opcional (ex.: train, val, test, holdout_m6).",
    )
    return parser.parse_args()


def main() -> None:
    """Run YRP dehydrated and internal export CLI."""
    args = parse_args()
    if not args.salt:
        raise SystemExit(
            "Salt de pseudonimização ausente. Informe --salt ou defina YRP_PSEUDONYM_SALT no .env."
        )

    storage_root = args.storage_root.expanduser().resolve()
    run_dirs: list[Path] = []
    if args.source_run:
        for item in args.source_run:
            p = Path(item).expanduser()
            if p.exists():
                run_dirs.append(p.resolve())
            else:
                candidate = storage_root / "runs" / item
                if candidate.exists():
                    run_dirs.append(candidate)
    else:
        runs_parent = storage_root / "runs"
        if runs_parent.exists():
            run_dirs = sorted(p for p in runs_parent.iterdir() if p.is_dir())

    if not run_dirs:
        raise SystemExit("Nenhuma run encontrada para exportação.")

    manifest = export_yrp_datasets(
        run_dirs=run_dirs,
        output_dir=args.output_dir,
        salt=args.salt,
        extra_ledger_paths=[p.expanduser().resolve() for p in args.takedowns_ledger],
        annotations_file=args.annotations_file.expanduser().resolve()
        if args.annotations_file
        else None,
        default_wave=args.default_wave,
        default_split=args.default_split,
    )
    counts = manifest["counts"]
    print("=== EXPORTAÇÃO YRP (INTERNA + DEHYDRATED) CONCLUÍDA ===")
    print(f"Vídeos internos                  : {counts['internal_videos_total']}")
    print(f"Vídeos públicos (dehydrated)     : {counts['public_dehydrated_videos_exported']}")
    print(f"Vídeos excluídos (takedown/ban)  : {counts['excluded_unavailable_videos']}")
    print(f"Comentários internos (HMAC)      : {counts['internal_comments_total']}")
    print(f"Comentários públicos (dehydrated): {counts['public_dehydrated_comments_exported']}")
    manifest_out = args.output_dir.expanduser().resolve() / "export_manifest.json"
    print(f"Manifesto                        : {manifest_out}")


if __name__ == "__main__":
    main()
