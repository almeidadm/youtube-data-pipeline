"""Helpers to prepare news records for YouTube search and record linkage."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

TEXT_FIELDS = (
    "record_id",
    "dataset_id",
    "title",
    "subtitle",
    "claim_text",
    "analysis_text",
    "body_text",
    "published_at",
    "source_url",
    "source_domain_display",
    "language",
    "catalogacao_final",
    "veracidade_binaria",
)

NULL_MARKERS = {"na", "none", "n/a", "nat", "null", "nan"}

GENERIC_STOPWORDS = {
    "a",
    "ao",
    "aos",
    "as",
    "com",
    "como",
    "contra",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "entre",
    "foi",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "ou",
    "para",
    "por",
    "que",
    "se",
    "sem",
    "sobre",
    "um",
    "uma",
    "é",
}

GENERIC_QUERY_TERMS = {
    "boato",
    "boatos",
    "checamos",
    "desinformacao",
    "desinformação",
    "fake",
    "fakenews",
    "falso",
    "fato",
    "fatos",
    "verdade",
    "verificamos",
}

GENERIC_ENTITY_PHRASES = {
    "aos fatos",
    "agencia publica",
    "agência pública",
    "presidente da republica",
    "presidente da república",
}

COMMON_HOST_TOKENS = {"br", "com", "net", "news", "org", "pt", "www"}

PUBLISHER_TOKEN_ALIASES = {
    "agenciapublica": {"agencia", "publica"},
    "apublica": {"agencia", "publica"},
    "aosfatos": {"aos", "fatos"},
    "uol": {"uol"},
    "globo": {"globo", "g1"},
    "boatos": {"boatos"},
    "estadao": {"estadao", "estadão"},
    "folha": {"folha"},
    "piaui": {"piaui", "piauí"},
    "checamos": {"checamos"},
    "afp": {"afp"},
    "farsas": {"farsas", "efarsas"},
    "ahp": {"agencia", "ahp", "portuguesa"},
}

TITLE_PREFIX_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"^\s*#verificamos:\s*",
        r"^\s*é\s+#?fake\s+que\s+",
        r"^\s*é\s+falso\s+que\s+",
        r"^\s*é\s+enganos[ao]\s+que\s+",
        r"^\s*não\s+é\s+verdade\s+que\s+",
        r"^\s*veja\s+o\s+que\s+é\s+#fato\s+ou\s+#fake\s+",
        r"^\s*veja\s+o\s+que\s+é\s+",
        r"^\s*checamos\s+\d+\s+frases\s+",
        r"^\s*checamos\s+",
    )
)

TOKEN_PATTERN = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ#]+", flags=re.UNICODE)
SENTENCE_BOUNDARY_PATTERN = re.compile(r"(?<=[.!?])\s+|\n+")


def normalize_text(value: Any) -> str:
    """Normalize potentially dirty text values from the raw dataset."""
    if value is None:
        return ""
    if isinstance(value, float):
        # Números (incluindo NaN) não são texto utilizável.
        return ""
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.replace("\xa0", " ").split())
    if normalized.lower() in NULL_MARKERS:
        return ""
    return normalized.strip()


def normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a normalized copy of one raw record."""
    normalized = dict(record)
    for field in TEXT_FIELDS:
        normalized[field] = normalize_text(record.get(field))

    entities = record.get("entities")
    normalized["entities"] = entities if isinstance(entities, list) else []

    entity_count = record.get("entity_count")
    if isinstance(entity_count, bool):
        normalized["entity_count"] = int(entity_count)
    elif isinstance(entity_count, (int, float)) and not math.isnan(float(entity_count)):
        normalized["entity_count"] = int(entity_count)
    else:
        normalized["entity_count"] = len(normalized["entities"])

    metadata = record.get("metadata")
    normalized["metadata"] = metadata if isinstance(metadata, dict) else {}
    normalized["source_domain"] = _resolve_source_domain(normalized)
    normalized["has_source_url"] = bool(normalized["source_url"])
    return normalized


def iter_jsonl_records(path: str | Path) -> Iterable[dict[str, Any]]:
    """Yield normalized JSONL records preserving file order."""
    input_path = Path(path)
    with input_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            yield normalize_record(json.loads(line))


def clean_title(title: str, *, source_url: str = "", source_domain: str = "") -> str:
    """Clean a title for query preparation."""
    cleaned = normalize_text(title)
    if not cleaned:
        return ""
    cleaned, _ = strip_title_prefix(cleaned)
    cleaned, _ = strip_publisher_suffix(
        cleaned,
        source_url=source_url,
        source_domain=source_domain,
    )
    cleaned = cleaned.strip(" -|:;,.")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def strip_title_prefix(title: str) -> tuple[str, bool]:
    """Remove recurring fact-check boilerplate prefixes from a title."""
    cleaned = title
    removed = False
    while True:
        updated = cleaned
        for pattern in TITLE_PREFIX_PATTERNS:
            updated = pattern.sub("", updated, count=1)
        updated = updated.strip(" -|:;,.")
        if updated == cleaned:
            return cleaned, removed
        cleaned = updated
        removed = True


def strip_publisher_suffix(
    title: str, *, source_url: str = "", source_domain: str = ""
) -> tuple[str, bool]:
    """Remove a trailing publisher segment when it is likely source branding."""
    publisher_tokens = publisher_hint_tokens(
        source_url=source_url, source_domain=source_domain
    )
    publisher_tokens.update({"agencia", "aos", "ahp", "agência", "publica", "fatos", "pública"})

    cleaned = title
    removed = False

    while True:
        match = re.search(r"\s(?:\-|\|)\s([^|\-]{2,})$", cleaned)
        if not match:
            return cleaned, removed

        tail = normalize_text(match.group(1))
        if not tail:
            return cleaned, removed

        tail_tokens = {normalize_token(token) for token in TOKEN_PATTERN.findall(tail)}
        tail_tokens.discard("")

        if not _looks_like_publisher_tail(tail, tail_tokens, publisher_tokens):
            return cleaned, removed

        cleaned = cleaned[: match.start()].rstrip(" -|")
        removed = True


def classify_title_quality(title: str, *, had_boilerplate: bool = False) -> str:
    """Classify how useful a cleaned title is as a search query."""
    normalized = normalize_text(title)
    if not normalized:
        return "missing"

    content_tokens = content_tokens_from_text(normalized)
    if not content_tokens:
        return "missing"

    if len(content_tokens) >= 4 and not had_boilerplate:
        return "strong"

    if len(content_tokens) >= 3:
        return "medium"

    return "weak"


def extract_claim_or_lead(record: Mapping[str, Any]) -> str:
    """Return the primary descriptive text used to enrich query generation."""
    claim_text = normalize_text(record.get("claim_text"))
    if claim_text:
        return claim_text

    for field_name in ("analysis_text", "body_text"):
        candidate = normalize_text(record.get(field_name))
        if not candidate:
            continue
        first_sentence = extract_first_sentence(candidate)
        if first_sentence:
            return first_sentence

    return ""


def extract_top_entities(
    entities: Sequence[Mapping[str, Any]],
    *,
    title_clean: str,
    source_url: str,
    source_domain: str,
    max_entities: int = 3,
) -> list[str]:
    """Return up to ``max_entities`` useful entities for search enrichment."""
    publisher_tokens = publisher_hint_tokens(
        source_url=source_url, source_domain=source_domain
    )
    title_terms = set(content_tokens_from_text(title_clean))

    selected: list[str] = []
    seen: set[str] = set()

    for entity in entities:
        text = normalize_text(entity.get("text"))
        if not text:
            continue

        normalized_key = normalize_token(text)
        entity_tokens = [normalize_token(token) for token in TOKEN_PATTERN.findall(text)]
        entity_tokens = [token for token in entity_tokens if token]
        entity_token_set = set(entity_tokens)

        if normalized_key in seen:
            continue
        if len(normalized_key) < 3:
            continue
        if normalized_key in GENERIC_ENTITY_PHRASES:
            continue
        if not entity_tokens:
            continue
        if all(token.isdigit() for token in entity_tokens):
            continue
        if entity_token_set and entity_token_set <= publisher_tokens:
            continue
        if len(entity_tokens) == 1 and entity_tokens[0] in GENERIC_QUERY_TERMS:
            continue
        if len(entity_tokens) == 1 and entity_tokens[0] in title_terms:
            continue

        seen.add(normalized_key)
        selected.append(text)
        if len(selected) >= max_entities:
            break

    return selected


def build_seed_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Build one seed-catalog row from a normalized raw record."""
    normalized = normalize_record(record)
    title = normalized["title"]

    title_without_prefix, had_prefix_boilerplate = strip_title_prefix(title)
    title_clean, had_suffix_boilerplate = strip_publisher_suffix(
        title_without_prefix,
        source_url=normalized["source_url"],
        source_domain=normalized["source_domain"],
    )
    title_clean = title_clean.strip(" -|:;,.")

    title_quality_band = classify_title_quality(
        title_clean, had_boilerplate=had_prefix_boilerplate
    )

    subtitle_clean = normalize_text(normalized["subtitle"])
    claim_or_lead_clean = extract_claim_or_lead(normalized)
    top_entities = extract_top_entities(
        normalized["entities"],
        title_clean=title_clean,
        source_url=normalized["source_url"],
        source_domain=normalized["source_domain"],
    )

    seed_reference_text = " ".join(
        part
        for part in (title_clean, claim_or_lead_clean, " ".join(top_entities))
        if part
    ).strip()

    return {
        "record_id": normalized["record_id"],
        "dataset_id": normalized["dataset_id"],
        "published_at": normalized["published_at"],
        "title": title,
        "title_clean": title_clean,
        "subtitle_clean": subtitle_clean,
        "claim_or_lead_clean": claim_or_lead_clean,
        "top_entities": top_entities,
        "seed_reference_text": seed_reference_text,
        "title_quality_band": title_quality_band,
        "title_had_boilerplate_prefix": had_prefix_boilerplate,
        "title_had_publisher_suffix": had_suffix_boilerplate,
        "source_url": normalized["source_url"],
        "source_domain": normalized["source_domain"],
        "source_domain_display": normalized["source_domain_display"],
        "language": normalized["language"],
        "catalogacao_final": normalized["catalogacao_final"],
        "veracidade_binaria": normalized["veracidade_binaria"],
        "entity_count": normalized["entity_count"],
        "has_source_url": normalized["has_source_url"],
    }


def build_query_rows(seed_record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build all candidate queries for one seed record."""
    seed = dict(seed_record)

    title_quality_band = normalize_text(seed.get("title_quality_band"))
    title_clean = normalize_text(seed.get("title_clean"))
    claim_or_lead_clean = normalize_text(seed.get("claim_or_lead_clean"))
    published_at = normalize_text(seed.get("published_at"))
    top_entities = _coerce_str_list(seed.get("top_entities"))

    strategies: list[tuple[int, str, str, int, int]] = []

    if title_quality_band in {"strong", "medium"}:
        title_core_text = join_query_terms(
            limit_content_terms(title_clean, max_terms=10)
        )
        if title_core_text:
            strategies.append((1, "title_core", title_core_text, 14, 1))

    if title_quality_band in {"weak", "medium"}:
        title_plus_entities_text = join_query_terms(
            limit_content_terms(title_clean, max_terms=10) + top_entities[:2]
        )
        if title_plus_entities_text:
            strategies.append((2, "title_plus_entities", title_plus_entities_text, 30, 1))

    if title_quality_band in {"missing", "weak"}:
        lead_plus_entities_text = join_query_terms(
            limit_content_terms(claim_or_lead_clean, max_terms=6) + top_entities[:3]
        )
        if lead_plus_entities_text:
            strategies.append((3, "lead_plus_entities", lead_plus_entities_text, 45, 2))

    default_budget = 1 if title_quality_band == "strong" else 2

    query_rows: list[dict[str, Any]] = []
    for index, (priority, strategy, query_text, days_back, max_pages) in enumerate(
        strategies, start=1
    ):
        record_id = normalize_text(seed.get("record_id"))
        query_id = build_query_id(
            record_id=record_id, strategy=strategy, query_text=query_text
        )
        query_rows.append(
            {
                "query_id": query_id,
                "record_id": record_id,
                "dataset_id": normalize_text(seed.get("dataset_id")),
                "query_priority": priority,
                "query_rank_for_record": index,
                "query_strategy": strategy,
                "query_text": query_text,
                "origin_value": build_origin_value(
                    record_id=record_id, query_id=query_id, query_strategy=strategy
                ),
                "published_after": build_published_after(
                    published_at, days_back=days_back
                ),
                "region_code": "BR",
                "search_type": "video",
                "max_results": 25,
                "max_pages": max_pages,
                "title_quality_band": title_quality_band,
                "top_entities": top_entities,
                "seed_reference_text": normalize_text(seed.get("seed_reference_text")),
                "should_run_by_default": index <= default_budget,
            }
        )
    return query_rows


def prepare_news_query_data(
    records: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return seed-catalog rows and query-manifest rows."""
    seed_rows: list[dict[str, Any]] = []
    query_rows: list[dict[str, Any]] = []
    for record in records:
        seed_record = build_seed_record(record)
        seed_rows.append(seed_record)
        query_rows.extend(build_query_rows(seed_record))
    return seed_rows, query_rows


def prepare_news_query_data_from_jsonl(
    input_path: str | Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load a JSONL dataset and return prepared seed and query rows."""
    return prepare_news_query_data(iter_jsonl_records(input_path))


def write_prepared_artifacts(
    *,
    seed_rows: Sequence[Mapping[str, Any]],
    query_rows: Sequence[Mapping[str, Any]],
    output_dir: str | Path,
) -> dict[str, str]:
    """Write JSONL and Parquet artifacts for prepared search data."""
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    seed_jsonl = target_dir / "news_seed_catalog.jsonl"
    query_jsonl = target_dir / "news_query_manifest.jsonl"
    _write_jsonl(seed_jsonl, seed_rows)
    _write_jsonl(query_jsonl, query_rows)

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "pyarrow é necessário para escrever artefatos Parquet."
        ) from exc

    seed_parquet = target_dir / "news_seed_catalog.parquet"
    query_parquet = target_dir / "news_query_manifest.parquet"
    pq.write_table(
        pa.Table.from_pylist([dict(row) for row in seed_rows]), seed_parquet
    )
    pq.write_table(
        pa.Table.from_pylist([dict(row) for row in query_rows]), query_parquet
    )

    return {
        "seed_jsonl": str(seed_jsonl),
        "seed_parquet": str(seed_parquet),
        "query_jsonl": str(query_jsonl),
        "query_parquet": str(query_parquet),
    }


def build_query_id(*, record_id: str, strategy: str, query_text: str) -> str:
    """Return a deterministic identifier for one query candidate."""
    payload = f"{record_id}|{strategy}|{query_text}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"qry-{digest}"


def build_origin_value(*, record_id: str, query_id: str, query_strategy: str) -> str:
    """Return the origin string expected by downstream API calls."""
    return f"rid={record_id}|qid={query_id}|strategy={query_strategy}"


def build_published_after(published_at: str, *, days_back: int) -> str:
    """Return an RFC3339 timestamp relative to the source publication date."""
    if not published_at:
        return ""

    source_date = parse_source_date(published_at)
    if source_date is None:
        return ""

    published_after_date = source_date - timedelta(days=days_back)
    published_after_dt = datetime.combine(
        published_after_date, datetime.min.time(), tzinfo=timezone.utc
    )
    return published_after_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def limit_content_terms(text: str, *, max_terms: int) -> list[str]:
    """Return up to ``max_terms`` content-heavy tokens preserving order."""
    selected: list[str] = []
    seen: set[str] = set()

    for token in TOKEN_PATTERN.findall(normalize_text(text)):
        lowered = normalize_token(token)
        if not lowered or lowered in GENERIC_STOPWORDS or lowered in GENERIC_QUERY_TERMS:
            continue
        if lowered in seen:
            continue
        selected.append(token)
        seen.add(lowered)
        if len(selected) >= max_terms:
            break

    return selected


def content_tokens_from_text(text: str) -> list[str]:
    """Return normalized content tokens from free text."""
    return [normalize_token(token) for token in limit_content_terms(text, max_terms=999)]


def join_query_terms(parts: Sequence[str]) -> str:
    """Join query parts removing duplicates and empty fragments."""
    selected: list[str] = []
    seen: set[str] = set()

    for part in parts:
        candidate = normalize_text(part)
        if not candidate:
            continue
        key = normalize_token(candidate)
        if key in seen:
            continue
        selected.append(candidate)
        seen.add(key)

    return " ".join(selected).strip()


def extract_first_sentence(text: str) -> str:
    """Extract a reasonably short lead sentence from body-like text."""
    normalized = normalize_text(text)
    if not normalized:
        return ""

    for chunk in SENTENCE_BOUNDARY_PATTERN.split(normalized):
        sentence = normalize_text(chunk)
        if len(sentence) >= 20:
            return sentence.rstrip(".!?")

    return normalized[:240].rstrip(".!? ")


def publisher_hint_tokens(*, source_url: str, source_domain: str) -> set[str]:
    """Return normalized tokens that hint the publisher identity."""
    tokens: set[str] = set()

    host = ""
    if source_url:
        parsed = urlparse(source_url)
        host = parsed.netloc.lower()
    if not host:
        host = source_domain.lower()
    host = host.removeprefix("www.")

    for token in re.split(r"[^a-z0-9]+", host):
        normalized = normalize_token(token)
        if not normalized:
            continue
        if normalized in COMMON_HOST_TOKENS:
            continue
        tokens.add(normalized)
        tokens.update(PUBLISHER_TOKEN_ALIASES.get(normalized, set()))

    return tokens


def normalize_token(value: str) -> str:
    """Normalize a token or fragment for comparisons."""
    return normalize_text(value).lower().replace("#", "")


def parse_source_date(value: str) -> datetime.date | None:
    """Parse common source-date formats found in the news catalog."""
    normalized = normalize_text(value)
    if not normalized:
        return None

    candidates = [normalized, normalized[:10]]
    formats = ("%Y-%m-%d", "%d/%m/%Y")

    for candidate in candidates:
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue

    return None


def _resolve_source_domain(record: Mapping[str, Any]) -> str:
    source_domain = normalize_text(record.get("source_domain_display"))
    if source_domain:
        return source_domain

    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        metadata_domain = normalize_text(metadata.get("source_domain"))
        if metadata_domain:
            return metadata_domain

    source_url = normalize_text(record.get("source_url"))
    if source_url:
        return urlparse(source_url).netloc.removeprefix("www.")

    return ""


def _looks_like_publisher_tail(
    tail: str, tail_tokens: set[str], publisher_tokens: set[str]
) -> bool:
    if not tail_tokens:
        return False
    if tail_tokens <= publisher_tokens:
        return True
    if normalize_token(tail) in {"aos fatos", "agência pública", "ahp", "agencia publica"}:
        return True
    if len(tail_tokens) == 1 and tail.isupper() and len(tail) <= 6:
        return True
    return False


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [normalize_text(item) for item in value if normalize_text(item)]


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
