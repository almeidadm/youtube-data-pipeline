# Screencast Script (3–4 minutes)

## Goal
Present the research intent and demonstrate the `youtube_data_pipeline` client workflow used for data collection, with emphasis on quota awareness and standardized storage.

## Runtime
3–4 minutes

## Screen flow (high level)
- Terminal or editor open in repo root
- Show `docs/design.md` architecture diagram
- Show `docs/usage.md` for example calls
- Brief glance at `src/youtube_data_pipeline/client.py` and `src/youtube_data_pipeline/endpoints/definitions.py`

## Script

### 0:00–0:30 — Intro and intent
**Narration**
“Hello, I’m Diego de Almeida Miranda from UNIFESP. This project focuses on multimodal financial misinformation in Portuguese YouTube content. The goal is to build a reproducible dataset and evaluate models that combine textual, audiovisual, and engagement signals.”

**On screen**
- Open `docs/yt_research_one_pager.md` and highlight the problem statement and objectives.

### 0:30–1:30 — Client overview and quota focus
**Narration**
“We use a lightweight Python client to collect public YouTube data while managing quota. The design is centered around declarative endpoint definitions, pagination, and credit monitoring so we can scale responsibly.”

**On screen**
- Show `docs/design.md` and point to the architecture diagram.
- Open `src/youtube_data_pipeline/endpoints/definitions.py` and highlight base credit costs.
- Open `src/youtube_data_pipeline/client.py` and highlight `fetch` and credit logging.

### 1:30–2:30 — Demo flow
**Narration**
“The workflow starts with search discovery, then enriches with video metadata, and finally collects comments and threads for engagement signals.”

**On screen**
- Open `docs/usage.md` and scroll to the Jupyter example.
- Highlight the `search` call and the `videos` call.
- Point to `commentThreads` in the recurring execution example.

**Optional terminal snippet to show in narration**
```python
from youtube_data_pipeline import YouTubeDataClient, YouTubeDataConfig

config = YouTubeDataConfig(api_key="...", storage_root="./data/raw")
client = YouTubeDataClient.from_config(config)

search_data = client.fetch(
    "search",
    {
        "part": "snippet",
        "q": "investimentos portugal brasil",
        "type": "video",
        "maxResults": 25,
    },
    max_pages=2,
)
```

### 2:30–3:20 — Storage and traceability
**Narration**
“Each call is stored by endpoint and timestamp. This makes the dataset traceable and ready for annotation and downstream analysis.”

**On screen**
- Show the output pattern from `docs/design.md` or mention the `{endpoint}/{timestamp}/response_001.json` structure.
- Optional: open a sample `data/raw/...` directory if available.

### 3:20–3:50 — Close and support ask
**Narration**
“YouTube Researcher Program support is essential to build a reliable, public Portuguese-language dataset and evaluate multimodal detection methods responsibly. Thank you for considering our proposal.”

**On screen**
- Return to `docs/yt_research_one_pager.md` and highlight the dataset constraints and expected outputs.
