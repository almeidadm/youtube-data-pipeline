"""Daily YouTube video-ID collection from fresh fact-check daily files.

Reads `{date}-mercado.jsonl` (produced by fact-checking-scrape), normalizes the
title of each fact-check into a YouTube search query, calls `search.list` and
appends every returned video ID to an append-only sightings file.

Only video IDs are persisted in the sightings file — no thumbnails, no
statistics, no comments. The raw `search.list` payloads are still saved by the
underlying `YouTubeDataClient` under `{storage_root}/runs/{run_id}/` for
auditability.

Re-running for the same `(date, item_id)` pair is a no-op (idempotent): the
script reads existing sightings once at startup and skips items already
processed for that query_date.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from youtube_data_pipeline import YouTubeDataClient, YouTubeDataConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

QUERY_MAX_CHARS = 100
DEFAULT_FACTCHECK_DAILY_DIR = Path(
    "~/Documentos/repositorios/fact-checking-scrape/data/daily"
).expanduser()
DEFAULT_SIGHTINGS_PATH = Path(
    "./data/processed/daily_video_ids/sightings.jsonl"
)
DEFAULT_RUNS_DIR = Path("./data/processed/daily_video_ids/runs")
DEFAULT_STORAGE_ROOT = Path("./data/raw")

# Per-agency suffix/prefix cleanups (lowercased match, original case stripped).
# Order matters: longest/most specific first.
AGENCY_SUFFIXES = (
    " - polígrafo",
    " - poligrafo",
    " - aos fatos",
    " - aosfatos",
    " - estadão verifica",
    " - estadao verifica",
    " - afp checamos",
    " - lupa",
    " - agência lupa",
    " - agencia lupa",
    " - uol confere",
    " - uol",
    " - g1",
    " - boatos.org",
    " - e-farsas",
    " - observador",
    " - público",
    " - publico",
    " - reuters",
    " | polígrafo",
    " | poligrafo",
)

AGENCY_PREFIX_PATTERNS = (
    re.compile(r"^checagem de fatos[:\-]\s*", re.IGNORECASE),
    re.compile(r"^é\s*#fake\s*que\s+", re.IGNORECASE),
    re.compile(r"^é\s*#fake\s+", re.IGNORECASE),
    re.compile(r"^#fake\s*[:\-]?\s*", re.IGNORECASE),
    re.compile(r"^é\s+falso\s+que\s+", re.IGNORECASE),
    re.compile(r"^é\s+falsa\s+", re.IGNORECASE),
    re.compile(r"^é\s+falso\s+", re.IGNORECASE),
    re.compile(r"^fato\s+ou\s+fake[\?:\-]?\s*", re.IGNORECASE),
    re.compile(r"^boato[\!:]?\s*", re.IGNORECASE),
)

WHITESPACE_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FactCheckItem:
    item_id: str
    agency_id: str
    title: str
    canonical_url: str
    published_at: str | None


@dataclass(slots=True)
class Sighting:
    video_id: str
    item_id: str
    query_date: str
    query: str
    page: int
    rank_in_page: int
    agency_id: str
    run_id: str
    seen_at: str


# ---------------------------------------------------------------------------
# Title normalization
# ---------------------------------------------------------------------------


def clean_title(raw_title: str) -> str:
    """Remove agency suffixes/prefixes and normalize whitespace for query use."""
    title = raw_title.strip()
    if not title:
        return ""

    lower = title.lower()
    for suffix in AGENCY_SUFFIXES:
        if lower.endswith(suffix):
            title = title[: -len(suffix)].rstrip(" -|—–")
            lower = title.lower()
            break

    for pattern in AGENCY_PREFIX_PATTERNS:
        if pattern.match(title):
            title = pattern.sub("", title, count=1)
            break

    title = WHITESPACE_RE.sub(" ", title).strip(" \t\n\r-—–|:")
    if len(title) > QUERY_MAX_CHARS:
        truncated = title[:QUERY_MAX_CHARS]
        last_space = truncated.rfind(" ")
        title = truncated[:last_space] if last_space > 0 else truncated
    return title


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def resolve_daily_file(date_str: str, daily_dir: Path) -> Path:
    candidate = daily_dir / f"{date_str}-mercado.jsonl"
    if not candidate.exists():
        raise FileNotFoundError(
            f"Arquivo diário não encontrado para {date_str}: {candidate}"
        )
    return candidate


def load_factchecks(path: Path) -> list[FactCheckItem]:
    items: list[FactCheckItem] = []
    for line_num, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            print(
                f"[warn] linha {line_num} inválida em {path.name}: {exc}",
                file=sys.stderr,
            )
            continue
        item_id = (record.get("item_id") or "").strip()
        title = (record.get("title") or "").strip()
        if not item_id or not title:
            continue
        items.append(
            FactCheckItem(
                item_id=item_id,
                agency_id=(record.get("agency_id") or "").strip(),
                title=title,
                canonical_url=(record.get("canonical_url") or "").strip(),
                published_at=record.get("published_at"),
            )
        )
    return items


def load_already_seen_item_ids(
    sightings_path: Path, query_date: str
) -> set[str]:
    if not sightings_path.exists():
        return set()
    seen: set[str] = set()
    for line in sightings_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("query_date") == query_date and entry.get("item_id"):
            seen.add(entry["item_id"])
    return seen


def append_sightings(path: Path, sightings: Iterable[Sighting]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as fh:
        for s in sightings:
            fh.write(
                json.dumps(
                    {
                        "video_id": s.video_id,
                        "item_id": s.item_id,
                        "query_date": s.query_date,
                        "query": s.query,
                        "page": s.page,
                        "rank_in_page": s.rank_in_page,
                        "agency_id": s.agency_id,
                        "run_id": s.run_id,
                        "seen_at": s.seen_at,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            count += 1
    return count


# ---------------------------------------------------------------------------
# Search response parsing
# ---------------------------------------------------------------------------


def extract_video_ids_with_rank(
    responses: list[dict[str, Any]],
) -> list[tuple[str, int, int]]:
    """Return (video_id, page_idx_1based, rank_in_page_0based) preserving order."""
    out: list[tuple[str, int, int]] = []
    for page_idx, response in enumerate(responses, start=1):
        for rank, item in enumerate(response.get("items", [])):
            video_id = (item.get("id") or {}).get("videoId")
            if video_id:
                out.append((video_id, page_idx, rank))
    return out


def should_abort(result: dict[str, Any]) -> bool:
    for error in result.get("errors", []):
        if error.get("action") == "stop":
            return True
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Coleta IDs de vídeos do YouTube relacionados às checagens do dia, "
            "armazenando cada apareção em um sightings.jsonl append-only."
        )
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("YOUTUBE_API_KEY"),
        help="API key (ou defina YOUTUBE_API_KEY).",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Data ISO (YYYY-MM-DD). Default: hoje (UTC).",
    )
    parser.add_argument(
        "--daily-file",
        default=None,
        help="Caminho direto para o .jsonl diário (sobrescreve --date).",
    )
    parser.add_argument(
        "--daily-dir",
        default=str(DEFAULT_FACTCHECK_DAILY_DIR),
        help=f"Diretório com {{date}}-mercado.jsonl (default: {DEFAULT_FACTCHECK_DAILY_DIR}).",
    )
    parser.add_argument(
        "--sightings-path",
        default=str(DEFAULT_SIGHTINGS_PATH),
        help=f"Arquivo append-only de sightings (default: {DEFAULT_SIGHTINGS_PATH}).",
    )
    parser.add_argument(
        "--runs-dir",
        default=str(DEFAULT_RUNS_DIR),
        help=f"Diretório para summary JSON por execução (default: {DEFAULT_RUNS_DIR}).",
    )
    parser.add_argument(
        "--storage-root",
        default=str(DEFAULT_STORAGE_ROOT),
        help="Raiz de storage do client (respostas brutas + manifest + logs).",
    )
    parser.add_argument(
        "--region-code",
        default="BR",
        help="Region code do search.list (default: BR).",
    )
    parser.add_argument(
        "--relevance-language",
        default="pt",
        help="relevanceLanguage do search.list (default: pt).",
    )
    parser.add_argument(
        "--search-pages",
        type=int,
        default=4,
        help="Páginas por query (default: 4 → até 200 IDs/query).",
    )
    parser.add_argument(
        "--search-max-results",
        type=int,
        default=50,
        help="Itens por página (1–50, default: 50).",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Limita quantos itens do diário processar (útil para testes).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="run_id explícito (default: daily-yt-{date}-{HHMMSS}).",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Ignora cache de item_ids já vistos no query_date e re-roda tudo.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Não chama a API; imprime queries que seriam executadas.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.daily_file:
        daily_path = Path(args.daily_file).expanduser().resolve()
        query_date = args.date or daily_path.stem.split("-mercado")[0]
    else:
        query_date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            date_cls.fromisoformat(query_date)
        except ValueError:
            raise SystemExit(f"--date inválido: {query_date}")
        daily_path = resolve_daily_file(query_date, Path(args.daily_dir).expanduser())

    if not args.dry_run and not args.api_key:
        raise SystemExit("API key não informada. Use --api-key ou YOUTUBE_API_KEY.")
    if args.search_pages < 1:
        raise SystemExit("--search-pages deve ser >= 1.")
    if not 1 <= args.search_max_results <= 50:
        raise SystemExit("--search-max-results deve estar entre 1 e 50.")

    items = load_factchecks(daily_path)
    if args.max_items is not None:
        items = items[: args.max_items]
    if not items:
        print(f"Nenhum item válido em {daily_path}.")
        return

    sightings_path = Path(args.sightings_path).expanduser().resolve()
    runs_dir = Path(args.runs_dir).expanduser().resolve()

    already_seen = (
        set() if args.reprocess else load_already_seen_item_ids(sightings_path, query_date)
    )
    pending = [it for it in items if it.item_id not in already_seen]
    print(
        f"[info] {daily_path.name}: {len(items)} itens totais, "
        f"{len(already_seen)} já processados para {query_date}, "
        f"{len(pending)} pendentes."
    )

    if not pending:
        print("Nada a fazer.")
        return

    started_at = datetime.now(timezone.utc)
    run_id = args.run_id or f"daily-yt-{query_date}-{started_at.strftime('%H%M%SZ')}"

    if args.dry_run:
        print("\n--- DRY RUN — queries que seriam enviadas ---")
        for idx, item in enumerate(pending, 1):
            q = clean_title(item.title)
            print(f"  [{idx:>3}/{len(pending)}] ({item.agency_id}) {q!r}")
        return

    config = YouTubeDataConfig(
        api_key=args.api_key,
        storage_root=args.storage_root,
        run_id=run_id,
    )
    client = YouTubeDataClient.from_config(config)

    total_videos_appended = 0
    items_processed = 0
    items_with_zero_results = 0
    stop_reason: str | None = None
    per_item: list[dict[str, Any]] = []

    for index, item in enumerate(pending, start=1):
        query = clean_title(item.title)
        if not query:
            print(f"[{index}/{len(pending)}] item={item.item_id} título vazio após limpeza — pulando")
            continue

        result = client.fetch(
            "search",
            {
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": args.search_max_results,
                "regionCode": args.region_code,
                "relevanceLanguage": args.relevance_language,
            },
            max_pages=args.search_pages,
            origin=f"factcheck:{item.item_id}",
        )

        if result.get("limit_reached"):
            stop_reason = "credit_limit"
            print(f"[{index}/{len(pending)}] limit_reached — interrompendo")
            break
        if should_abort(result):
            stop_reason = "api_stop_action"
            print(f"[{index}/{len(pending)}] API retornou stop — interrompendo")
            break

        ranked = extract_video_ids_with_rank(result["responses"])
        now_iso = datetime.now(timezone.utc).isoformat()
        sightings = [
            Sighting(
                video_id=vid,
                item_id=item.item_id,
                query_date=query_date,
                query=query,
                page=page,
                rank_in_page=rank,
                agency_id=item.agency_id,
                run_id=run_id,
                seen_at=now_iso,
            )
            for (vid, page, rank) in ranked
        ]
        appended = append_sightings(sightings_path, sightings)
        total_videos_appended += appended
        items_processed += 1
        if appended == 0:
            items_with_zero_results += 1

        per_item.append(
            {
                "item_id": item.item_id,
                "agency_id": item.agency_id,
                "query": query,
                "videos_returned": appended,
                "pages_collected": len(result["responses"]),
            }
        )

        print(
            f"[{index}/{len(pending)}] ({item.agency_id}) videos={appended} "
            f"pages={len(result['responses'])} cumulative_credits="
            f"{result['credit_state']['total_spent']}"
        )

    finished_at = datetime.now(timezone.utc)
    runs_dir.mkdir(parents=True, exist_ok=True)
    summary_path = runs_dir / f"{run_id}.json"
    summary_payload = {
        "run_id": run_id,
        "query_date": query_date,
        "daily_file": str(daily_path),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 2),
        "items_total_in_file": len(items),
        "items_skipped_already_processed": len(already_seen),
        "items_attempted": len(pending),
        "items_processed": items_processed,
        "items_with_zero_results": items_with_zero_results,
        "videos_appended": total_videos_appended,
        "stop_reason": stop_reason,
        "credit_state": client.credit_monitor.snapshot(),
        "per_item": per_item,
        "sightings_path": str(sightings_path),
    }
    summary_path.write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n--- Concluído ---")
    print(f"run_id: {run_id}")
    print(f"itens processados: {items_processed}/{len(pending)}")
    print(f"sightings adicionados: {total_videos_appended}")
    print(f"créditos gastos: {summary_payload['credit_state']['total_spent']}")
    print(f"summary: {summary_path}")
    print(f"sightings: {sightings_path}")


if __name__ == "__main__":
    main()
