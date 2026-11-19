#!/usr/bin/env bash
# Daily YouTube ID collection runner.
# Invoked by the youtube-daily-collect.timer (systemd --user) after the
# fact-checking-scrape tier2 service finishes.
#
# Usage:
#   run_daily_youtube_collect.sh                # uses today's date (UTC)
#   run_daily_youtube_collect.sh YYYY-MM-DD     # explicit date

set -u

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_DIR="${YOUTUBE_API_REPO_DIR:-$(dirname "$SCRIPT_DIR")}"
readonly FACTCHECK_DAILY_DIR="${FACTCHECK_DAILY_DIR:-$HOME/Documentos/repositorios/fact-checking-scrape/data/daily}"
readonly LOG_FILE="$REPO_DIR/logs/daily_youtube_collect.log"
readonly PIDFILE="/tmp/youtube-daily-collect.pid"

mkdir -p "$(dirname "$LOG_FILE")"

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "[$(date -Iseconds)] another run is in progress (pid $(cat "$PIDFILE")), exiting" >> "$LOG_FILE"
    exit 0
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

readonly TARGET_DATE="${1:-$(date -u +%Y-%m-%d)}"
readonly DAILY_FILE="$FACTCHECK_DAILY_DIR/${TARGET_DATE}-mercado.jsonl"

{
    echo "[$(date -Iseconds)] starting daily youtube collect date=$TARGET_DATE"

    if [[ ! -s "$DAILY_FILE" ]]; then
        echo "[$(date -Iseconds)] daily file missing or empty: $DAILY_FILE — skipping"
        exit 0
    fi

    if [[ -f "$REPO_DIR/.env" ]]; then
        set -a
        # shellcheck source=/dev/null
        source "$REPO_DIR/.env"
        set +a
    fi

    if [[ -z "${YOUTUBE_API_KEY:-}" ]]; then
        echo "[$(date -Iseconds)] YOUTUBE_API_KEY not set (check $REPO_DIR/.env) — aborting" >&2
        exit 2
    fi

    cd "$REPO_DIR" || { echo "cannot cd to $REPO_DIR" >&2; exit 2; }

    uv run python scripts/collect_videos_from_daily_factchecks.py \
        --daily-file "$DAILY_FILE" \
        --date "$TARGET_DATE"
    rc=$?

    echo "[$(date -Iseconds)] finished daily youtube collect rc=$rc"
    exit "$rc"
} >> "$LOG_FILE" 2>&1
