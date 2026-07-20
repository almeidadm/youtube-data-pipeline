"""Gera notebooks/eda_factcheck_unified_runs.ipynb a partir deste script.

Ao rodar, escreve o .ipynb equivalente. Reexecutar sempre que quiser
regenerar o notebook canônico. As células Python foram escritas para
funcionar sem depender deste gerador.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent


def md(text: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": [line + "\n" for line in dedent(text).strip("\n").splitlines()],
    }


def code(text: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": [line + "\n" for line in dedent(text).strip("\n").splitlines()],
    }


CELLS: list[dict] = []


# ─────────────────────────────────────────────────────────────
# 0. Setup
# ─────────────────────────────────────────────────────────────
CELLS.append(md("""
# EDA — runs `factcheck_unified_q{1,2,3}`

Análise exploratória quantitativa das três tiers de coleta descritas em
`docs/metodologia_busca_youtube.md` (Q1 linguagem natural, Q2 termos+NER ±180d,
Q3 TF-IDF+NER -30/+60d). Complementa o diagnóstico qualitativo de
`docs/experimento_record_linkage_video_noticia.md` com métricas agregadas
sobre cobertura, sobreposição, concentração de canais, distribuição temporal
e ruído proxy.

**Saídas:** `reports/figures/eda_runs/`, `reports/tables/eda_runs/`,
cache parquet em `data/interim/eda_runs/`.
"""))

CELLS.append(md("## 0. Setup"))
CELLS.append(code("""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import random
import re
import warnings
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

warnings.filterwarnings("ignore")
plt.rcParams["figure.dpi"] = 110
sns.set_theme(style="whitegrid", context="notebook")
random.seed(7)
np.random.seed(7)
"""))

CELLS.append(code("""
PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", Path.cwd().parent)).resolve()
if not (PROJECT_ROOT / "data").exists():
    # fallback para quando o notebook é executado a partir da raiz do projeto
    PROJECT_ROOT = Path.cwd().resolve()

RUNS_DIR = PROJECT_ROOT / "data" / "raw" / "runs"
QUERIES_DIR = PROJECT_ROOT / "data" / "processed" / "queries"
NEWS_CSV = PROJECT_ROOT / "data" / "raw" / "predictions_s2_factcheck_scrape_unified.csv"

FIGURES_DIR = PROJECT_ROOT / "reports" / "figures" / "eda_runs"
TABLES_DIR = PROJECT_ROOT / "reports" / "tables" / "eda_runs"
INTERIM_DIR = PROJECT_ROOT / "data" / "interim" / "eda_runs"
for d in (FIGURES_DIR, TABLES_DIR, INTERIM_DIR):
    d.mkdir(parents=True, exist_ok=True)

TIERS = ["q1", "q2", "q3"]
RUN_PREFIX = "factcheck_unified_"
PROJECT_ROOT, RUNS_DIR.exists(), QUERIES_DIR.exists(), NEWS_CSV.exists()
"""))


# ─────────────────────────────────────────────────────────────
# 1. Helpers
# ─────────────────────────────────────────────────────────────
CELLS.append(md("""
## 1. Helpers

Funções utilitárias para carregar os artefatos de cada run. O hash usado
em `origin=explicit:<hash>` é `sha256(origin_json)[:12]` (ver
`src/youtube_data_pipeline/storage.py`), mas na prática usamos o
`manifest.jsonl` como índice reverso de `term → file`, evitando reconstruir
o JSON canônico do `origin`.
"""))

CELLS.append(code("""
def tier_run_dir(tier: str) -> Path:
    return RUNS_DIR / f"{RUN_PREFIX}{tier}"


def tier_exists(tier: str) -> bool:
    d = tier_run_dir(tier)
    return d.exists() and (d / "manifest.jsonl").exists()


def available_tiers() -> list[str]:
    return [t for t in TIERS if tier_exists(t)]


def load_queries_jsonl(tier: str) -> pd.DataFrame:
    path = QUERIES_DIR / f"{RUN_PREFIX}{tier}.jsonl"
    if not path.exists():
        return pd.DataFrame()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    df = pd.DataFrame(rows)
    for col in ("publishedAfter", "publishedBefore"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    return df


def load_checkpoint(tier: str) -> pd.DataFrame:
    path = tier_run_dir(tier) / "terms_collection_checkpoint.json"
    if not path.exists():
        return pd.DataFrame()
    raw = json.loads(path.read_text(encoding="utf-8"))
    per_term = raw.get("per_term") or raw.get("terms") or []
    df = pd.DataFrame(per_term)
    df["tier"] = tier
    return df


def load_summary(tier: str) -> dict:
    path = tier_run_dir(tier) / "terms_collection_summary.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def iter_manifest(tier: str) -> Iterator[dict]:
    path = tier_run_dir(tier) / "manifest.jsonl"
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_manifest(tier: str) -> pd.DataFrame:
    rows = list(iter_manifest(tier))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "timestamp" in df.columns:
        df["ts"] = pd.to_datetime(df["timestamp"], format="%Y%m%dT%H%M%SZ", errors="coerce", utc=True)
    df["tier"] = tier

    def _origin_term(o):
        if not isinstance(o, str):
            return None
        try:
            parsed = json.loads(o)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed.get("term") if isinstance(parsed, dict) else None

    if "origin" in df.columns:
        df["term"] = df["origin"].apply(_origin_term)
    return df
"""))

CELLS.append(code("""
def _read_json_any(path: Path) -> dict | None:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _iter_json_files(root: Path) -> Iterator[Path]:
    for p in root.rglob("*.json"):
        if p.name.startswith("error_"):
            continue
        yield p
    for p in root.rglob("*.json.gz"):
        if p.name.startswith("error_"):
            continue
        yield p


def load_search_pages(tier: str) -> pd.DataFrame:
    \"\"\"Varre endpoint=search/... e emite uma linha por item retornado.\"\"\"
    cache = INTERIM_DIR / f"{tier}_search.parquet"
    if cache.exists():
        return pd.read_parquet(cache)

    root = tier_run_dir(tier) / "endpoint=search"
    if not root.exists():
        return pd.DataFrame()

    manifest_idx = {}
    for entry in iter_manifest(tier):
        if entry.get("entry_type") != "response" or entry.get("endpoint") != "search":
            continue
        rel = entry.get("file")
        if not rel:
            continue
        manifest_idx[rel] = entry.get("term")

    records: list[dict] = []
    run_root = tier_run_dir(tier)
    for file_path in _iter_json_files(root):
        rel = str(file_path.relative_to(run_root))
        term = manifest_idx.get(rel)
        page_match = re.search(r"page_(\\d+)", file_path.name)
        page_num = int(page_match.group(1)) if page_match else -1
        payload = _read_json_any(file_path)
        if not payload:
            continue
        for pos, item in enumerate(payload.get("items") or []):
            vid = (item.get("id") or {}).get("videoId")
            snip = item.get("snippet") or {}
            records.append({
                "tier": tier,
                "term": term,
                "page": page_num,
                "position": pos,
                "videoId": vid,
                "channelId": snip.get("channelId"),
                "channelTitle": snip.get("channelTitle"),
                "publishedAt": snip.get("publishedAt"),
                "title": snip.get("title"),
                "description": snip.get("description"),
                "source_file": rel,
            })

    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df["publishedAt"] = pd.to_datetime(df["publishedAt"], utc=True, errors="coerce")
        df.to_parquet(cache, index=False)
    return df


def load_videos_details(tier: str) -> pd.DataFrame:
    cache = INTERIM_DIR / f"{tier}_videos.parquet"
    if cache.exists():
        return pd.read_parquet(cache)

    root = tier_run_dir(tier) / "endpoint=videos"
    if not root.exists():
        return pd.DataFrame()

    records: list[dict] = []
    for file_path in _iter_json_files(root):
        payload = _read_json_any(file_path)
        if not payload:
            continue
        for item in payload.get("items") or []:
            snip = item.get("snippet") or {}
            stats = item.get("statistics") or {}
            cd = item.get("contentDetails") or {}
            records.append({
                "tier": tier,
                "videoId": item.get("id"),
                "channelId": snip.get("channelId"),
                "channelTitle": snip.get("channelTitle"),
                "publishedAt": snip.get("publishedAt"),
                "title": snip.get("title"),
                "description": snip.get("description"),
                "tags": snip.get("tags") or [],
                "categoryId": snip.get("categoryId"),
                "duration": cd.get("duration"),
                "definition": cd.get("definition"),
                "caption": cd.get("caption"),
                "viewCount": stats.get("viewCount"),
                "likeCount": stats.get("likeCount"),
                "commentCount": stats.get("commentCount"),
            })

    df = pd.DataFrame.from_records(records)
    if df.empty:
        return df
    df["publishedAt"] = pd.to_datetime(df["publishedAt"], utc=True, errors="coerce")
    for col in ("viewCount", "likeCount", "commentCount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # ISO 8601 duration → segundos
    iso_re = re.compile(r"PT(?:(\\d+)H)?(?:(\\d+)M)?(?:(\\d+)S)?")
    def _parse_dur(s):
        if not isinstance(s, str):
            return np.nan
        m = iso_re.fullmatch(s)
        if not m:
            return np.nan
        h, mi, se = (int(x) if x else 0 for x in m.groups())
        return h * 3600 + mi * 60 + se
    df["duration_sec"] = df["duration"].apply(_parse_dur)
    df.to_parquet(cache, index=False)
    return df
"""))

CELLS.append(code("""
def join_terms_with_news(queries_df: pd.DataFrame, news_df: pd.DataFrame) -> pd.DataFrame:
    if queries_df.empty or news_df.empty:
        return queries_df.copy() if not queries_df.empty else pd.DataFrame()
    if "record_id" not in news_df.columns:
        # infere dataset + claim_id → record_id
        if {"dataset_id", "claim_id"}.issubset(news_df.columns):
            news_df = news_df.copy()
            news_df["record_id"] = news_df["dataset_id"].astype(str) + ":" + news_df["claim_id"].astype(str)
    cols = [c for c in ("record_id", "dataset_id", "standard_label", "published_at", "claim_text", "title")
            if c in news_df.columns]
    merged = queries_df.merge(news_df[cols], on="record_id", how="left", suffixes=("", "_news"))
    if "published_at" in merged.columns:
        merged["published_at"] = pd.to_datetime(merged["published_at"], utc=True, errors="coerce")
    return merged


def gini(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    x = x[x >= 0]
    if x.size == 0 or np.all(x == 0):
        return 0.0
    x = np.sort(x)
    n = x.size
    cum = np.cumsum(x)
    return (n + 1 - 2 * np.sum(cum) / cum[-1]) / n


EMOJI_RE = re.compile(
    "[\\U0001F300-\\U0001FAFF\\U00002600-\\U000027BF\\U0001F000-\\U0001F9FF]",
    flags=re.UNICODE,
)
CLICKBAIT_LEX = {
    "urgente", "bomba", "chocante", "absurdo", "escândalo", "revelado",
    "exclusivo", "flagra", "vergonha", "derretendo", "arrebentando",
    "desmascarado", "escandaloso", "impressionante",
}


def sensationalism_score(title: str) -> dict:
    if not isinstance(title, str) or not title.strip():
        return {"caps_ratio": 0.0, "exclam_count": 0, "emoji_count": 0, "clickbait_hits": 0, "len_chars": 0}
    letters = [c for c in title if c.isalpha()]
    caps = [c for c in letters if c.isupper()]
    caps_ratio = (len(caps) / len(letters)) if letters else 0.0
    lower = title.lower()
    hits = sum(1 for w in CLICKBAIT_LEX if w in lower)
    return {
        "caps_ratio": caps_ratio,
        "exclam_count": title.count("!"),
        "emoji_count": len(EMOJI_RE.findall(title)),
        "clickbait_hits": hits,
        "len_chars": len(title),
    }


def save_fig(fig, name: str) -> Path:
    path = FIGURES_DIR / f"{name}.png"
    fig.savefig(path, bbox_inches="tight", dpi=140)
    return path


def save_table(df: pd.DataFrame, name: str) -> Path:
    path = TABLES_DIR / f"{name}.csv"
    df.to_csv(path, index=False)
    return path
"""))


# ─────────────────────────────────────────────────────────────
# 2. Carregamento
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 2. Carregamento consolidado"))
CELLS.append(code("""
AVAILABLE = available_tiers()
MISSING = [t for t in TIERS if t not in AVAILABLE]
print(f"Tiers disponíveis: {AVAILABLE}")
if MISSING:
    print(f"Tiers AUSENTES (seções univariadas os ignoram; comparativas exigem >= 2): {MISSING}")
"""))

CELLS.append(code("""
news_df = pd.read_csv(NEWS_CSV) if NEWS_CSV.exists() else pd.DataFrame()
if not news_df.empty and "label" in news_df.columns:
    news_mercado = news_df[news_df["label"] == "mercado"].copy()
else:
    news_mercado = news_df.copy()
print("notícias totais:", len(news_df), "| após filtro label==mercado:", len(news_mercado))
news_mercado.head(2)
"""))

CELLS.append(code("""
dfs: dict[str, dict[str, pd.DataFrame]] = {}
for t in AVAILABLE:
    print(f"--- {t} ---")
    queries = load_queries_jsonl(t)
    checkpoint = load_checkpoint(t)
    manifest = load_manifest(t)
    search = load_search_pages(t)
    videos = load_videos_details(t)
    joined = join_terms_with_news(queries, news_mercado)
    dfs[t] = {
        "queries": queries,
        "checkpoint": checkpoint,
        "manifest": manifest,
        "search": search,
        "videos": videos,
        "joined": joined,
        "summary": load_summary(t),
    }
    print(
        f"  queries={len(queries)} | checkpoint={len(checkpoint)} | manifest={len(manifest)} "
        f"| search_items={len(search)} | videos_details={len(videos)}"
    )
"""))

CELLS.append(code("""
# sanity checks
rows = []
for t, d in dfs.items():
    rows.append({
        "tier": t,
        "terms_queries_jsonl": len(d["queries"]),
        "terms_checkpoint": len(d["checkpoint"]),
        "search_items": len(d["search"]),
        "search_unique_videoIds": d["search"]["videoId"].nunique() if not d["search"].empty else 0,
        "videos_details_rows": len(d["videos"]),
        "videos_details_unique": d["videos"]["videoId"].nunique() if not d["videos"].empty else 0,
    })
sanity = pd.DataFrame(rows)
save_table(sanity, "01_sanity_checks")
sanity
"""))


# ─────────────────────────────────────────────────────────────
# 3. Status e custo
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 3. Status da coleta e custo"))
CELLS.append(code("""
status_rows = []
for t, d in dfs.items():
    chk = d["checkpoint"]
    summ = d["summary"]
    if chk.empty:
        continue
    vc = chk["status"].value_counts().to_dict()
    total_credits = summ.get("credit_state", {}).get("total_spent", np.nan)
    unique_videos = summ.get("unique_videos_global", d["search"]["videoId"].nunique() if not d["search"].empty else 0)
    duration = summ.get("duration_seconds", np.nan)
    status_rows.append({
        "tier": t,
        "terms_total": summ.get("terms_total", len(chk)),
        "terms_processed": summ.get("terms_processed", len(chk)),
        "completed": vc.get("completed", 0),
        "pending": vc.get("pending", 0),
        "errored": vc.get("errored", 0) + vc.get("error", 0),
        "credits_total": total_credits,
        "credits_per_unique_video": (total_credits / unique_videos) if unique_videos else np.nan,
        "unique_videos": unique_videos,
        "duration_seconds": duration,
        "status": summ.get("status"),
        "stop_reason": summ.get("stop_reason"),
    })
status_df = pd.DataFrame(status_rows)
save_table(status_df, "03_run_status_by_tier")
status_df
"""))

CELLS.append(code("""
if not status_df.empty:
    fig, ax = plt.subplots(figsize=(8, 4))
    plot_df = status_df.set_index("tier")[["completed", "pending", "errored"]]
    plot_df.plot(kind="bar", stacked=True, ax=ax, color=["#4C9F70", "#E0A458", "#C3423F"])
    ax.set_title("Status dos termos por tier")
    ax.set_ylabel("nº de termos")
    ax.legend(title="status")
    save_fig(fig, "03_status_by_tier")
    plt.show()
"""))


# ─────────────────────────────────────────────────────────────
# 4. Cobertura
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 4. Cobertura por tier"))
CELLS.append(code("""
cov_rows = []
for t, d in dfs.items():
    joined = d["joined"]
    search = d["search"]
    chk = d["checkpoint"]
    terms_total = d["summary"].get("terms_total", len(d["queries"]))
    terms_with_videos = (chk["videos_found"] > 0).sum() if not chk.empty and "videos_found" in chk else np.nan
    # record_ids efetivamente atendidos: termos com ≥1 vídeo retornado
    if not chk.empty and "term" in chk.columns and not joined.empty:
        ok_terms = set(chk.loc[chk.get("videos_found", 0) > 0, "term"].astype(str))
        rids = joined.loc[joined["query"].astype(str).isin(ok_terms), "record_id"].nunique()
    else:
        rids = np.nan
    cov_rows.append({
        "tier": t,
        "terms_total": terms_total,
        "terms_with_videos": terms_with_videos,
        "record_ids_covered": rids,
        "pct_record_ids": (rids / terms_total * 100) if terms_total and pd.notna(rids) else np.nan,
        "videos_unique": search["videoId"].nunique() if not search.empty else 0,
    })
cov_df = pd.DataFrame(cov_rows)
save_table(cov_df, "04_coverage_by_tier")
cov_df
"""))

CELLS.append(code("""
# Heatmap: cobertura por dataset_id (agência) × tier
blocks = []
for t, d in dfs.items():
    joined = d["joined"]
    chk = d["checkpoint"]
    if joined.empty or chk.empty or "dataset_id" not in joined.columns:
        continue
    ok_terms = set(chk.loc[chk.get("videos_found", 0) > 0, "term"].astype(str))
    j = joined.copy()
    j["covered"] = j["query"].astype(str).isin(ok_terms).astype(int)
    agg = j.groupby("dataset_id")["covered"].agg(["sum", "count"]).reset_index()
    agg["tier"] = t
    agg["rate"] = agg["sum"] / agg["count"]
    blocks.append(agg)
if blocks:
    heat = pd.concat(blocks).pivot(index="dataset_id", columns="tier", values="rate").fillna(0)
    fig, ax = plt.subplots(figsize=(7, max(4, 0.35 * len(heat))))
    sns.heatmap(heat, annot=True, fmt=".2f", cmap="YlGnBu", ax=ax, cbar_kws={"label": "taxa de cobertura"})
    ax.set_title("Taxa de termos com ≥1 vídeo, por agência e tier")
    save_fig(fig, "04_coverage_heatmap")
    plt.show()
    save_table(heat.reset_index(), "04_coverage_heatmap_matrix")
"""))


# ─────────────────────────────────────────────────────────────
# 5. Distribuição de vídeos por termo
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 5. Distribuição de vídeos por termo"))
CELLS.append(code("""
rows = []
for t, d in dfs.items():
    chk = d["checkpoint"]
    if chk.empty or "videos_found" not in chk.columns:
        continue
    sub = chk[["videos_found"]].copy()
    sub["tier"] = t
    rows.append(sub)
if rows:
    vf = pd.concat(rows, ignore_index=True)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    sns.boxplot(data=vf, x="tier", y="videos_found", ax=axes[0])
    axes[0].set_title("Vídeos encontrados por termo (boxplot)")
    for tier_name, sub in vf.groupby("tier"):
        sns.ecdfplot(sub["videos_found"], label=tier_name, ax=axes[1])
    axes[1].set_title("ECDF")
    axes[1].set_xlabel("videos_found")
    axes[1].legend()
    save_fig(fig, "05_videos_per_term")
    plt.show()
    stats = vf.groupby("tier")["videos_found"].describe(percentiles=[.5, .9]).round(1)
    save_table(stats.reset_index(), "05_videos_per_term_stats")
    display(stats)
"""))


# ─────────────────────────────────────────────────────────────
# 6. Sobreposição entre tiers
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 6. Sobreposição entre tiers"))
CELLS.append(code("""
sets = {t: set(d["search"]["videoId"].dropna().unique()) for t, d in dfs.items() if not d["search"].empty}
pair_rows = []
tiers_present = list(sets.keys())
for i, a in enumerate(tiers_present):
    for b in tiers_present[i+1:]:
        inter = sets[a] & sets[b]
        union = sets[a] | sets[b]
        pair_rows.append({
            "a": a, "b": b,
            "|a|": len(sets[a]), "|b|": len(sets[b]),
            "|a∩b|": len(inter),
            "jaccard": (len(inter) / len(union)) if union else 0.0,
            "|a∩b|/|a|": (len(inter) / len(sets[a])) if sets[a] else 0.0,
        })
if tiers_present:
    inter_all = set.intersection(*sets.values()) if len(tiers_present) > 1 else set()
    pair_rows.append({"a": "∩ all", "b": "", "|a|": len(inter_all), "|b|": "", "|a∩b|": len(inter_all),
                      "jaccard": "", "|a∩b|/|a|": ""})
overlap_df = pd.DataFrame(pair_rows)
save_table(overlap_df, "06_overlap_matrix")
overlap_df
"""))

CELLS.append(code("""
# Tenta usar matplotlib_venn; se ausente, cai para gráfico de barras
if len(sets) >= 2:
    try:
        if len(sets) == 3:
            from matplotlib_venn import venn3
            fig, ax = plt.subplots(figsize=(6, 5))
            venn3([sets[t] for t in tiers_present], set_labels=tiers_present, ax=ax)
            ax.set_title("Sobreposição de videoIds entre tiers")
            save_fig(fig, "06_venn")
            plt.show()
        else:
            from matplotlib_venn import venn2
            a, b = tiers_present[:2]
            fig, ax = plt.subplots(figsize=(5, 4))
            venn2([sets[a], sets[b]], set_labels=(a, b), ax=ax)
            save_fig(fig, "06_venn")
            plt.show()
    except Exception as e:
        print(f"matplotlib_venn indisponível ({e}); use a tabela acima.")
"""))

CELLS.append(code("""
# UpSet plot opcional
try:
    from upsetplot import from_contents, UpSet
    data = from_contents({t: s for t, s in sets.items()})
    fig = plt.figure(figsize=(8, 4.5))
    UpSet(data, show_counts=True, sort_by="cardinality").plot(fig=fig)
    save_fig(fig, "06_upset")
    plt.show()
except Exception as e:
    print(f"upsetplot indisponível ({e}); seguindo com tabelas.")
"""))


# ─────────────────────────────────────────────────────────────
# 7. Deduplicação intra-tier
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 7. Deduplicação intra-tier"))
CELLS.append(code("""
rows = []
for t, d in dfs.items():
    s = d["search"]
    if s.empty:
        continue
    pair = s.dropna(subset=["videoId", "term"]).drop_duplicates(["videoId", "term"])
    mult = pair.groupby("videoId").size()
    total_slots = len(s)
    unique_videos = s["videoId"].nunique()
    rows.append({
        "tier": t,
        "total_slots": total_slots,
        "unique_videos": unique_videos,
        "redundancy_ratio": 1 - (unique_videos / total_slots) if total_slots else 0.0,
        "mean_multiplicity": mult.mean(),
        "max_multiplicity": int(mult.max()) if not mult.empty else 0,
        "pct_videos_multi_term": (mult > 1).mean() * 100,
    })
redund = pd.DataFrame(rows)
save_table(redund, "07_redundancy_by_tier")
redund
"""))

CELLS.append(code("""
# Top-20 vídeos mais repetidos no total (união dos tiers)
blocks = []
for t, d in dfs.items():
    s = d["search"]
    if s.empty:
        continue
    pair = s.dropna(subset=["videoId", "term"]).drop_duplicates(["videoId", "term"])
    m = pair.groupby(["videoId"]).size().rename("term_multiplicity").reset_index()
    m["tier"] = t
    m = m.merge(s[["videoId", "title", "channelTitle"]].drop_duplicates("videoId"), on="videoId", how="left")
    blocks.append(m)
if blocks:
    top = pd.concat(blocks).sort_values("term_multiplicity", ascending=False).head(20)
    save_table(top, "07_top20_repeated_videos")
    display(top.head(20))
"""))


# ─────────────────────────────────────────────────────────────
# 8. Distribuição temporal
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 8. Distribuição temporal"))
CELLS.append(code("""
rows = []
for t, d in dfs.items():
    s = d["search"]
    joined = d["joined"]
    if s.empty or joined.empty or "published_at" not in joined.columns:
        continue
    q_small = joined[["query", "publishedAfter", "publishedBefore", "published_at"]].drop_duplicates("query")
    merged = s.merge(q_small, left_on="term", right_on="query", how="left")
    merged["date_delta_days"] = (merged["publishedAt"] - merged["published_at"]).dt.days

    def _in_window(r):
        pa, pb, pv = r["publishedAfter"], r["publishedBefore"], r["publishedAt"]
        if pd.isna(pv):
            return np.nan
        if pd.notna(pa) and pv < pa:
            return False
        if pd.notna(pb) and pv > pb:
            return False
        return True
    merged["in_window"] = merged.apply(_in_window, axis=1)
    merged["tier"] = t
    rows.append(merged[["tier", "date_delta_days", "in_window", "publishedAt"]])

if rows:
    tempo = pd.concat(rows, ignore_index=True)
    fig, axes = plt.subplots(1, 2, figsize=(13, 4))
    sns.histplot(data=tempo.dropna(subset=["date_delta_days"]), x="date_delta_days", hue="tier",
                 bins=60, element="step", stat="density", common_norm=False, ax=axes[0])
    axes[0].set_title("Δ dias (vídeo - notícia)")
    axes[0].set_xlim(-365 * 2, 365 * 2)

    monthly = (tempo.dropna(subset=["publishedAt"])
                    .assign(month=lambda x: x["publishedAt"].dt.to_period("M").dt.to_timestamp())
                    .groupby(["tier", "month"]).size().reset_index(name="n"))
    sns.lineplot(data=monthly, x="month", y="n", hue="tier", ax=axes[1])
    axes[1].set_title("Vídeos por mês de publicação")
    axes[1].tick_params(axis="x", rotation=30)
    save_fig(fig, "08_temporal")
    plt.show()

    window_stats = (tempo.groupby("tier")["in_window"]
                         .agg(["mean", "sum", "count"])
                         .rename(columns={"mean": "pct_in_window", "sum": "n_in_window", "count": "n_total"})
                         .reset_index())
    save_table(window_stats, "08_window_adherence")
    display(window_stats)
"""))


# ─────────────────────────────────────────────────────────────
# 9. Concentração por canal
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 9. Concentração por canal"))
CELLS.append(code("""
gini_rows = []
for t, d in dfs.items():
    s = d["search"]
    if s.empty:
        continue
    counts = s.drop_duplicates(["videoId"]).groupby("channelTitle").size().sort_values(ascending=False)
    g = gini(counts.values)
    top20 = counts.head(20)
    gini_rows.append({
        "tier": t,
        "n_channels": len(counts),
        "n_unique_videos": s["videoId"].nunique(),
        "gini": round(g, 3),
        "pct_top20": round(top20.sum() / counts.sum() * 100, 1),
    })
    save_table(top20.rename("videos").reset_index(), f"09_channel_top20_{t}")
gini_df = pd.DataFrame(gini_rows)
save_table(gini_df, "09_channel_gini")
gini_df
"""))

CELLS.append(code("""
# Curva de Lorenz
fig, ax = plt.subplots(figsize=(6, 5))
for t, d in dfs.items():
    s = d["search"]
    if s.empty:
        continue
    counts = np.sort(s.drop_duplicates("videoId").groupby("channelTitle").size().values)
    cum = np.cumsum(counts) / counts.sum()
    x = np.linspace(0, 1, len(cum))
    ax.plot(x, cum, label=t)
ax.plot([0, 1], [0, 1], "k--", alpha=0.4, label="igualdade")
ax.set_title("Curva de Lorenz (concentração por canal)")
ax.set_xlabel("fração de canais (ordenados)")
ax.set_ylabel("fração de vídeos")
ax.legend()
save_fig(fig, "09_lorenz")
plt.show()
"""))


# ─────────────────────────────────────────────────────────────
# 10. Engajamento
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 10. Estatísticas de engajamento"))
CELLS.append(code("""
rows = []
for t, d in dfs.items():
    v = d["videos"]
    if v.empty:
        continue
    sub = v[["viewCount", "likeCount", "commentCount", "duration_sec"]].copy()
    sub["tier"] = t
    rows.append(sub)
if rows:
    eng = pd.concat(rows, ignore_index=True)
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    for ax, col in zip(axes.ravel(), ["viewCount", "likeCount", "commentCount", "duration_sec"]):
        data = eng.dropna(subset=[col]).copy()
        data[col] = data[col].clip(lower=1)
        sns.boxplot(data=data, x="tier", y=col, ax=ax)
        ax.set_yscale("log")
        ax.set_title(col + " (log)")
    fig.suptitle("Engajamento por tier")
    fig.tight_layout()
    save_fig(fig, "10_engagement_boxplots")
    plt.show()
    eng_stats = (eng.groupby("tier")
                    [["viewCount", "likeCount", "commentCount", "duration_sec"]]
                    .quantile([0.25, 0.5, 0.75, 0.95])
                    .round(1))
    save_table(eng_stats.reset_index(), "10_engagement_quantiles")
    display(eng_stats)
"""))


# ─────────────────────────────────────────────────────────────
# 11. Categorias
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 11. Categorias do YouTube"))
CELLS.append(code("""
YT_CATEGORIES = {
    "1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music",
    "15": "Pets & Animals", "17": "Sports", "19": "Travel & Events",
    "20": "Gaming", "22": "People & Blogs", "23": "Comedy",
    "24": "Entertainment", "25": "News & Politics", "26": "Howto & Style",
    "27": "Education", "28": "Science & Technology", "29": "Nonprofits",
}

rows = []
for t, d in dfs.items():
    v = d["videos"]
    if v.empty or "categoryId" not in v.columns:
        continue
    counts = v.drop_duplicates("videoId")["categoryId"].astype(str).map(lambda x: YT_CATEGORIES.get(x, x)).value_counts(normalize=True)
    for cat, pct in counts.items():
        rows.append({"tier": t, "category": cat, "pct": pct})
if rows:
    cat_df = pd.DataFrame(rows)
    pivot = cat_df.pivot(index="category", columns="tier", values="pct").fillna(0).sort_values(
        by=list(cat_df["tier"].unique())[0], ascending=False
    )
    fig, ax = plt.subplots(figsize=(9, max(4, 0.35 * len(pivot))))
    pivot.plot(kind="barh", ax=ax)
    ax.set_title("Distribuição de categorias por tier (proporção)")
    ax.set_xlabel("fração")
    save_fig(fig, "11_categories")
    plt.show()
    save_table(pivot.reset_index(), "11_categories_by_tier")
"""))


# ─────────────────────────────────────────────────────────────
# 12. Tamanho da query vs retorno
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 12. Tamanho da query vs. retorno"))
CELLS.append(code("""
from scipy.stats import spearmanr

rows = []
for t, d in dfs.items():
    q, chk = d["queries"], d["checkpoint"]
    if q.empty or chk.empty:
        continue
    m = chk.merge(q[["query", "token_count"]], left_on="term", right_on="query", how="left")
    m["tier"] = t
    rows.append(m[["tier", "token_count", "videos_found"]])
if rows:
    qsz = pd.concat(rows, ignore_index=True).dropna()
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.scatterplot(data=qsz, x="token_count", y="videos_found", hue="tier", alpha=0.5, ax=ax)
    ax.set_title("Tokens da query vs. vídeos encontrados")
    save_fig(fig, "12_tokens_vs_videos")
    plt.show()
    corr_rows = []
    for t, g in qsz.groupby("tier"):
        r, p = spearmanr(g["token_count"], g["videos_found"])
        corr_rows.append({"tier": t, "spearman_r": round(r, 3), "p_value": round(p, 4), "n": len(g)})
    corr = pd.DataFrame(corr_rows)
    save_table(corr, "12_tokens_correlation")
    display(corr)
"""))


# ─────────────────────────────────────────────────────────────
# 13. Ruído proxy
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 13. Ruído proxy no título"))
CELLS.append(code("""
rows = []
for t, d in dfs.items():
    s = d["search"]
    if s.empty:
        continue
    titles = s.drop_duplicates("videoId")[["videoId", "title", "channelTitle"]].copy()
    feats = titles["title"].apply(sensationalism_score).apply(pd.Series)
    titles = pd.concat([titles, feats], axis=1)
    titles["tier"] = t
    rows.append(titles)
if rows:
    sens = pd.concat(rows, ignore_index=True)
    feat_cols = ["caps_ratio", "exclam_count", "emoji_count", "clickbait_hits"]
    fig, axes = plt.subplots(1, 4, figsize=(16, 4))
    for ax, col in zip(axes, feat_cols):
        sns.boxplot(data=sens, x="tier", y=col, ax=ax, showfliers=False)
        ax.set_title(col)
    fig.suptitle("Features de sensacionalismo por tier")
    fig.tight_layout()
    save_fig(fig, "13_sensationalism")
    plt.show()
    save_table(sens.groupby("tier")[feat_cols].mean().round(3).reset_index(),
               "13_sensationalism_means")
    top_sens = sens.sort_values(["clickbait_hits", "exclam_count", "caps_ratio"], ascending=False).head(30)
    save_table(top_sens, "13_top_sensational_titles")
    display(top_sens[["tier", "title", "channelTitle", "clickbait_hits", "caps_ratio", "exclam_count"]].head(15))
"""))


# ─────────────────────────────────────────────────────────────
# 14. Enriquecimento por agência
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 14. Enriquecimento por agência"))
CELLS.append(code("""
blocks = []
for t, d in dfs.items():
    joined = d["joined"]
    chk = d["checkpoint"]
    if joined.empty or chk.empty or "dataset_id" not in joined.columns:
        continue
    yields = chk[["term", "videos_found"]].copy()
    j = joined.merge(yields, left_on="query", right_on="term", how="left")
    agg = (j.groupby("dataset_id")
             .agg(n_terms=("record_id", "count"),
                  pct_covered=("videos_found", lambda x: (x > 0).mean()),
                  yield_mean=("videos_found", "mean"))
             .reset_index())
    agg["tier"] = t
    blocks.append(agg)
if blocks:
    ag = pd.concat(blocks)
    pivot_yield = ag.pivot(index="dataset_id", columns="tier", values="yield_mean").fillna(0)
    fig, ax = plt.subplots(figsize=(7, max(4, 0.35 * len(pivot_yield))))
    sns.heatmap(pivot_yield, annot=True, fmt=".1f", cmap="magma_r", ax=ax,
                cbar_kws={"label": "yield médio"})
    ax.set_title("Yield médio (vídeos/termo) por agência × tier")
    save_fig(fig, "14_yield_heatmap")
    plt.show()
    save_table(ag, "14_agency_yield")

# distribuição de standard_label entre record_ids cobertos vs universo
if not news_mercado.empty and "standard_label" in news_mercado.columns:
    universe = news_mercado["standard_label"].value_counts(normalize=True).rename("universo")
    dists = [universe]
    for t, d in dfs.items():
        joined = d["joined"]
        chk = d["checkpoint"]
        if joined.empty or chk.empty:
            continue
        ok_terms = set(chk.loc[chk.get("videos_found", 0) > 0, "term"].astype(str))
        rids = joined.loc[joined["query"].astype(str).isin(ok_terms), "record_id"].unique()
        covered = news_mercado[news_mercado["record_id"].isin(rids)] if "record_id" in news_mercado.columns else pd.DataFrame()
        if covered.empty:
            continue
        dists.append(covered["standard_label"].value_counts(normalize=True).rename(t))
    if len(dists) > 1:
        label_dist = pd.concat(dists, axis=1).fillna(0)
        fig, ax = plt.subplots(figsize=(8, 4))
        label_dist.plot(kind="bar", ax=ax)
        ax.set_title("Distribuição de standard_label: universo vs. cobertos")
        save_fig(fig, "14_label_dist")
        plt.show()
        save_table(label_dist.reset_index().rename(columns={"index": "standard_label"}),
                   "14_label_distribution")
"""))


# ─────────────────────────────────────────────────────────────
# 15. Amostra auditável
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 15. Amostra auditável (inspeção manual)"))
CELLS.append(code("""
SAMPLE_PER_TIER = 20
samples = {}
for t, d in dfs.items():
    s = d["search"]
    joined = d["joined"]
    if s.empty:
        continue
    pool = s.drop_duplicates("videoId").dropna(subset=["videoId"])
    if not joined.empty:
        qmap = joined[["query", "record_id", "claim_text" if "claim_text" in joined.columns else "query"]].drop_duplicates("query")
        pool = pool.merge(qmap, left_on="term", right_on="query", how="left")
    n = min(SAMPLE_PER_TIER, len(pool))
    samp = pool.sample(n=n, random_state=7).copy()
    samp["url"] = "https://youtu.be/" + samp["videoId"].astype(str)
    keep = [c for c in ["record_id", "claim_text", "term", "videoId", "url", "title", "channelTitle", "publishedAt"] if c in samp.columns]
    samp = samp[keep]
    save_table(samp, f"15_qualitative_sample_{t}")
    samples[t] = samp
    print(f"--- {t} (n={n}) ---")
    display(samp.head(10))
"""))


# ─────────────────────────────────────────────────────────────
# 16. Síntese comparativa
# ─────────────────────────────────────────────────────────────
CELLS.append(md("## 16. Síntese comparativa"))
CELLS.append(code("""
summary_rows = []
for t, d in dfs.items():
    summ = d["summary"]
    s = d["search"]
    v = d["videos"]
    chk = d["checkpoint"]
    row = {
        "tier": t,
        "terms_processed": summ.get("terms_processed"),
        "unique_videos": summ.get("unique_videos_global", s["videoId"].nunique() if not s.empty else 0),
        "credits": summ.get("credit_state", {}).get("total_spent"),
        "credits_per_unique_video": (summ.get("credit_state", {}).get("total_spent", 0)
                                     / max(1, summ.get("unique_videos_global", 1))),
        "yield_mean": chk["videos_found"].mean() if not chk.empty and "videos_found" in chk else np.nan,
        "redundancy_ratio": redund.loc[redund.tier == t, "redundancy_ratio"].iat[0] if not redund.empty else np.nan,
        "gini_channel": gini_df.loc[gini_df.tier == t, "gini"].iat[0] if not gini_df.empty else np.nan,
    }
    summary_rows.append(row)
final = pd.DataFrame(summary_rows)
save_table(final, "16_tier_summary")
final
"""))


# ─────────────────────────────────────────────────────────────
# 17. Conclusões
# ─────────────────────────────────────────────────────────────
CELLS.append(md("""
## 17. Conclusões e próximos passos

Checklist de leitura dos resultados — preencher qualitativamente após
execução:

- **Complementaridade das tiers** (§6): a sobreposição Q1∩Q2∩Q3 confirma
  ou refuta a hipótese da metodologia de que os três níveis capturam
  universos distintos?
- **Adesão aos filtros temporais** (§8): quantos vídeos fora da janela
  declarada aparecem em Q2 e Q3? Isso indica que o `publishedAfter/Before`
  foi efetivamente enviado?
- **Concentração de canais** (§9): se o Gini é alto e o top-20 domina,
  considerar allowlist de veículos jornalísticos como feature em
  `docs/experimento_record_linkage_video_noticia.md` §4.4.
- **Redundância intra-tier** (§7): vídeos que aparecem em muitos termos
  distintos são candidatos a ruído genérico (ex.: compilações de mídia).
- **Viés de cobertura** (§14): alguma agência é sistematicamente sub-
  representada? Algum `standard_label` (ex.: `true`) foi coletado menos?

**Gaps que só o pipeline multimodal resolve** (referência cruzada com
`docs/experimento_record_linkage_video_noticia.md`):

1. Score de relevância explícito (exige bi/cross-encoder sobre transcrição);
2. Tipologia de canal (exige `channels.list`);
3. Sinais de ceticismo (exige `commentThreads`);
4. Conteúdo real do vídeo (exige `captions` / whisper).
"""))


# ─────────────────────────────────────────────────────────────
# Monta ipynb
# ─────────────────────────────────────────────────────────────
NOTEBOOK = {
    "cells": CELLS,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.11"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

OUT = Path(__file__).resolve().parents[1] / "notebooks" / "eda_factcheck_unified_runs.ipynb"
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(NOTEBOOK, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"wrote {OUT} with {len(CELLS)} cells")
