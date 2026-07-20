"""Bulk collect YouTube videos from a terms file using YouTube Data API v3.

The script executes the flow:
1) search by term (paginated)
2) videos details for collected IDs (batched by 50 IDs)

All API responses are persisted by youtube_data_pipeline under:
{storage_root}/runs/{run_id}/...
"""

import argparse
import json
import os
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from youtube_data_pipeline import YouTubeDataClient, YouTubeDataConfig


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
            "persistindo respostas de search e videos."
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
        help="Identificador da execução (opcional).",
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
        help="Limite total de créditos da execução.",
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


def main() -> None:
    """Run bulk collection process."""
    args = parse_args()
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

    started_at = datetime.now(timezone.utc)
    total_terms = len(terms)
    processed_terms = 0
    seen_video_ids: set[str] = set()
    limit_hit = False
    stop_reason: str | None = None
    per_term_summary: list[dict[str, Any]] = []

    for index, term in enumerate(terms, start=1):
        search_result = client.fetch(
            "search",
            {
                "part": "snippet",
                "q": term,
                "type": "video",
                "maxResults": args.search_max_results,
                "regionCode": args.region_code,
            },
            max_pages=args.search_pages,
            credit_limit=args.credit_limit,
        )
        if search_result.get("limit_reached"):
            limit_hit = True
            stop_reason = "credit_limit"
            break
        if should_abort(search_result):
            limit_hit = True
            stop_reason = "stop_action_on_search"
            break

        term_video_ids = extract_video_ids(search_result["responses"])
        new_global_ids = 0
        for video_id in term_video_ids:
            if video_id in seen_video_ids:
                continue
            seen_video_ids.add(video_id)
            new_global_ids += 1

        videos_calls = 0
        for batch in chunked(term_video_ids, 50):
            videos_result = client.fetch(
                "videos",
                {
                    "part": "snippet,statistics,contentDetails",
                    "id": ",".join(batch),
                    "maxResults": 50,
                    "regionCode": args.region_code,
                },
                max_pages=1,
                credit_limit=args.credit_limit,
                origin=term,
            )
            videos_calls += 1

            if videos_result.get("limit_reached"):
                limit_hit = True
                stop_reason = "credit_limit"
                break
            if should_abort(videos_result):
                limit_hit = True
                stop_reason = "stop_action_on_videos"
                break

        per_term_summary.append(
            {
                "term": term,
                "search_pages_collected": len(search_result["responses"]),
                "videos_found": len(term_video_ids),
                "videos_calls": videos_calls,
                "new_unique_videos_global": new_global_ids,
            }
        )
        processed_terms += 1

        print(
            f"[{index}/{total_terms}] term={term!r} "
            f"videos_found={len(term_video_ids)} "
            f"unique_global={len(seen_video_ids)}"
        )

        if limit_hit:
            break

    finished_at = datetime.now(timezone.utc)
    run_root = Path(args.storage_root).expanduser().resolve() / "runs" / client.run_id
    run_root.mkdir(parents=True, exist_ok=True)
    summary_path = run_root / args.summary_name

    summary_payload: dict[str, Any] = {
        "run_id": client.run_id,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 2),
        "terms_total": total_terms,
        "terms_processed": processed_terms,
        "limit_reached": limit_hit,
        "stop_reason": stop_reason,
        "unique_videos_global": len(seen_video_ids),
        "credit_state": client.credit_monitor.snapshot(),
        "per_term": per_term_summary,
    }
    summary_path.write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Bulk collection finished")
    print(f"Run ID: {client.run_id}")
    print(f"Terms processed: {processed_terms}/{total_terms}")
    print(f"Unique videos (global): {len(seen_video_ids)}")
    print(f"Total spent: {summary_payload['credit_state']['total_spent']}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
