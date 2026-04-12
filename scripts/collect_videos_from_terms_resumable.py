"""Resumable bulk collector for YouTube videos from a terms file.

This script keeps the original `collect_videos_from_terms.py` untouched and adds
resume-safe execution when rerun with the same `--run-id`.

Flow:
1) fetch `search` one page at a time;
2) fetch `videos` one batch at a time;
3) persist an atomic checkpoint after every successful request.

All API responses are still persisted by youtube_data_pipeline under:
{storage_root}/runs/{run_id}/...
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from youtube_data_pipeline import YouTubeDataClient, YouTubeDataConfig

CHECKPOINT_SCHEMA_VERSION = 1
CHECKPOINT_NAME = "terms_collection_checkpoint.json"


def chunked(items: Sequence[str], size: int) -> Iterable[list[str]]:
    """Yield fixed-size chunks preserving input order."""
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def should_abort(result: dict[str, Any]) -> bool:
    """Return True when endpoint error policy requires stopping the run."""
    for error in result.get("errors", []):
        if error.get("action") == "stop":
            return True
    return False


def extract_video_ids(search_responses: Sequence[dict[str, Any]]) -> list[str]:
    """Extract video IDs from search responses preserving first occurrence order."""
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for response in search_responses:
        for item in response.get("items", []):
            video_id = item.get("id", {}).get("videoId")
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            ordered_ids.append(video_id)
    return ordered_ids


def merge_video_ids(existing: Sequence[str], new_ids: Sequence[str]) -> list[str]:
    """Merge ordered video IDs preserving the first appearance globally."""
    merged = list(existing)
    seen = set(existing)
    for video_id in new_ids:
        if video_id in seen:
            continue
        merged.append(video_id)
        seen.add(video_id)
    return merged


def read_terms_file(path: Path) -> list[str]:
    """Read search terms from file.

    Rules:
    - one term per line;
    - empty lines are ignored;
    - lines starting with '#' are ignored;
    - duplicate terms are removed preserving first occurrence order.
    """
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de termos não encontrado: {path}")

    ordered_terms: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        term = raw_line.strip()
        if not term or term.startswith("#"):
            continue
        if term in seen:
            continue
        seen.add(term)
        ordered_terms.append(term)
    return ordered_terms


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Coleta vídeos relacionados a uma lista de termos (um por linha), "
            "com retomada automática por checkpoint ao reutilizar o mesmo --run-id."
        )
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("YOUTUBE_API_KEY"),
        help="API key do YouTube (ou defina YOUTUBE_API_KEY).",
    )
    parser.add_argument(
        "--terms-file",
        required=True,
        help="Arquivo UTF-8 com um termo por linha.",
    )
    parser.add_argument(
        "--storage-root",
        default="./data/raw",
        help="Diretório para armazenar respostas e artefatos.",
    )
    parser.add_argument(
        "--log-path",
        default="youtube_data_pipeline.jsonl",
        help="Nome base do arquivo JSONL de logs dentro do diretório da run.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Identificador da execução (opcional). Reutilize para retomar uma run.",
    )
    parser.add_argument(
        "--compress-responses",
        action="store_true",
        help="Salvar respostas comprimidas (.json.gz).",
    )
    parser.add_argument(
        "--region-code",
        default="BR",
        help="Region code para busca e vídeos (padrão: BR).",
    )
    parser.add_argument(
        "--search-pages",
        type=int,
        default=5,
        help="Número máximo de páginas do endpoint search por termo.",
    )
    parser.add_argument(
        "--search-max-results",
        type=int,
        default=50,
        help="Quantidade de itens por página no search (máximo: 50).",
    )
    parser.add_argument(
        "--credit-limit",
        type=int,
        default=110_000,
        help="Limite total de créditos da execução atual.",
    )
    parser.add_argument(
        "--max-terms",
        type=int,
        default=None,
        help="Limita quantos termos do arquivo serão processados (útil para testes).",
    )
    parser.add_argument(
        "--summary-name",
        default="terms_collection_summary.json",
        help="Nome do arquivo JSON de resumo salvo em {storage_root}/runs/{run_id}/.",
    )
    return parser.parse_args()


@dataclass(slots=True)
class TermSummary:
    """Public summary entry for one processed term."""

    term_index: int
    term: str
    status: str = "completed"
    search_pages_collected: int = 0
    videos_found: int = 0
    videos_calls: int = 0
    new_unique_videos_global: int = 0

    @classmethod
    def from_dict(cls, payload: dict[str, Any], fallback_index: int) -> TermSummary:
        """Build a summary entry from checkpoint or legacy summary data."""
        return cls(
            term_index=int(payload.get("term_index", fallback_index)),
            term=str(payload["term"]),
            status=str(payload.get("status", "completed")),
            search_pages_collected=int(payload.get("search_pages_collected", 0)),
            videos_found=int(payload.get("videos_found", 0)),
            videos_calls=int(payload.get("videos_calls", 0)),
            new_unique_videos_global=int(payload.get("new_unique_videos_global", 0)),
        )


@dataclass(slots=True)
class ActiveTermState:
    """Checkpoint payload for an in-flight term."""

    term_index: int
    term: str
    completed_search_pages: list[int] = field(default_factory=list)
    search_complete: bool = False
    next_page_token: str | None = None
    term_video_ids: list[str] = field(default_factory=list)
    completed_video_batches: list[int] = field(default_factory=list)
    videos_calls: int = 0
    videos_found: int = 0
    new_unique_videos_global: int = 0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ActiveTermState:
        """Load checkpoint data for the current in-flight term."""
        return cls(
            term_index=int(payload["term_index"]),
            term=str(payload["term"]),
            completed_search_pages=[
                int(value) for value in payload.get("completed_search_pages", [])
            ],
            search_complete=bool(payload.get("search_complete", False)),
            next_page_token=payload.get("next_page_token"),
            term_video_ids=[str(value) for value in payload.get("term_video_ids", [])],
            completed_video_batches=[
                int(value) for value in payload.get("completed_video_batches", [])
            ],
            videos_calls=int(payload.get("videos_calls", 0)),
            videos_found=int(payload.get("videos_found", 0)),
            new_unique_videos_global=int(payload.get("new_unique_videos_global", 0)),
        )


@dataclass(slots=True)
class RunState:
    """Durable checkpoint state for the resumable collector."""

    run_id: str
    status: str
    started_at: str
    updated_at: str
    finished_at: str | None
    config: dict[str, Any]
    credit_state: dict[str, Any]
    request_seq: int
    seen_video_ids: list[str]
    per_term: list[TermSummary]
    active_term: ActiveTermState | None
    next_term_index: int
    stop_reason: str | None
    resume_supported: bool = True
    schema_version: int = CHECKPOINT_SCHEMA_VERSION

    @classmethod
    def fresh(cls, run_id: str, config: dict[str, Any]) -> RunState:
        """Create a brand new checkpoint state."""
        now = iso_now()
        return cls(
            run_id=run_id,
            status="running",
            started_at=now,
            updated_at=now,
            finished_at=None,
            config=config,
            credit_state={"total_spent": 0, "per_endpoint": {}},
            request_seq=0,
            seen_video_ids=[],
            per_term=[],
            active_term=None,
            next_term_index=0,
            stop_reason=None,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RunState:
        """Restore checkpoint state from JSON."""
        per_term = [
            TermSummary.from_dict(item, fallback_index=index)
            for index, item in enumerate(payload.get("per_term", []))
        ]
        active_payload = payload.get("active_term")
        return cls(
            run_id=str(payload["run_id"]),
            status=str(payload.get("status", "running")),
            started_at=str(payload["started_at"]),
            updated_at=str(payload.get("updated_at", payload["started_at"])),
            finished_at=payload.get("finished_at"),
            config=dict(payload.get("config", {})),
            credit_state=dict(payload.get("credit_state", {"total_spent": 0, "per_endpoint": {}})),
            request_seq=int(payload.get("request_seq", 0)),
            seen_video_ids=[str(value) for value in payload.get("seen_video_ids", [])],
            per_term=per_term,
            active_term=ActiveTermState.from_dict(active_payload) if active_payload else None,
            next_term_index=int(payload.get("next_term_index", len(per_term))),
            stop_reason=payload.get("stop_reason"),
            resume_supported=bool(payload.get("resume_supported", True)),
            schema_version=int(payload.get("schema_version", CHECKPOINT_SCHEMA_VERSION)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize checkpoint state to JSON."""
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "config": self.config,
            "credit_state": self.credit_state,
            "request_seq": self.request_seq,
            "seen_video_ids": self.seen_video_ids,
            "per_term": [asdict(item) for item in self.per_term],
            "active_term": asdict(self.active_term) if self.active_term is not None else None,
            "next_term_index": self.next_term_index,
            "stop_reason": self.stop_reason,
            "resume_supported": self.resume_supported,
        }


def iso_now() -> str:
    """Return the current UTC timestamp as ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


def validate_args(args: argparse.Namespace) -> None:
    """Validate CLI argument ranges."""
    if not args.api_key:
        raise SystemExit("API key não informada. Use --api-key ou YOUTUBE_API_KEY.")
    if args.search_max_results < 1 or args.search_max_results > 50:
        raise SystemExit("--search-max-results deve estar entre 1 e 50.")
    if args.search_pages < 1:
        raise SystemExit("--search-pages deve ser >= 1.")
    if args.credit_limit < 1:
        raise SystemExit("--credit-limit deve ser >= 1.")
    if args.max_terms is not None and args.max_terms < 1:
        raise SystemExit("--max-terms deve ser >= 1 quando informado.")


def compute_terms_hash(terms: Sequence[str]) -> str:
    """Hash the effective term list after dedupe and max-terms filtering."""
    digest = hashlib.sha256()
    for term in terms:
        digest.update(term.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def build_config_metadata(args: argparse.Namespace, terms: Sequence[str]) -> dict[str, Any]:
    """Build resumable config metadata and fingerprint."""
    terms_hash = compute_terms_hash(terms)
    fingerprint_payload = {
        "terms_hash": terms_hash,
        "region_code": args.region_code,
        "search_pages": args.search_pages,
        "search_max_results": args.search_max_results,
        "compress_responses": args.compress_responses,
        "summary_name": args.summary_name,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "terms_file": str(Path(args.terms_file).expanduser().resolve()),
        "terms_hash": terms_hash,
        "terms_total": len(terms),
        "region_code": args.region_code,
        "search_pages": args.search_pages,
        "search_max_results": args.search_max_results,
        "compress_responses": args.compress_responses,
        "summary_name": args.summary_name,
        "fingerprint": fingerprint,
    }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically to avoid partial checkpoints on interruption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def checkpoint_path_for(run_root: Path) -> Path:
    """Return the checkpoint path inside the run directory."""
    return run_root / CHECKPOINT_NAME


def summary_path_for(run_root: Path, summary_name: str) -> Path:
    """Return the summary path inside the run directory."""
    return run_root / summary_name


def manifest_path_for(run_root: Path) -> Path:
    """Return the manifest path inside the run directory."""
    return run_root / "manifest.jsonl"


def build_summary_payload(state: RunState, *, summary_name: str) -> dict[str, Any]:
    """Build the public run summary from checkpoint state."""
    end_time = state.finished_at or state.updated_at
    started_at = datetime.fromisoformat(state.started_at)
    ended_at = datetime.fromisoformat(end_time)
    return {
        "run_id": state.run_id,
        "started_at": state.started_at,
        "finished_at": state.finished_at,
        "updated_at": state.updated_at,
        "duration_seconds": round((ended_at - started_at).total_seconds(), 2),
        "terms_total": state.config["terms_total"],
        "terms_processed": len(state.per_term),
        "limit_reached": state.stop_reason == "credit_limit",
        "stop_reason": state.stop_reason,
        "status": state.status,
        "next_term_index": state.next_term_index,
        "resume_supported": state.resume_supported,
        "checkpoint_file": CHECKPOINT_NAME,
        "unique_videos_global": len(state.seen_video_ids),
        "credit_state": state.credit_state,
        "per_term": [asdict(item) for item in state.per_term],
        "active_term": asdict(state.active_term) if state.active_term is not None else None,
        "summary_file": summary_name,
    }


def persist_state(
    state: RunState,
    client: YouTubeDataClient,
    *,
    run_root: Path,
    summary_name: str,
) -> tuple[Path, Path]:
    """Persist checkpoint and public summary atomically."""
    state.credit_state = client.credit_monitor.snapshot()
    state.request_seq = client.request_seq
    state.updated_at = iso_now()
    checkpoint_path = checkpoint_path_for(run_root)
    summary_path = summary_path_for(run_root, summary_name)
    atomic_write_json(checkpoint_path, state.to_dict())
    atomic_write_json(summary_path, build_summary_payload(state, summary_name=summary_name))
    return checkpoint_path, summary_path


def expected_videos_calls(videos_found: int) -> int:
    """Return the number of `videos.list` calls required for the term."""
    if videos_found <= 0:
        return 0
    return math.ceil(videos_found / 50)


def extract_request_seq(request_id: str | None) -> int:
    """Extract the numeric request sequence suffix from a request id."""
    if not request_id:
        return 0
    tail = request_id.rsplit("-", maxsplit=1)[-1]
    try:
        return int(tail)
    except ValueError:
        return 0


def read_last_manifest_entry(path: Path) -> dict[str, Any] | None:
    """Return the last manifest line, if the file exists."""
    if not path.exists():
        return None
    last_line: str | None = None
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            if raw_line.strip():
                last_line = raw_line
    if last_line is None:
        return None
    return json.loads(last_line)


def origin_hash(origin: str) -> str:
    """Reproduce the storage origin hash used by ResponseStorage."""
    return hashlib.sha256(origin.encode("utf-8")).hexdigest()[:12]


def build_search_origin(term: str, page_index: int) -> str:
    """Build a deterministic origin string for one search page."""
    return json.dumps(
        {"phase": "search_page", "term": term, "page": page_index},
        ensure_ascii=False,
        sort_keys=True,
    )


def build_videos_origin(term: str, batch_index: int) -> str:
    """Build a deterministic origin string for one videos batch."""
    return json.dumps(
        {"phase": "videos_batch", "term": term, "batch": batch_index},
        ensure_ascii=False,
        sort_keys=True,
    )


def load_json_payload(path: Path) -> dict[str, Any]:
    """Load either plain JSON or gzipped JSON based on the file suffix."""
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def find_saved_page(run_root: Path, *, endpoint: str, origin: str, page_index: int) -> Path:
    """Locate a saved response page for a given endpoint/origin/page combination."""
    base_dir = run_root / f"endpoint={endpoint}" / f"origin=q:{origin_hash(origin)}"
    if endpoint != "search":
        base_dir = run_root / f"endpoint={endpoint}" / f"origin=explicit:{origin_hash(origin)}"
    for suffix in (".json", ".json.gz"):
        candidate = base_dir / f"page_{page_index:03d}{suffix}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Arquivo salvo não encontrado para {endpoint=} {page_index=} {origin=}"
    )


def restore_client_state(
    client: YouTubeDataClient,
    state: RunState,
    *,
    manifest_entry: dict[str, Any] | None,
) -> None:
    """Restore credit counters and request sequencing before resuming."""
    credit_state = state.credit_state
    if manifest_entry is not None and not credit_state.get("per_endpoint"):
        credit_state = {
            "total_spent": int(manifest_entry.get("cumulative_credits", 0)),
            "per_endpoint": dict(manifest_entry.get("per_endpoint", {})),
        }
        state.credit_state = credit_state

    client.credit_monitor.total_spent = int(credit_state.get("total_spent", 0))
    client.credit_monitor.per_endpoint = dict(credit_state.get("per_endpoint", {}))

    manifest_seq = extract_request_seq(manifest_entry.get("request_id")) if manifest_entry else 0
    client.request_seq = max(state.request_seq, manifest_seq)
    state.request_seq = client.request_seq


def validate_checkpoint_config(state: RunState, config: dict[str, Any]) -> None:
    """Ensure a resumed run uses the same effective request-shaping config."""
    current_fingerprint = config["fingerprint"]
    stored_fingerprint = state.config.get("fingerprint")
    if stored_fingerprint == current_fingerprint:
        return
    raise SystemExit(
        "Configuração incompatível para retomar esta run. "
        "Use o mesmo terms file efetivo, region code, paginação, compressão e summary name."
    )


def reconstruct_legacy_seen_video_ids(
    run_root: Path,
    per_term: Sequence[TermSummary],
) -> list[str]:
    """Rebuild the global unique video ids set from legacy search response files."""
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for item in per_term:
        if item.search_pages_collected <= 0:
            continue
        responses: list[dict[str, Any]] = []
        for page_index in range(1, item.search_pages_collected + 1):
            path = find_saved_page(
                run_root,
                endpoint="search",
                origin=item.term,
                page_index=page_index,
            )
            responses.append(load_json_payload(path))
        for video_id in extract_video_ids(responses):
            if video_id in seen:
                continue
            seen.add(video_id)
            ordered_ids.append(video_id)
    return ordered_ids


def bootstrap_legacy_state(
    *,
    run_root: Path,
    run_id: str,
    summary_name: str,
    terms: Sequence[str],
    config: dict[str, Any],
    manifest_entry: dict[str, Any] | None,
) -> RunState:
    """Convert a legacy summary/manifest into the new checkpoint format."""
    summary_path = summary_path_for(run_root, summary_name)
    if not summary_path.exists():
        raise SystemExit(
            "Não foi possível retomar a run: checkpoint ausente e summary legado não encontrado."
        )

    summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if str(summary_payload.get("run_id")) != run_id:
        raise SystemExit("O summary legado não corresponde ao --run-id informado.")

    per_term = [
        TermSummary.from_dict(item, fallback_index=index)
        for index, item in enumerate(summary_payload.get("per_term", []))
    ]
    if len(per_term) > len(terms):
        raise SystemExit("O terms file atual tem menos termos do que a run legada registrada.")
    for index, item in enumerate(per_term):
        if terms[index] != item.term:
            raise SystemExit(
                "O terms file atual não corresponde ao histórico da run legada. "
                f"Esperado {item.term!r} na posição {index + 1}."
            )
    for item in per_term:
        if item.videos_calls != expected_videos_calls(item.videos_found):
            raise SystemExit(
                "Legacy partial term cannot be resumed exactly. "
                f"Termo incompatível: {item.term!r}."
            )

    seen_video_ids = reconstruct_legacy_seen_video_ids(run_root, per_term)
    credit_state = dict(summary_payload.get("credit_state", {}))
    if manifest_entry is not None:
        credit_state = {
            "total_spent": int(
                manifest_entry.get(
                    "cumulative_credits",
                    credit_state.get("total_spent", 0),
                )
            ),
            "per_endpoint": dict(
                manifest_entry.get("per_endpoint", credit_state.get("per_endpoint", {}))
            ),
        }

    return RunState(
        run_id=run_id,
        status="stopped",
        started_at=str(summary_payload.get("started_at", iso_now())),
        updated_at=iso_now(),
        finished_at=summary_payload.get("finished_at"),
        config=config,
        credit_state=credit_state,
        request_seq=extract_request_seq(manifest_entry.get("request_id")) if manifest_entry else 0,
        seen_video_ids=seen_video_ids,
        per_term=per_term,
        active_term=None,
        next_term_index=len(per_term),
        stop_reason=summary_payload.get("stop_reason"),
    )


def load_or_bootstrap_state(
    *,
    client: YouTubeDataClient,
    args: argparse.Namespace,
    terms: Sequence[str],
) -> RunState:
    """Load an existing checkpoint, bootstrap a legacy run, or create a fresh state."""
    config = build_config_metadata(args, terms)
    run_root = Path(args.storage_root).expanduser().resolve() / "runs" / client.run_id
    run_root.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_path_for(run_root)
    manifest_entry = read_last_manifest_entry(manifest_path_for(run_root))

    if checkpoint_path.exists():
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        state = RunState.from_dict(payload)
        validate_checkpoint_config(state, config)
        state.config = config
        restore_client_state(client, state, manifest_entry=manifest_entry)
        return state

    summary_path = summary_path_for(run_root, args.summary_name)
    if summary_path.exists() and manifest_entry is not None:
        state = bootstrap_legacy_state(
            run_root=run_root,
            run_id=client.run_id,
            summary_name=args.summary_name,
            terms=terms,
            config=config,
            manifest_entry=manifest_entry,
        )
        restore_client_state(client, state, manifest_entry=manifest_entry)
        persist_state(state, client, run_root=run_root, summary_name=args.summary_name)
        return state

    state = RunState.fresh(client.run_id, config)
    restore_client_state(client, state, manifest_entry=manifest_entry)
    return state


def ensure_active_term(state: RunState, term_index: int, term: str) -> ActiveTermState:
    """Return the active term checkpoint, creating it if necessary."""
    if state.active_term is not None:
        if state.active_term.term_index != term_index or state.active_term.term != term:
            raise SystemExit(
                "Checkpoint inconsistente: active_term não corresponde ao próximo termo."
            )
        return state.active_term

    state.active_term = ActiveTermState(term_index=term_index, term=term)
    state.next_term_index = term_index
    return state.active_term


def finalize_search_phase(
    active_term: ActiveTermState,
    *,
    seen_video_ids: set[str],
    state: RunState,
) -> None:
    """Mark search as complete and update global unique ids only once."""
    if active_term.search_complete and active_term.videos_found > 0:
        return

    active_term.search_complete = True
    active_term.videos_found = len(active_term.term_video_ids)
    new_unique_ids = 0
    for video_id in active_term.term_video_ids:
        if video_id in seen_video_ids:
            continue
        seen_video_ids.add(video_id)
        new_unique_ids += 1
    active_term.new_unique_videos_global = new_unique_ids
    state.seen_video_ids = list(seen_video_ids)


def mark_term_completed(state: RunState, active_term: ActiveTermState) -> TermSummary:
    """Move an active term into the public per-term summary."""
    summary = TermSummary(
        term_index=active_term.term_index,
        term=active_term.term,
        search_pages_collected=len(active_term.completed_search_pages),
        videos_found=active_term.videos_found,
        videos_calls=len(active_term.completed_video_batches),
        new_unique_videos_global=active_term.new_unique_videos_global,
    )
    state.per_term.append(summary)
    state.active_term = None
    state.next_term_index = active_term.term_index + 1
    return summary


def build_search_params(
    args: argparse.Namespace,
    term: str,
    page_token: str | None,
) -> dict[str, Any]:
    """Build request params for one search page."""
    params: dict[str, Any] = {
        "part": "snippet",
        "q": term,
        "type": "video",
        "maxResults": args.search_max_results,
        "regionCode": args.region_code,
    }
    if page_token:
        params["pageToken"] = page_token
    return params


def build_videos_params(args: argparse.Namespace, batch: Sequence[str]) -> dict[str, Any]:
    """Build request params for one videos batch."""
    return {
        "part": "snippet,statistics,contentDetails",
        "id": ",".join(batch),
        "maxResults": 50,
        "regionCode": args.region_code,
    }


def collect_videos_from_terms_resumable(
    client: YouTubeDataClient,
    args: argparse.Namespace,
    terms: Sequence[str],
) -> tuple[dict[str, Any], Path]:
    """Execute the resumable bulk collection and persist checkpoint/summary."""
    run_root = Path(args.storage_root).expanduser().resolve() / "runs" / client.run_id
    run_root.mkdir(parents=True, exist_ok=True)

    state = load_or_bootstrap_state(client=client, args=args, terms=terms)
    state.status = "running"
    state.stop_reason = None
    state.finished_at = None
    persist_state(state, client, run_root=run_root, summary_name=args.summary_name)

    if state.next_term_index >= len(terms) and state.active_term is None:
        state.status = "completed"
        state.finished_at = iso_now()
        _, summary_path = persist_state(
            state,
            client,
            run_root=run_root,
            summary_name=args.summary_name,
        )
        return build_summary_payload(state, summary_name=args.summary_name), summary_path

    seen_video_ids = set(state.seen_video_ids)
    stop_reason: str | None = None

    for term_index in range(state.next_term_index, len(terms)):
        term = terms[term_index]
        active_term = ensure_active_term(state, term_index, term)
        persist_state(state, client, run_root=run_root, summary_name=args.summary_name)

        if not active_term.search_complete:
            page_index = len(active_term.completed_search_pages) + 1
            page_token = active_term.next_page_token

            while page_index <= args.search_pages:
                search_result = client.fetch(
                    "search",
                    build_search_params(args, term, page_token),
                    max_pages=1,
                    credit_limit=args.credit_limit,
                    origin=build_search_origin(term, page_index),
                )
                responses = search_result.get("responses", [])
                if responses:
                    # `client.fetch(..., max_pages=1)` may still stop after the first saved
                    # page because the internal paginator checks whether a next page exists.
                    response = responses[0]
                    active_term.completed_search_pages.append(page_index)
                    active_term.term_video_ids = merge_video_ids(
                        active_term.term_video_ids,
                        extract_video_ids([response]),
                    )
                    page_token = response.get("nextPageToken")
                    active_term.next_page_token = page_token

                if should_abort(search_result):
                    stop_reason = "stop_action_on_search"
                    persist_state(state, client, run_root=run_root, summary_name=args.summary_name)
                    break
                if search_result.get("limit_reached"):
                    stop_reason = "credit_limit"
                    persist_state(state, client, run_root=run_root, summary_name=args.summary_name)
                    break

                if not responses:
                    raise RuntimeError("search fetch retornou sem resposta e sem motivo de parada.")

                if page_token is None or page_index >= args.search_pages:
                    finalize_search_phase(active_term, seen_video_ids=seen_video_ids, state=state)
                    persist_state(state, client, run_root=run_root, summary_name=args.summary_name)
                    break

                persist_state(state, client, run_root=run_root, summary_name=args.summary_name)
                page_index += 1

            if stop_reason is not None:
                break

            if not active_term.search_complete:
                finalize_search_phase(active_term, seen_video_ids=seen_video_ids, state=state)
                persist_state(state, client, run_root=run_root, summary_name=args.summary_name)

        completed_batches = set(active_term.completed_video_batches)
        for batch_index, batch in enumerate(chunked(active_term.term_video_ids, 50), start=1):
            if batch_index in completed_batches:
                continue
            videos_result = client.fetch(
                "videos",
                build_videos_params(args, batch),
                max_pages=1,
                credit_limit=args.credit_limit,
                origin=build_videos_origin(term, batch_index),
            )
            if videos_result.get("limit_reached"):
                stop_reason = "credit_limit"
                break
            if should_abort(videos_result):
                stop_reason = "stop_action_on_videos"
                break

            active_term.completed_video_batches.append(batch_index)
            active_term.completed_video_batches.sort()
            active_term.videos_calls = len(active_term.completed_video_batches)
            persist_state(state, client, run_root=run_root, summary_name=args.summary_name)

        if stop_reason is not None:
            break

        summary_entry = mark_term_completed(state, active_term)
        persist_state(state, client, run_root=run_root, summary_name=args.summary_name)
        print(
            f"[{term_index + 1}/{len(terms)}] term={term!r} "
            f"videos_found={summary_entry.videos_found} "
            f"unique_global={len(seen_video_ids)}"
        )

    if stop_reason is None and state.active_term is None and state.next_term_index >= len(terms):
        state.status = "completed"
        state.stop_reason = None
    else:
        state.status = "stopped"
        state.stop_reason = stop_reason or state.stop_reason or "interrupted"
    state.finished_at = iso_now()
    _, summary_path = persist_state(
        state,
        client,
        run_root=run_root,
        summary_name=args.summary_name,
    )
    return build_summary_payload(state, summary_name=args.summary_name), summary_path


def print_summary(summary_payload: dict[str, Any], summary_path: Path) -> None:
    """Print a concise terminal summary for the completed execution."""
    print("Bulk collection finished")
    print(f"Run ID: {summary_payload['run_id']}")
    print(f"Status: {summary_payload['status']}")
    print(f"Terms processed: {summary_payload['terms_processed']}/{summary_payload['terms_total']}")
    print(f"Unique videos (global): {summary_payload['unique_videos_global']}")
    print(f"Total spent: {summary_payload['credit_state']['total_spent']}")
    print(f"Summary: {summary_path}")
    checkpoint_path = summary_path.parent / CHECKPOINT_NAME
    print(f"Checkpoint: {checkpoint_path}")


def main() -> None:
    """Run the resumable bulk collection CLI."""
    args = parse_args()
    validate_args(args)

    terms = read_terms_file(Path(args.terms_file))
    if args.max_terms is not None:
        terms = terms[: args.max_terms]
    if not terms:
        raise SystemExit("Nenhum termo válido encontrado no arquivo.")

    config = YouTubeDataConfig(
        api_key=args.api_key,
        storage_root=args.storage_root,
        log_path=args.log_path,
        run_id=args.run_id,
        compress_responses=args.compress_responses,
    )
    client = YouTubeDataClient.from_config(config)
    summary_payload, summary_path = collect_videos_from_terms_resumable(client, args, terms)
    print_summary(summary_payload, summary_path)


if __name__ == "__main__":
    main()
