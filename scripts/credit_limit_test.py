import argparse
import os
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlretrieve

from youtube_data_pipeline import YouTubeDataClient, YouTubeDataConfig


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def chunked(items: Iterable[str], size: int) -> Iterable[list[str]]:
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def extract_video_ids(search_responses: list[dict[str, Any]]) -> list[str]:
    video_ids: list[str] = []
    for response in search_responses:
        for item in response.get("items", []):
            video_id = item.get("id", {}).get("videoId")
            if video_id:
                video_ids.append(video_id)
    return video_ids


def extract_thumbnail_urls(video_responses: list[dict[str, Any]]) -> dict[str, str]:
    thumbnails: dict[str, str] = {}
    for response in video_responses:
        for item in response.get("items", []):
            video_id = item.get("id")
            snippet = item.get("snippet", {})
            thumb_obj = snippet.get("thumbnails", {})
            for key in ("maxres", "standard", "high", "medium", "default"):
                if key in thumb_obj:
                    thumbnails[video_id] = thumb_obj[key].get("url")
                    break
    return {k: v for k, v in thumbnails.items() if v}


def download_thumbnails(thumbnails: dict[str, str], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for video_id, url in thumbnails.items():
        if not url:
            continue
        output_path = output_dir / f"{video_id}.jpg"
        if output_path.exists():
            continue
        urlretrieve(url, output_path)


def collect_comments(
    client: YouTubeDataClient,
    video_id: str,
    *,
    comment_pages: int,
    reply_pages: int,
    credit_limit: int,
) -> bool:
    threads_result = client.fetch(
        "commentThreads",
        {
            "part": "snippet,replies",
            "videoId": video_id,
            "maxResults": 100,
        },
        max_pages=comment_pages,
        credit_limit=credit_limit,
    )
    if threads_result.get("limit_reached"):
        return True

    for response in threads_result["responses"]:
        for item in response.get("items", []):
            total_replies = item.get("snippet", {}).get("totalReplyCount", 0)
            if total_replies <= 0:
                continue
            parent_id = item.get("id")
            replies_result = client.fetch(
                "comments",
                {
                    "part": "snippet",
                    "parentId": parent_id,
                    "maxResults": 100,
                },
                max_pages=reply_pages,
                credit_limit=credit_limit,
            )
            if replies_result.get("limit_reached"):
                return True
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Executa um teste de coleta até atingir o limite diário de créditos, "
            "seguindo o fluxo: pesquisa -> vídeos -> legendas -> comentários -> thumbs."
        )
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("YOUTUBE_API_KEY"),
        help="API key do YouTube (ou defina YOUTUBE_API_KEY).",
    )
    parser.add_argument(
        "--storage-root",
        default="./data/raw",
        help="Diretório para armazenar respostas e mídias.",
    )
    parser.add_argument(
        "--log-path",
        default="./logs/youtube_data_pipeline.jsonl",
        help="Caminho do arquivo JSONL de logs.",
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
        "--terms",
        required=True,
        help="Termos de busca separados por vírgula.",
    )
    parser.add_argument(
        "--region-code",
        default="BR",
        help="Region code para busca e vídeos (padrão: BR).",
    )
    parser.add_argument(
        "--credit-limit",
        type=int,
        default=10_000,
        help="Limite diário de créditos para o teste.",
    )
    parser.add_argument(
        "--search-pages",
        type=int,
        default=10,
        help="Número máximo de páginas do search por termo.",
    )
    parser.add_argument(
        "--comment-pages",
        type=int,
        default=999,
        help="Número máximo de páginas de commentThreads por vídeo.",
    )
    parser.add_argument(
        "--reply-pages",
        type=int,
        default=999,
        help="Número máximo de páginas de replies por thread.",
    )
    parser.add_argument(
        "--skip-captions",
        action="store_true",
        help="Pular a coleta de legendas.",
    )
    parser.add_argument(
        "--skip-thumbnails",
        action="store_true",
        help="Pular o download de thumbnails.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.api_key:
        raise SystemExit("API key não informada. Use --api-key ou YOUTUBE_API_KEY.")

    config = YouTubeDataConfig(
        api_key=args.api_key,
        storage_root=args.storage_root,
        log_path=args.log_path,
        run_id=args.run_id,
        compress_responses=args.compress_responses,
    )
    client = YouTubeDataClient.from_config(config)

    terms = [term.strip() for term in args.terms.split(",") if term.strip()]
    seen_video_ids: set[str] = set()
    credit_limit = args.credit_limit
    thumbnails_to_download: dict[str, str] = {}
    limit_hit = False

    for term in terms:
        search_result = client.fetch(
            "search",
            {
                "part": "snippet",
                "q": term,
                "type": "video",
                "maxResults": 50,
                "regionCode": args.region_code,
            },
            max_pages=args.search_pages,
            credit_limit=credit_limit,
        )
        if search_result.get("limit_reached"):
            limit_hit = True
            break

        video_ids = extract_video_ids(search_result["responses"])
        for video_id in video_ids:
            if video_id in seen_video_ids:
                continue
            seen_video_ids.add(video_id)

        for batch in chunked(video_ids, 50):
            videos_result = client.fetch(
                "videos",
                {
                    "part": "snippet,statistics,contentDetails",
                    "id": ",".join(batch),
                    "maxResults": 50,
                    "regionCode": args.region_code,
                },
                max_pages=1,
                credit_limit=credit_limit,
            )
            if videos_result.get("limit_reached"):
                limit_hit = True
                break

            if not args.skip_thumbnails:
                thumbnails_to_download.update(
                    extract_thumbnail_urls(videos_result["responses"])
                )

            for video_id in batch:
                if args.skip_captions is False:
                    captions_result = client.fetch(
                        "captions",
                        {
                            "part": "snippet",
                            "videoId": video_id,
                        },
                        max_pages=1,
                        credit_limit=credit_limit,
                    )
                    if captions_result.get("limit_reached"):
                        limit_hit = True
                        break

                limit_hit = collect_comments(
                    client,
                    video_id,
                    comment_pages=args.comment_pages,
                    reply_pages=args.reply_pages,
                    credit_limit=credit_limit,
                )
                if limit_hit:
                    break

            if limit_hit:
                break

        if limit_hit:
            break

    if not args.skip_thumbnails and thumbnails_to_download:
        download_thumbnails(
            thumbnails_to_download,
            Path(args.storage_root)
            / "runs"
            / client.run_id
            / "thumbnails"
            / _timestamp(),
        )

    state = client.credit_monitor.snapshot()
    print("Credit limit test finished")
    print(f"Total spent: {state['total_spent']}")
    print(f"Per endpoint: {state['per_endpoint']}")
    print(f"Videos processed: {len(seen_video_ids)}")
    print(f"Run ID: {client.run_id}")


if __name__ == "__main__":
    main()
