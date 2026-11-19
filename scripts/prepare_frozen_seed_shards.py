"""Prepare static disjoint shards from `seed_20261107.csv` for one-shot YouTube collection.

Implements the frozen hand-off contract from `economia_screening_status_report_template.md` (§3):
1) Reads cluster-level seed rows (`seed_20261107.csv`, ordered by `rank`).
2) Optionally filters out clusters already collected in `monitoring.db` (`yt_queries`).
3) Builds the deterministic Option 2B query (`q2` over `central_claim` -> `claim_text` -> `title`,
   stripping agency boilerplate, debunking frames/terms, and Portuguese stopwords, capped at
   `max_tokens=20`).
4) Computes the closed temporal window `[published_at - 45d, published_at + 90d]` in RFC 3339 UTC.
5) Splits the frozen queue into K static disjoint shards (`interleaved` round-robin by `rank` or
   `contiguous` blocks) and writes a cryptographic manifest with ready-to-run CLI commands.
"""

from __future__ import annotations

import argparse
import csv
import email.utils
import hashlib
import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_SEED_CSV = Path("./data/processed/colab_outputs/outputs/seed_20261107.csv")
DEFAULT_OUTPUT_DIR = Path("./data/processed/frozen_seed_20261107")
DEFAULT_LOCAL_Q2_JSONL = Path("./data/processed/queries/factcheck_unified_q2.jsonl")
DEFAULT_LOCAL_Q2_SUMMARY = Path(
    "./data/raw/runs/factcheck_unified_q2/terms_collection_summary.json"
)

WHITESPACE_RE: re.Pattern[str] = re.compile(r"\s+")

AGENCY_SUFFIXES: tuple[str, ...] = (
    " - polígrafo",
    " - poligrafo",
    " - aos fatos",
    " | aos fatos",
    " - aosfatos",
    " - estadão verifica",
    " - estadao verifica",
    " - afp checamos",
    " - lupa",
    " - agência lupa",
    " - agencia lupa",
    " - projeto comprova",
    " - comprova",
    " - uol confere",
    " - uol",
    " - g1",
    " - fato ou fake",
    " - boatos.org",
    " - e-farsas",
    " - observador",
    " - público",
    " - publico",
    " - reuters",
    " | polígrafo",
    " | poligrafo",
)

AGENCY_PREFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^checagem de fatos[:\-]\s*", re.IGNORECASE),
    re.compile(r"^é\s+#?fake\s+que\s+", re.IGNORECASE),
    re.compile(r"^é\s*#fake\s+", re.IGNORECASE),
    re.compile(r"^#fake\s*[:\-]?\s*", re.IGNORECASE),
    re.compile(r"^fato\s+ou\s+fake\??:?\s*", re.IGNORECASE),
    re.compile(r"^boato[\!:]?\s*", re.IGNORECASE),
)

GENERIC_TITLES: frozenset[str] = frozenset({"observador", "observador.pt"})

DEBUNK_PREFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^é\s+fals[oa]\s+a\s+\w+\s+de\s+que\s+", re.IGNORECASE),
    re.compile(r"^é\s+fals[oa]\s+que\s+", re.IGNORECASE),
    re.compile(r"^é\s+fals[oa]\s*:?\s+", re.IGNORECASE),
    re.compile(r"^não\s+é\s+verdade\s+que\s+", re.IGNORECASE),
    re.compile(
        r"^é\s+engan[oa]s[oa]\s+(?:que\s+|ao\s+(?:dizer|afirmar)\s+que\s+)?",
        re.IGNORECASE,
    ),
    re.compile(r"^é\s+distorcid[oa]\s+(?:que\s+)?", re.IGNORECASE),
    re.compile(r"^é\s+montagem\s+(?:que\s+|[oa]\s+)?", re.IGNORECASE),
    re.compile(
        r"^é\s+golpe\s+(?:[oa]\s+|que\s+|v[íi]deo\s+|post\s+|link\s+|mensagem\s+)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"^posts?\s+(?:enganam?|usam?|distorcem?)\s+(?:ao\s+(?:dizer|afirmar)\s+que\s+)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"^v[íi]deos?\s+(?:engana|distorce|mente|manipula)\s+ao\s+(?:dizer|afirmar)\s+que\s+",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:checamos|verificamos|enganoso|distorcido)\s*:?\s+", re.IGNORECASE),
)

DEBUNK_TERMS: frozenset[str] = frozenset(
    """
    falso falsa falsos falsas enganoso enganosa enganosos enganosas distorcido distorcida
    boato boatos montagem mentira mentiroso golpe golpes checamos verificamos checagem fake
    engana enganam desinformação nunca jamais
    """.split()
)

PT_STOPWORDS: frozenset[str] = frozenset(
    """
    a o as os um uma uns umas de do da dos das em no na nos nas ao aos à às por para
    com sem sob sobre entre até após ante desde perante e ou nem mas porém contudo todavia
    que se como quando onde porque pois então também já não sim talvez muito pouco mais
    menos tão tanto bem mal aqui ali lá cá este esta isto esse essa isso aquele aquela aquilo
    seu sua seus suas meu minha teu tua nosso nossa vosso dele dela deles delas lhe lhes me te
    nos vos eu tu ele ela nós vós eles elas você vocês o a é são foi era eram ser sendo sido
    ter tem têm tinha há haver vai vão ia foi pela pelo pelas pelos num numa nuns numas dum
    duma das dos seu sua à às todo toda todos todas cada qualquer the of and to in for on
    """.split()
)

_STRIP_CHARS = ".,;:!?\"'()[]{}«»…–—"


@dataclass(slots=True)
class FrozenClusterQuery:
    """One cluster entry in a frozen snapshot shard."""

    cluster_index: int
    rank: int
    claim_cluster_id: str
    representative_item_id: str
    item_ids: list[str]
    n_items_in_cluster: int
    agency_id: str
    published_at: str | None
    published_after: str | None
    published_before: str | None
    query_text: str
    token_count: int
    query_source_field: str
    max_llm_confidence: int
    max_lex_score: float
    source: str
    central_claim: str
    claim_text: str
    title: str
    shard_id: int = 1
    num_shards: int = 1
    arm: str | None = None
    query_id: str | None = None
    video_duration: str | None = None
    wave_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict (includes `term` alias for collector)."""
        payload = asdict(self)
        payload["term"] = self.query_text
        for opt_key in ("arm", "query_id", "video_duration", "wave_type"):
            if payload.get(opt_key) is None:
                payload.pop(opt_key, None)
        return payload


def _normalize_token(token: str) -> str:
    return token.strip(_STRIP_CHARS).lower()


def _is_stopword(token: str) -> bool:
    norm = _normalize_token(token)
    return norm == "" or norm in PT_STOPWORDS


def clean_title(raw_title: str) -> str:
    """Remove agency suffixes/prefixes and normalize whitespace."""
    if not raw_title:
        return ""
    text = WHITESPACE_RE.sub(" ", raw_title).strip()
    changed = True
    while changed and text:
        changed = False
        lowered = text.lower()
        for suffix in AGENCY_SUFFIXES:
            if lowered.endswith(suffix):
                text = text[: len(text) - len(suffix)].rstrip()
                changed = True
                break
    changed = True
    while changed and text:
        changed = False
        for pattern in AGENCY_PREFIX_PATTERNS:
            new_text = pattern.sub("", text, count=1)
            if new_text != text:
                text = new_text.lstrip()
                changed = True
                break
    return WHITESPACE_RE.sub(" ", text).strip()


def is_generic_title(title: str | None) -> bool:
    """Return True when title is empty or a generic site name."""
    cleaned = clean_title(title or "")
    if not cleaned:
        return True
    return cleaned.casefold().rstrip(".") in GENERIC_TITLES


def strip_debunk_frame(text: str) -> str:
    """Remove debunking prefixes and meta-vocabulary while keeping content tokens."""
    if not text:
        return ""
    changed = True
    while changed and text:
        changed = False
        for pattern in DEBUNK_PREFIX_PATTERNS:
            new_text = pattern.sub("", text, count=1)
            if new_text != text:
                text = new_text.lstrip()
                changed = True
                break
    kept = [token for token in text.split() if _normalize_token(token) not in DEBUNK_TERMS]
    return " ".join(kept)


def strip_stopwords(text: str) -> str:
    """Remove Portuguese stopwords preserving token order, accents, and numbers."""
    return " ".join(token for token in text.split() if not _is_stopword(token))


def build_option_2b_query(
    *,
    central_claim: str | None,
    claim_text: str | None,
    title: str | None,
    max_tokens: int = 20,
) -> tuple[str, str]:
    """Build the deterministic Option 2B (`q2`) query over `central_claim` with fallbacks.

    Returns:
        Tuple of `(query_text, source_field_name)`.
    """
    candidates: list[tuple[str, str]] = [
        ("central_claim", central_claim or ""),
        ("claim_text", claim_text or ""),
        ("title", "" if is_generic_title(title) else (title or "")),
    ]
    for field_name, raw_value in candidates:
        cleaned = clean_title(raw_value)
        if not cleaned:
            continue
        rewritten = strip_stopwords(strip_debunk_frame(cleaned))
        tokens = rewritten.split()[:max_tokens]
        if tokens:
            return " ".join(tokens).strip(), field_name

    # Fallback if all tokens were debunk/stopwords: use cleaned text without stripping
    for field_name, raw_value in candidates:
        cleaned = clean_title(raw_value)
        tokens = cleaned.split()[:max_tokens]
        if tokens:
            return " ".join(tokens).strip(), f"{field_name}_raw_fallback"

    return "", "empty"


def build_arm_query(
    *,
    central_claim: str | None,
    claim_text: str | None,
    title: str | None,
    economic_object: str | None = None,
    arm: str | None = None,
    max_tokens: int = 20,
) -> tuple[str, str]:
    """Build deterministic query for the requested YRP arm (`propagacao`, `combate`, `neutra`)."""
    base_query, source_field = build_option_2b_query(
        central_claim=central_claim,
        claim_text=claim_text,
        title=title,
        max_tokens=max_tokens,
    )
    if not base_query:
        return "", "empty"
    if arm is None or arm == "propagacao":
        return base_query, source_field
    if arm == "combate":
        core_tokens = base_query.split()[: max(1, max_tokens - 2)]
        return " ".join([*core_tokens, "verdade", "falso"]).strip(), f"{source_field}_combate"
    if arm == "neutra":
        obj_tokens = [
            tok for tok in strip_stopwords(clean_title(economic_object or "")).split() if tok
        ]
        core_tokens = list(dict.fromkeys([*obj_tokens, *base_query.split()]))[: min(8, max_tokens)]
        return " ".join(core_tokens).strip(), f"{source_field}_neutra"
    return base_query, source_field


def parse_published_timestamp(raw_value: str | None) -> datetime | None:
    """Parse ISO 8601, YYYY-MM-DD, or RFC 2822 timestamp into UTC datetime."""
    if not raw_value or not raw_value.strip():
        return None
    text = raw_value.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_date_window(
    published_at: str | None,
    *,
    lookback_days: int = 45,
    lookahead_days: int = 90,
) -> tuple[str | None, str | None]:
    """Compute `[published_at - lookback_days, published_at + lookahead_days]` in RFC 3339 UTC."""
    dt = parse_published_timestamp(published_at)
    if dt is None:
        return None, None
    after = (dt - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    before = (dt + timedelta(days=lookahead_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return after, before


def load_collected_item_ids_from_db(db_path: Path) -> set[str]:
    """Load `item_id`s that already have at least one collected query in `monitoring.db`."""
    if not db_path.exists():
        raise FileNotFoundError(f"Banco SQLite não encontrado: {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(
            "SELECT DISTINCT item_id FROM yt_queries WHERE last_collected_at IS NOT NULL"
        )
        return {str(row[0]) for row in cursor.fetchall() if row[0]}
    finally:
        conn.close()


def load_collected_item_ids_from_local_q2_run(
    q2_jsonl_path: Path = DEFAULT_LOCAL_Q2_JSONL,
    summary_path: Path = DEFAULT_LOCAL_Q2_SUMMARY,
) -> set[str]:
    """Load `item_id`s already collected in the local `factcheck_unified_q2` run."""
    if not q2_jsonl_path.exists() or not summary_path.exists():
        return set()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    processed_terms = {
        str(entry.get("term") or "") for entry in summary.get("per_term", []) if entry.get("term")
    }
    collected: set[str] = set()
    for line in q2_jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if payload.get("query") in processed_terms:
            rec_id = str(payload.get("record_id") or "")
            if ":" in rec_id:
                collected.add(rec_id.split(":", 1)[1])
            elif rec_id:
                collected.add(rec_id)
    return collected


def resolve_predictions_csv(
    seed_csv: Path,
    predictions_csv: Path | None = None,
) -> Path | None:
    """Resolve explicit `--predictions-csv` or auto-discover sibling `predictions_*.csv`."""
    if predictions_csv is not None:
        if not predictions_csv.exists():
            raise FileNotFoundError(f"Arquivo de predições não encontrado: {predictions_csv}")
        return predictions_csv
    candidates = sorted(seed_csv.parent.glob("predictions_*.csv"))
    return candidates[0] if candidates else None


def load_predictions_enrichment(predictions_csv: Path) -> dict[str, dict[str, str]]:
    """Index `central_claim`, `published_at`, and `published_dt` by `item_id`."""
    by_item: dict[str, dict[str, str]] = {}
    with predictions_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            item_id = str(row.get("item_id") or "").strip()
            if not item_id:
                continue
            by_item[item_id] = {
                "central_claim": str(row.get("central_claim") or "").strip(),
                "published_at": str(row.get("published_at") or "").strip(),
                "published_dt": str(row.get("published_dt") or "").strip(),
            }
    return by_item


def parse_item_ids(raw_json: str | None, fallback_item_id: str) -> list[str]:
    """Parse `item_ids_json` from CSV row, falling back to `[fallback_item_id]`."""
    if raw_json and raw_json.strip():
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, list) and parsed:
                return [str(x) for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [fallback_item_id] if fallback_item_id else []


def load_and_prepare_clusters(
    seed_csv: Path,
    *,
    predictions_csv: Path | None = None,
    lookback_days: int = 45,
    lookahead_days: int = 90,
    max_tokens: int = 20,
    min_confidence: int = 1,
    source_filter: str = "all",
    max_clusters: int | None = None,
    collected_item_ids: set[str] | None = None,
    arm: str | None = None,
    video_duration: str | None = None,
    wave_type: str | None = None,
) -> tuple[list[FrozenClusterQuery], list[FrozenClusterQuery]]:
    """Read `seed_20261107.csv` and build active and excluded `FrozenClusterQuery` lists."""
    if not seed_csv.exists():
        raise FileNotFoundError(f"Arquivo de seed não encontrado: {seed_csv}")

    resolved_preds = resolve_predictions_csv(seed_csv, predictions_csv)
    preds_by_item = (
        load_predictions_enrichment(resolved_preds) if resolved_preds is not None else {}
    )

    collected = collected_item_ids or set()
    raw_rows: list[dict[str, str]] = []
    with seed_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_rows.append(row)

    # Sort deterministically by rank ASC if present
    def _row_sort_key(row: dict[str, str]) -> tuple[int, str]:
        try:
            rank_val = int(row.get("rank") or 0)
        except ValueError:
            rank_val = 0
        return (
            rank_val if rank_val > 0 else 10**9,
            str(row.get("claim_cluster_id") or row.get("item_id") or ""),
        )

    raw_rows.sort(key=_row_sort_key)

    active: list[FrozenClusterQuery] = []
    excluded: list[FrozenClusterQuery] = []
    seen_clusters: set[str] = set()

    for idx, row in enumerate(raw_rows, start=1):
        # Optional s_llm filter when running over full cluster maps
        if "s_llm" in row and str(row["s_llm"]).strip().lower() in {"false", "0", "no"}:
            continue

        cluster_id = str(row.get("claim_cluster_id") or row.get("item_id") or "").strip()
        if not cluster_id or cluster_id in seen_clusters:
            continue
        seen_clusters.add(cluster_id)

        rep_item_id = str(
            row.get("representative_item_id") or row.get("item_id") or cluster_id
        ).strip()
        item_ids = parse_item_ids(row.get("item_ids_json"), rep_item_id)

        try:
            conf = int(float(row.get("max_llm_confidence") or row.get("confidence") or 3))
        except ValueError:
            conf = 3
        if conf < min_confidence:
            continue

        source = str(row.get("source") or "llm").strip()
        if source_filter != "all" and source != source_filter:
            continue

        try:
            rank = int(row.get("rank") or idx)
        except ValueError:
            rank = idx

        try:
            lex_score = float(row.get("max_lex_score") or row.get("score") or 0.0)
        except ValueError:
            lex_score = 0.0

        central_claim = str(row.get("central_claim") or "").strip()
        published_raw = (
            str(row.get("published_at") or "").strip()
            or str(row.get("published_dt") or "").strip()
            or None
        )
        if preds_by_item and (not central_claim or not published_raw):
            for candidate_iid in [rep_item_id, *item_ids]:
                pred_info = preds_by_item.get(candidate_iid)
                if not pred_info:
                    continue
                if not central_claim and pred_info["central_claim"]:
                    central_claim = pred_info["central_claim"]
                if not published_raw and (pred_info["published_at"] or pred_info["published_dt"]):
                    published_raw = pred_info["published_at"] or pred_info["published_dt"]
                if central_claim and published_raw:
                    break

        claim_text = str(row.get("claim_text") or row.get("claim") or "").strip()
        title = str(row.get("title") or "").strip()
        economic_object = str(row.get("economic_object") or "").strip()

        query_text, source_field = build_arm_query(
            central_claim=central_claim,
            claim_text=claim_text,
            title=title,
            economic_object=economic_object,
            arm=arm,
            max_tokens=max_tokens,
        )
        if not query_text:
            continue

        after, before = compute_date_window(
            published_raw,
            lookback_days=lookback_days,
            lookahead_days=lookahead_days,
        )

        query_id: str | None = None
        if any((arm, video_duration, wave_type)):
            parts = [cluster_id]
            if arm:
                parts.append(arm)
            if video_duration:
                parts.append(video_duration)
            if wave_type:
                parts.append(wave_type)
            query_id = ":".join(parts)

        entry = FrozenClusterQuery(
            cluster_index=len(active) + 1,
            rank=rank,
            claim_cluster_id=cluster_id,
            representative_item_id=rep_item_id,
            item_ids=item_ids,
            n_items_in_cluster=len(item_ids),
            agency_id=str(row.get("agency_id") or "").strip(),
            published_at=published_raw,
            published_after=after,
            published_before=before,
            query_text=query_text,
            token_count=len(query_text.split()),
            query_source_field=source_field,
            max_llm_confidence=conf,
            max_lex_score=lex_score,
            source=source,
            central_claim=central_claim,
            claim_text=claim_text,
            title=title,
            arm=arm,
            query_id=query_id,
            video_duration=video_duration,
            wave_type=wave_type,
        )

        if collected and any(item_id in collected for item_id in item_ids):
            excluded.append(entry)
            continue

        active.append(entry)
        if max_clusters is not None and len(active) >= max_clusters:
            break

    # Re-index active sequentially
    for new_idx, item in enumerate(active, start=1):
        item.cluster_index = new_idx

    return active, excluded


def partition_clusters(
    clusters: Sequence[FrozenClusterQuery],
    *,
    num_shards: int,
    strategy: str = "interleaved",
) -> list[list[FrozenClusterQuery]]:
    """Partition clusters into `num_shards` static disjoint lists."""
    if num_shards < 1:
        raise ValueError("num_shards deve ser >= 1")
    if strategy not in {"interleaved", "contiguous"}:
        raise ValueError(f"Estratégia de sharding inválida: {strategy}")

    shards: list[list[FrozenClusterQuery]] = [[] for _ in range(num_shards)]
    total = len(clusters)
    if total == 0:
        return shards

    if strategy == "interleaved":
        for index, cluster in enumerate(clusters):
            shard_idx = index % num_shards
            cluster.shard_id = shard_idx + 1
            cluster.num_shards = num_shards
            shards[shard_idx].append(cluster)
    else:
        base_size, remainder = divmod(total, num_shards)
        cursor = 0
        for shard_idx in range(num_shards):
            size = base_size + (1 if shard_idx < remainder else 0)
            slice_items = clusters[cursor : cursor + size]
            for cluster in slice_items:
                cluster.shard_id = shard_idx + 1
                cluster.num_shards = num_shards
                shards[shard_idx].append(cluster)
            cursor += size

    return shards


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl(path: Path, items: Sequence[FrozenClusterQuery]) -> None:
    """Write a sequence of `FrozenClusterQuery` objects as UTF-8 JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")


def prepare_shards(
    *,
    seed_csv: Path,
    output_dir: Path,
    predictions_csv: Path | None = None,
    prefix: str = "econ_snapshot_20261107",
    num_shards: int = 1,
    shard_strategy: str = "interleaved",
    lookback_days: int = 45,
    lookahead_days: int = 90,
    max_tokens: int = 20,
    search_pages: int = 3,
    daily_credit_limit: int = 10_000,
    min_confidence: int = 1,
    source_filter: str = "all",
    max_clusters: int | None = None,
    monitoring_db: Path | None = None,
    exclude_local_q2_run: bool = False,
    arm: str | None = None,
    video_duration: str | None = None,
    wave_type: str | None = None,
) -> dict[str, Any]:
    """Generate frozen queue JSONL files, disjoint shards, and cryptographic manifest."""
    collected_ids: set[str] = set()
    if monitoring_db is not None:
        collected_ids.update(load_collected_item_ids_from_db(monitoring_db))
    if exclude_local_q2_run:
        collected_ids.update(load_collected_item_ids_from_local_q2_run())
    active, excluded = load_and_prepare_clusters(
        seed_csv,
        predictions_csv=predictions_csv,
        lookback_days=lookback_days,
        lookahead_days=lookahead_days,
        max_tokens=max_tokens,
        min_confidence=min_confidence,
        source_filter=source_filter,
        max_clusters=max_clusters,
        collected_item_ids=collected_ids,
        arm=arm,
        video_duration=video_duration,
        wave_type=wave_type,
    )

    shards = partition_clusters(active, num_shards=num_shards, strategy=shard_strategy)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_path = output_dir / f"{prefix}_all.jsonl"
    write_jsonl(all_path, active)

    excluded_path: Path | None = None
    if excluded:
        excluded_path = output_dir / f"{prefix}_excluded_already_collected.jsonl"
        write_jsonl(excluded_path, excluded)

    shard_manifests: list[dict[str, Any]] = []
    width = max(2, len(str(num_shards)))
    for shard_idx, shard_items in enumerate(shards, start=1):
        shard_tag = f"s{shard_idx:0{width}d}of{num_shards:0{width}d}"
        shard_file = output_dir / f"{prefix}_{shard_tag}.jsonl"
        run_id = f"{prefix}_{shard_tag}"
        write_jsonl(shard_file, shard_items)

        n_clusters = len(shard_items)
        worst_case_units = n_clusters * (search_pages * 100 + search_pages)
        empirical_avg_units = round(n_clusters * (1.8 * 100 + 2))
        cmd = (
            "uv run python scripts/collect_videos_from_terms_resumable.py "
            f"--terms-file {shard_file} "
            f"--run-id {run_id} "
            f"--search-pages {search_pages} "
            f"--credit-limit {daily_credit_limit} "
            "--region-code BR "
            "--relevance-language pt"
        )
        shard_manifests.append(
            {
                "shard_id": shard_idx,
                "shard_tag": shard_tag,
                "run_id": run_id,
                "file": str(shard_file),
                "sha256": sha256_file(shard_file),
                "clusters_count": n_clusters,
                "rank_min": min((c.rank for c in shard_items), default=None),
                "rank_max": max((c.rank for c in shard_items), default=None),
                "estimated_units_worst_case_3pages": worst_case_units,
                "estimated_units_empirical_1_8pages": empirical_avg_units,
                "estimated_days_at_10k_worst_case": round(
                    worst_case_units / max(1, daily_credit_limit), 2
                ),
                "estimated_days_at_10k_empirical": round(
                    empirical_avg_units / max(1, daily_credit_limit), 2
                ),
                "command": cmd,
            }
        )

    manifest_payload: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prefix": prefix,
        "seed_csv": str(seed_csv.resolve()),
        "seed_csv_sha256": sha256_file(seed_csv),
        "monitoring_db_excluded": str(monitoring_db.resolve()) if monitoring_db else None,
        "parameters": {
            "num_shards": num_shards,
            "shard_strategy": shard_strategy,
            "lookback_days": lookback_days,
            "lookahead_days": lookahead_days,
            "max_tokens": max_tokens,
            "search_pages": search_pages,
            "daily_credit_limit": daily_credit_limit,
            "min_confidence": min_confidence,
            "source_filter": source_filter,
            "max_clusters": max_clusters,
        },
        "counts": {
            "clusters_active_total": len(active),
            "clusters_excluded_already_collected": len(excluded),
            "items_covered_by_active_clusters": sum(c.n_items_in_cluster for c in active),
            "clusters_with_date_window": sum(
                1 for c in active if c.published_after and c.published_before
            ),
            "clusters_without_date_window": sum(
                1 for c in active if not (c.published_after and c.published_before)
            ),
        },
        "all_queue_file": str(all_path),
        "all_queue_sha256": sha256_file(all_path),
        "excluded_file": str(excluded_path) if excluded_path else None,
        "shards": shard_manifests,
    }

    manifest_path = output_dir / f"{prefix}_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_payload


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Gera fila congelada (Opção 2B: q2 sobre central_claim, janela [-45d, +90d]) "
            "e divide em K partições estáticas disjuntas para coleta no YouTube."
        )
    )
    parser.add_argument(
        "--seed-csv",
        type=Path,
        default=DEFAULT_SEED_CSV,
        help=f"Caminho para seed_20261107.csv (padrão: {DEFAULT_SEED_CSV}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Diretório de saída dos shards JSONL e manifesto (padrão: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--prefix",
        default="econ_snapshot_20261107",
        help="Prefixo dos arquivos gerados e run-ids.",
    )
    parser.add_argument(
        "--num-shards",
        type=int,
        default=1,
        help="Número de partições estáticas disjuntas K (padrão: 1).",
    )
    parser.add_argument(
        "--shard-strategy",
        choices=("interleaved", "contiguous"),
        default="interleaved",
        help=(
            "Estratégia de particionamento: 'interleaved' (round-robin por rank, "
            "balanceando prioridade entre shards) ou 'contiguous' (blocos contíguos)."
        ),
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=45,
        help="Dias antes de published_at para publishedAfter (padrão: 45).",
    )
    parser.add_argument(
        "--lookahead-days",
        type=int,
        default=90,
        help="Dias após published_at para publishedBefore (padrão: 90).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=20,
        help="Limite máximo de tokens na query Opção 2B (padrão: 20).",
    )
    parser.add_argument(
        "--search-pages",
        type=int,
        default=3,
        help="Páginas máximas por cluster no comando gerado (padrão: 3).",
    )
    parser.add_argument(
        "--daily-credit-limit",
        type=int,
        default=10_000,
        help="Limite diário de créditos por conta/execução (padrão: 10000).",
    )
    parser.add_argument(
        "--min-confidence",
        type=int,
        choices=(1, 2, 3),
        default=1,
        help="Confiança mínima do LLM (1, 2 ou 3) para incluir no seed (padrão: 1).",
    )
    parser.add_argument(
        "--source-filter",
        choices=("all", "llm_and_broad", "llm_only"),
        default="all",
        help="Filtro opcional pela coluna source do seed (padrão: all).",
    )
    parser.add_argument(
        "--max-clusters",
        type=int,
        default=None,
        help="Corta a fila nos N primeiros clusters (após ordenação por rank e deduplicação).",
    )
    parser.add_argument(
        "--predictions-csv",
        type=Path,
        default=None,
        help=(
            "Caminho opcional para predictions_52601_20261107.csv para enriquecer "
            "central_claim e published_at (auto-detectado se estiver no mesmo diretório)."
        ),
    )
    parser.add_argument(
        "--exclude-monitoring-db",
        type=Path,
        default=None,
        help="Caminho opcional para monitoring.db para excluir clusters já coletados.",
    )
    parser.add_argument(
        "--exclude-local-q2-run",
        action="store_true",
        help=(
            "Excluir clusters já coletados na run local factcheck_unified_q2 "
            "(433 clusters no seed_20261107.csv)."
        ),
    )
    parser.add_argument(
        "--arm",
        default=None,
        choices=("propagacao", "combate", "neutra", "scam_in_the_wild", "cross_market"),
        help="Braço experimental YRP opcional para rotular e moldar as queries do shard.",
    )
    parser.add_argument(
        "--video-duration",
        default=None,
        choices=("any", "short", "medium", "long"),
        help="Filtro opcional de duração de vídeo (ex.: short ou medium para campanha Shorts).",
    )
    parser.add_argument(
        "--wave-type",
        default=None,
        choices=("backfill", "decay_t7", "decay_t30", "decay_t90", "holdout_m6"),
        help="Tipo de onda de busca YRP opcional.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the static shard generator CLI."""
    args = parse_args()
    manifest = prepare_shards(
        seed_csv=args.seed_csv.expanduser(),
        output_dir=args.output_dir.expanduser(),
        predictions_csv=args.predictions_csv.expanduser() if args.predictions_csv else None,
        prefix=args.prefix,
        num_shards=args.num_shards,
        shard_strategy=args.shard_strategy,
        lookback_days=args.lookback_days,
        lookahead_days=args.lookahead_days,
        max_tokens=args.max_tokens,
        search_pages=args.search_pages,
        daily_credit_limit=args.daily_credit_limit,
        min_confidence=args.min_confidence,
        source_filter=args.source_filter,
        max_clusters=args.max_clusters,
        monitoring_db=args.exclude_monitoring_db.expanduser()
        if args.exclude_monitoring_db
        else None,
        exclude_local_q2_run=args.exclude_local_q2_run,
        arm=args.arm,
        video_duration=args.video_duration,
        wave_type=args.wave_type,
    )
    counts = manifest["counts"]
    print("=== FILA CONGELADA E PARTIÇÕES ESTÁTICAS GERADAS ===")
    print(f"Seed de entrada          : {manifest['seed_csv']}")
    print(f"Clusters ativos na fila  : {counts['clusters_active_total']}")
    print(f"Itens cobertos (fan-out) : {counts['items_covered_by_active_clusters']}")
    print(f"Excluídos (já no DB)     : {counts['clusters_excluded_already_collected']}")
    print(f"Com janela [-45d, +90d]  : {counts['clusters_with_date_window']}")
    print(f"Sem data (janela aberta) : {counts['clusters_without_date_window']}")
    print(f"Partições disjuntas (K)  : {len(manifest['shards'])} ({args.shard_strategy})")
    print()
    for shard in manifest["shards"]:
        print(
            f"  [Shard {shard['shard_id']} | {shard['shard_tag']}] "
            f"clusters={shard['clusters_count']} "
            f"(rank {shard['rank_min']}..{shard['rank_max']}) "
            f"| est. ~{shard['estimated_days_at_10k_empirical']}d (méd.) a "
            f"{shard['estimated_days_at_10k_worst_case']}d (pior caso 3 págs.)"
        )
        print(f"    Comando: {shard['command']}")


if __name__ == "__main__":
    main()
