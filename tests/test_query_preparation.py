import json
from pathlib import Path

import pytest

from youtube_data_pipeline.query_preparation import (
    build_origin_value,
    build_published_after,
    build_query_rows,
    build_seed_record,
    classify_title_quality,
    clean_title,
    extract_claim_or_lead,
    extract_top_entities,
    normalize_text,
    prepare_news_query_data,
    write_prepared_artifacts,
)


def make_record(**overrides):
    record = {
        "record_id": "factck_br:001",
        "dataset_id": "factck_br",
        "title": "Vacina contra dengue protege idosos no Brasil",
        "subtitle": "Subtítulo explicativo",
        "claim_text": "Vacina protege grupo prioritário contra dengue.",
        "analysis_text": "Vacina protege grupo prioritário contra dengue. Texto longo.",
        "body_text": "Corpo do texto com mais detalhes.",
        "published_at": "2024-01-20",
        "source_url": "https://www.aosfatos.org/noticias/exemplo",
        "source_domain_display": "aosfatos.org",
        "language": "pt-BR",
        "catalogacao_final": "outros",
        "veracidade_binaria": "falso",
        "entity_count": 3,
        "entities": [
            {"text": "Brasil", "label": "LOC"},
            {"text": "Ministério da Saúde", "label": "ORG"},
            {"text": "Dengue", "label": "MISC"},
        ],
        "metadata": {"source_domain": "aosfatos.org"},
    }
    record.update(overrides)
    return record


def test_normalize_text_handles_nan_markers_and_whitespace():
    assert normalize_text(float("nan")) == ""
    assert normalize_text(3.14) == ""
    assert normalize_text("  nan  ") == ""
    assert normalize_text("  Texto   com \n espaços  ") == "Texto com espaços"


def test_clean_title_removes_fact_check_prefixes_and_publisher_suffixes():
    cleaned = clean_title(
        "#Verificamos: É #FAKE que vídeo mostre fraude nas urnas | Aos Fatos",
        source_url="https://www.aosfatos.org/noticias/exemplo",
        source_domain="aosfatos.org",
    )
    assert cleaned == "vídeo mostre fraude nas urnas"


@pytest.mark.parametrize(
    ("title", "had_boilerplate", "expected"),
    [
        ("vacina dengue protege idosos brasil", False, "strong"),
        ("fraude urnas eletrônicas", False, "medium"),
        ("boletim hoje", False, "weak"),
        ("", False, "missing"),
        ("video mostra fraude nas urnas", True, "medium"),
    ],
)
def test_classify_title_quality(title, had_boilerplate, expected):
    assert classify_title_quality(title, had_boilerplate=had_boilerplate) == expected


def test_extract_top_entities_filters_noise_and_publisher_tokens():
    entities = [
        {"text": "Agência Pública", "label": "ORG"},
        {"text": "2022", "label": "MISC"},
        {"text": "Alexandre de Moraes", "label": "PER"},
        {"text": "TSE", "label": "ORG"},
        {"text": "Fraude", "label": "MISC"},
        {"text": "Brasil", "label": "LOC"},
    ]
    top_entities = extract_top_entities(
        entities,
        title_clean="Fraude nas urnas com Alexandre de Moraes",
        source_url="https://apublica.org/noticia/exemplo",
        source_domain="apublica.org",
    )
    assert top_entities == ["Alexandre de Moraes", "TSE", "Brasil"]


def test_extract_claim_or_lead_uses_claim_then_analysis_then_body():
    with_claim = make_record(
        claim_text="Texto do claim.", analysis_text="Primeira frase. Segunda frase."
    )
    assert extract_claim_or_lead(with_claim) == "Texto do claim."

    without_claim = make_record(
        claim_text="", analysis_text="Primeira frase útil. Segunda frase."
    )
    assert extract_claim_or_lead(without_claim) == "Primeira frase útil"

    without_analysis = make_record(
        claim_text="",
        analysis_text="",
        body_text="Corpo principal da notícia. Outra frase.",
    )
    assert extract_claim_or_lead(without_analysis) == "Corpo principal da notícia"


def test_build_published_after_accepts_multiple_date_formats():
    assert build_published_after("2024-01-20", days_back=14) == "2024-01-06T00:00:00Z"
    assert build_published_after("17/11/2017", days_back=30) == "2017-10-18T00:00:00Z"
    assert build_published_after("2017-11-17 15:48:11", days_back=14) == "2017-11-03T00:00:00Z"


def test_build_query_rows_is_deterministic_and_preserves_origin_metadata():
    seed_record = build_seed_record(
        make_record(
            title="Fraude urnas Brasil",
            claim_text="Discussão sobre urnas e TSE.",
            entities=[
                {"text": "Alexandre de Moraes", "label": "PER"},
                {"text": "TSE", "label": "ORG"},
                {"text": "Brasil", "label": "LOC"},
            ],
        )
    )
    query_rows = build_query_rows(seed_record)

    assert [row["query_strategy"] for row in query_rows] == [
        "title_core",
        "title_plus_entities",
    ]
    assert [row["query_priority"] for row in query_rows] == [1, 2]
    assert all(row["should_run_by_default"] for row in query_rows)
    assert query_rows[0]["published_after"] == "2024-01-06T00:00:00Z"
    assert query_rows[0]["query_text"] == "Fraude urnas Brasil"
    assert query_rows[0]["origin_value"] == build_origin_value(
        record_id="factck_br:001",
        query_id=query_rows[0]["query_id"],
        query_strategy=query_rows[0]["query_strategy"],
    )

    # Determinismo: ids idênticos em execuções repetidas
    again = build_query_rows(seed_record)
    assert [row["query_id"] for row in query_rows] == [
        row["query_id"] for row in again
    ]


def test_prepare_news_query_data_and_artifacts_keep_record_ids(tmp_path):
    record_2 = make_record(
        record_id="factck_br:002",
        title="#Verificamos: É falso que vacina cause infertilidade - Agência Pública",
        source_url="https://apublica.org/noticia/exemplo",
        source_domain_display="",
        metadata={"source_domain": "apublica.org"},
        claim_text="Vacina não causa infertilidade. Segunda frase.",
        analysis_text="",
        entities=[
            {"text": "Vacina", "label": "MISC"},
            {"text": "Brasil", "label": "LOC"},
            {"text": "Agência Pública", "label": "ORG"},
        ],
    )
    seed_rows, query_rows = prepare_news_query_data([make_record(), record_2])

    assert [row["record_id"] for row in seed_rows] == ["factck_br:001", "factck_br:002"]
    assert all(
        row["record_id"] in {"factck_br:001", "factck_br:002"} for row in query_rows
    )

    artifacts = write_prepared_artifacts(
        seed_rows=seed_rows, query_rows=query_rows, output_dir=tmp_path
    )

    seed_jsonl_path = Path(artifacts["seed_jsonl"])
    query_jsonl_path = Path(artifacts["query_jsonl"])
    assert seed_jsonl_path.exists()
    assert query_jsonl_path.exists()

    with seed_jsonl_path.open(encoding="utf-8") as handle:
        seed_columns = set(json.loads(handle.readline()).keys())
    assert {
        "top_entities",
        "record_id",
        "title_clean",
        "claim_or_lead_clean",
        "dataset_id",
        "seed_reference_text",
    } <= seed_columns

    with query_jsonl_path.open(encoding="utf-8") as handle:
        query_columns = set(json.loads(handle.readline()).keys())
    assert {
        "top_entities",
        "origin_value",
        "search_type",
        "record_id",
        "query_id",
        "published_after",
        "should_run_by_default",
        "dataset_id",
        "max_results",
        "query_strategy",
        "title_quality_band",
        "seed_reference_text",
        "query_text",
        "region_code",
        "max_pages",
        "query_priority",
    } <= query_columns

    import pyarrow.parquet as pq

    seed_parquet_columns = set(pq.read_table(artifacts["seed_parquet"]).column_names)
    assert "record_id" in seed_parquet_columns
    query_parquet_columns = set(pq.read_table(artifacts["query_parquet"]).column_names)
    assert "query_id" in query_parquet_columns


def test_notebook_exists_and_has_expected_structure():
    notebook_path = Path("notebooks/eda_news_query_preparation.ipynb")
    assert notebook_path.exists()
    with notebook_path.open(encoding="utf-8") as handle:
        notebook = json.load(handle)
    assert notebook["nbformat"] == 4
    sources = ["".join(cell.get("source", [])) for cell in notebook["cells"]]
    assert any("query_preparation" in source for source in sources)
