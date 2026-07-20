"""Gera queries estruturadas para busca no YouTube a partir de CSVs de fact-checking.

Produz arquivos JSONL (um por tier) seguindo a metodologia definida em
docs/metodologia_busca_youtube.md:

  - Q1: claim_text em linguagem natural, sem filtro de data
  - Q2: termos sem stopwords + entidades NER, filtro ±180 dias
  - Q3: top termos TF-IDF + entidades NER, filtro -30/+60 dias

Uso:
    python scripts/generate_queries_from_factchecks.py \
        --csv-path data/raw/predictions_s2_factcheck_scrape_unified.csv \
        --output-dir data/processed/queries \
        --dataset-name factcheck_unified
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

CLAIM_MAX_CHARS = 100
Q2_MAX_ENTITIES = 3
Q2_MAX_TOKENS = 7
Q3_TFIDF_TOP_K = 3
Q3_MAX_ENTITIES = 2
TFIDF_MAX_DF = 0.3
TFIDF_MIN_DF = 3
TFIDF_MIN_DF_SMALL = 2  # para corpora com menos de 100 documentos
ENTITY_PRIORITY = {"PER": 0, "LOC": 1, "ORG": 2}
MIN_TOKEN_LEN = 3


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FactCheckRecord:
    record_id: str
    claim_text: str
    text_without_stopwords: str
    entities: list[dict[str, Any]]
    published_at: datetime | None
    standard_label: str
    title: str


@dataclass(slots=True)
class QueryEntry:
    record_id: str
    tier: str
    query: str
    published_after: str | None
    published_before: str | None
    token_count: int


# ---------------------------------------------------------------------------
# Parsing de data
# ---------------------------------------------------------------------------


def parse_published_at(raw: str | None) -> datetime | None:
    """Converte as diversas representações de data dos CSVs para datetime UTC."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S%z",        # 2026-03-04 16:52:00+00:00
        "%Y-%m-%d %H:%M:%S.%f%z",     # 2023-11-24 20:56:45.338000+00:00
        "%Y-%m-%dT%H:%M:%S%z",        # ISO com timezone
        "%Y-%m-%dT%H:%M:%S",          # ISO sem timezone
        "%Y-%m-%d %H:%M:%S",          # datetime sem timezone
        "%Y-%m-%d",                    # date only
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Parsing de entidades
# ---------------------------------------------------------------------------


def parse_entities(raw: str | None) -> list[dict[str, Any]]:
    """Faz parse da coluna entities (JSON ou repr Python)."""
    if not raw or not raw.strip() or raw.strip() == "[]":
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return []
    if not isinstance(parsed, list):
        return []
    return parsed


def deduplicate_entities(
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplica entidades por texto (lowercase), preferindo a de maior prioridade."""
    best: dict[str, dict[str, Any]] = {}
    for ent in entities:
        text = ent.get("text", "").strip().lower()
        label = ent.get("label", "")
        if not text or len(text) < 2:
            continue
        existing = best.get(text)
        if existing is None:
            best[text] = {"text": text, "label": label}
        else:
            if ENTITY_PRIORITY.get(label, 99) < ENTITY_PRIORITY.get(existing["label"], 99):
                best[text] = {"text": text, "label": label}
    # Remove entidades que são substrings de outras (heurística para erros de NER)
    texts = list(best.keys())
    to_remove: set[str] = set()
    for i, t1 in enumerate(texts):
        if " " not in t1:
            continue
        parts = t1.split()
        # Se todas as partes aparecem como entidades separadas, provável erro de NER
        if all(p in best for p in parts):
            to_remove.add(t1)
    for key in to_remove:
        del best[key]
    return list(best.values())


def select_entities(
    entities: list[dict[str, Any]],
    max_count: int,
    prioritize: bool = False,
) -> list[str]:
    """Seleciona entidades únicas, opcionalmente priorizando PER > LOC > ORG."""
    deduped = deduplicate_entities(entities)
    if prioritize:
        deduped.sort(key=lambda e: ENTITY_PRIORITY.get(e["label"], 99))
    return [e["text"] for e in deduped[:max_count]]


# ---------------------------------------------------------------------------
# Carregamento e filtragem do CSV
# ---------------------------------------------------------------------------


def load_and_filter_csv(csv_path: Path, max_records: int | None = None) -> list[FactCheckRecord]:
    """Carrega CSV e aplica filtros: label=mercado, claim>20 chars, label!=missing."""
    records: list[FactCheckRecord] = []
    with csv_path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            label = row.get("label", "").strip()
            if label != "mercado":
                continue
            standard_label = row.get("standard_label", "").strip()
            if standard_label == "missing":
                continue
            claim = row.get("claim_text", "").strip()
            if len(claim) <= 20:
                continue
            records.append(
                FactCheckRecord(
                    record_id=row.get("record_id", "").strip(),
                    claim_text=claim,
                    text_without_stopwords=row.get("text_without_stopwords", "").strip(),
                    entities=parse_entities(row.get("entities")),
                    published_at=parse_published_at(row.get("published_at")),
                    standard_label=standard_label,
                    title=row.get("title", "").strip(),
                )
            )
            if max_records and len(records) >= max_records:
                break
    return records


# ---------------------------------------------------------------------------
# Utilitários de data
# ---------------------------------------------------------------------------


def date_to_rfc3339(dt: datetime) -> str:
    """Formata datetime para RFC 3339 (exigido pela YouTube API)."""
    return dt.strftime("%Y-%m-%dT00:00:00Z")


def compute_date_window(
    published_at: datetime | None,
    days_before: int,
    days_after: int,
) -> tuple[str | None, str | None]:
    """Calcula janela temporal em formato RFC 3339."""
    if published_at is None:
        return None, None
    after = date_to_rfc3339(published_at - timedelta(days=days_before))
    before = date_to_rfc3339(published_at + timedelta(days=days_after))
    return after, before


# ---------------------------------------------------------------------------
# Truncamento de texto
# ---------------------------------------------------------------------------


def truncate_at_word_boundary(text: str, max_chars: int) -> str:
    """Trunca texto no último espaço antes de max_chars."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return truncated[:last_space]
    return truncated


# ---------------------------------------------------------------------------
# Builders de queries
# ---------------------------------------------------------------------------


def build_query_q1(record: FactCheckRecord) -> QueryEntry:
    """Q1: claim_text em linguagem natural, sem filtro de data."""
    query = truncate_at_word_boundary(record.claim_text, CLAIM_MAX_CHARS)
    return QueryEntry(
        record_id=record.record_id,
        tier="q1",
        query=query,
        published_after=None,
        published_before=None,
        token_count=len(query.split()),
    )


def build_query_q2(record: FactCheckRecord) -> QueryEntry:
    """Q2: termos sem stopwords + entidades NER, filtro ±180 dias."""
    entity_texts = select_entities(record.entities, Q2_MAX_ENTITIES, prioritize=False)
    entity_set = set(entity_texts)

    tokens = record.text_without_stopwords.split()
    remaining = [t for t in tokens if t.lower() not in entity_set]

    slots_left = Q2_MAX_TOKENS - len(entity_texts)
    selected = entity_texts + remaining[:slots_left]
    query = " ".join(selected)

    pub_after, pub_before = compute_date_window(record.published_at, 180, 180)
    return QueryEntry(
        record_id=record.record_id,
        tier="q2",
        query=query,
        published_after=pub_after,
        published_before=pub_before,
        token_count=len(selected),
    )


def build_query_q3(
    record: FactCheckRecord,
    tfidf_top_terms: list[str],
) -> QueryEntry:
    """Q3: top termos TF-IDF + entidades NER (PER>LOC>ORG), filtro -30/+60 dias."""
    entity_texts = select_entities(record.entities, Q3_MAX_ENTITIES, prioritize=True)
    entity_set = set(entity_texts)

    # Remover entidades que já estão nos termos TF-IDF
    tfidf_filtered = [t for t in tfidf_top_terms if t.lower() not in entity_set][:Q3_TFIDF_TOP_K]
    selected = tfidf_filtered + entity_texts
    query = " ".join(selected)

    pub_after, pub_before = compute_date_window(record.published_at, 30, 60)
    return QueryEntry(
        record_id=record.record_id,
        tier="q3",
        query=query,
        published_after=pub_after,
        published_before=pub_before,
        token_count=len(selected),
    )


# ---------------------------------------------------------------------------
# TF-IDF
# ---------------------------------------------------------------------------


def compute_tfidf(records: list[FactCheckRecord]) -> dict[str, list[str]]:
    """Calcula TF-IDF sobre text_without_stopwords e retorna top termos por record_id."""
    corpus = [r.text_without_stopwords for r in records]
    min_df = TFIDF_MIN_DF if len(records) >= 100 else TFIDF_MIN_DF_SMALL

    vectorizer = TfidfVectorizer(max_df=TFIDF_MAX_DF, min_df=min_df)
    tfidf_matrix = vectorizer.fit_transform(corpus)
    feature_names = vectorizer.get_feature_names_out()

    result: dict[str, list[str]] = {}
    for idx, record in enumerate(records):
        row = tfidf_matrix[idx]
        scores = np.asarray(row.todense()).flatten()
        ranked_indices = scores.argsort()[::-1]

        top_terms: list[str] = []
        for term_idx in ranked_indices:
            if scores[term_idx] == 0:
                break
            term = feature_names[term_idx]
            if len(term) < MIN_TOKEN_LEN:
                continue
            if term.isdigit():
                continue
            top_terms.append(term)
            if len(top_terms) >= Q3_TFIDF_TOP_K + 5:  # margem para filtragem posterior
                break

        # Fallback: se TF-IDF retorna poucos termos, completar com tokens do texto
        if len(top_terms) < Q3_TFIDF_TOP_K:
            existing = set(top_terms)
            for token in record.text_without_stopwords.split():
                if token not in existing and len(token) >= MIN_TOKEN_LEN and not token.isdigit():
                    top_terms.append(token)
                    existing.add(token)
                if len(top_terms) >= Q3_TFIDF_TOP_K:
                    break

        result[record.record_id] = top_terms
    return result


# ---------------------------------------------------------------------------
# Escrita JSONL
# ---------------------------------------------------------------------------


def query_to_dict(entry: QueryEntry, source_csv: str) -> dict[str, Any]:
    """Serializa QueryEntry para dict."""
    return {
        "record_id": entry.record_id,
        "tier": entry.tier,
        "query": entry.query,
        "publishedAfter": entry.published_after,
        "publishedBefore": entry.published_before,
        "token_count": entry.token_count,
        "source_csv": source_csv,
    }


def write_jsonl(queries: list[QueryEntry], output_path: Path, source_csv: str) -> int:
    """Escreve queries em JSONL. Retorna quantidade de linhas escritas."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        for entry in queries:
            line = json.dumps(query_to_dict(entry, source_csv), ensure_ascii=False)
            fh.write(line + "\n")
    return len(queries)


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------


def generate_report(
    records: list[FactCheckRecord],
    q1: list[QueryEntry],
    q2: list[QueryEntry],
    q3: list[QueryEntry],
    source_csv: str,
) -> dict[str, Any]:
    """Gera relatório estatístico da geração de queries."""
    records_without_date = sum(1 for r in records if r.published_at is None)
    records_without_entities = sum(1 for r in records if not r.entities)

    def tier_stats(queries: list[QueryEntry]) -> dict[str, Any]:
        lengths = [q.token_count for q in queries]
        with_date = sum(1 for q in queries if q.published_after is not None)
        return {
            "count": len(queries),
            "avg_token_count": round(sum(lengths) / len(lengths), 1) if lengths else 0,
            "min_token_count": min(lengths) if lengths else 0,
            "max_token_count": max(lengths) if lengths else 0,
            "with_date_filter": with_date,
            "without_date_filter": len(queries) - with_date,
        }

    return {
        "source_csv": source_csv,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_records_filtered": len(records),
        "records_without_date": records_without_date,
        "records_without_entities": records_without_entities,
        "estimated_search_credits": len(records) * 3 * 100,
        "tiers": {
            "q1": tier_stats(q1),
            "q2": tier_stats(q2),
            "q3": tier_stats(q3),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera queries JSONL para busca no YouTube a partir de CSVs de fact-checking.",
    )
    parser.add_argument(
        "--csv-path",
        required=True,
        help="Caminho para o CSV de entrada.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/processed/queries",
        help="Diretório de saída para os JSONL (padrão: data/processed/queries).",
    )
    parser.add_argument(
        "--dataset-name",
        default=None,
        help="Nome do dataset para prefixo dos arquivos. Se omitido, usa o nome do CSV.",
    )
    parser.add_argument(
        "--tiers",
        default="q1,q2,q3",
        help="Tiers a gerar, separados por vírgula (padrão: q1,q2,q3).",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Limita quantidade de registros processados (útil para testes).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Imprime estatísticas sem escrever arquivos.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_path = Path(args.csv_path).expanduser().resolve()
    if not csv_path.exists():
        print(f"Arquivo não encontrado: {csv_path}", file=sys.stderr)
        raise SystemExit(1)

    dataset_name = args.dataset_name or csv_path.stem
    tiers = {t.strip() for t in args.tiers.split(",")}
    output_dir = Path(args.output_dir).expanduser().resolve()
    source_csv = csv_path.name

    print(f"Carregando {csv_path.name}...")
    records = load_and_filter_csv(csv_path, args.max_records)
    if not records:
        print("Nenhum registro encontrou os critérios de filtragem.", file=sys.stderr)
        raise SystemExit(1)
    print(f"  {len(records)} registros após filtragem (label=mercado, claim>20, label!=missing)")

    # Computar TF-IDF se Q3 solicitado
    tfidf_map: dict[str, list[str]] = {}
    if "q3" in tiers:
        print("Computando TF-IDF...")
        tfidf_map = compute_tfidf(records)

    # Gerar queries
    q1_queries: list[QueryEntry] = []
    q2_queries: list[QueryEntry] = []
    q3_queries: list[QueryEntry] = []

    for record in records:
        if "q1" in tiers:
            q1_queries.append(build_query_q1(record))
        if "q2" in tiers:
            q2_queries.append(build_query_q2(record))
        if "q3" in tiers:
            top_terms = tfidf_map.get(record.record_id, [])
            q3_queries.append(build_query_q3(record, top_terms))

    # Relatório
    report = generate_report(records, q1_queries, q2_queries, q3_queries, source_csv)
    print(f"\n--- Relatório ---")
    print(f"Registros filtrados: {report['total_records_filtered']}")
    print(f"Sem data de publicação: {report['records_without_date']}")
    print(f"Sem entidades NER: {report['records_without_entities']}")
    print(f"Créditos estimados (search): {report['estimated_search_credits']:,}")
    for tier_name, stats in report["tiers"].items():
        if tier_name not in tiers:
            continue
        print(
            f"  {tier_name}: {stats['count']} queries, "
            f"avg {stats['avg_token_count']} tokens, "
            f"{stats['with_date_filter']} com filtro de data"
        )

    if args.dry_run:
        # Mostrar amostras
        print("\n--- Amostras ---")
        for tier_name, queries in [("q1", q1_queries), ("q2", q2_queries), ("q3", q3_queries)]:
            if tier_name not in tiers or not queries:
                continue
            print(f"\n[{tier_name}]")
            for q in queries[:3]:
                date_info = ""
                if q.published_after:
                    date_info = f" | {q.published_after[:10]}..{q.published_before[:10]}"
                print(f"  {q.query[:80]}{date_info}")
        return

    # Escrever JSONL
    written: dict[str, int] = {}
    for tier_name, queries in [("q1", q1_queries), ("q2", q2_queries), ("q3", q3_queries)]:
        if tier_name not in tiers or not queries:
            continue
        path = output_dir / f"{dataset_name}_{tier_name}.jsonl"
        count = write_jsonl(queries, path, source_csv)
        written[tier_name] = count
        print(f"  {path}: {count} queries")

    # Salvar relatório
    report_path = output_dir / f"{dataset_name}_generation_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  {report_path}: relatório salvo")


if __name__ == "__main__":
    main()
