# Multimodal Financial Misinformation in Portuguese YouTube

## Team and affiliation
- Diego de Almeida Miranda (MSc student, UNIFESP)
- Lilian Berton (advisor, UNIFESP)

## Problem statement
The rapid digitalization of financial advice on YouTube has amplified the influence of “finfluencers” and, with it, the spread of financial misinformation. In Portuguese-speaking markets, the lack of specialized datasets and the linguistic nuances of financial slang and localized fraud schemes make reliable detection particularly challenging. This project addresses that gap with a multimodal framework tailored to Portuguese financial content.

## Research intent
Build a reproducible, multimodal dataset and evaluation framework to detect financial misinformation in Portuguese YouTube videos by integrating textual, audiovisual, and engagement signals.

## YouTube data used
- Search results to discover candidate videos (`search.list`)
- Video metadata and statistics (`videos.list`)
- Channel context (`channels.list`)
- Comment threads and comments for engagement signals and filtering (`commentThreads.list`, `comments.list`)
- Captions for aligned text (`captions.list`)

## Dataset targets and constraints
- Current dataset size: 40 records
- Target dataset size: 100+ records
- Low yield: fewer than 1% of videos scanned are suitable for inclusion
- Quota pressure: detailed video analysis and full comment collection are required even when a video is later discarded

## Client capabilities (this repository)
- Declarative endpoint registry with parameter validation
- Credit monitoring per call and per session
- Standardized response persistence by endpoint and timestamp
- Pagination support for large collections
- Notebook-friendly API for iterative research workflows

## Ethics and data handling
- Public YouTube data only
- Public identifiers (videoId, channelId, commentId) preserved for reproducibility
- Dataset intended for permanent public availability
- Proposed open license: CC BY 4.0 (to be confirmed)

## 12‑month milestones (high‑level)
- Months 1–3: data collection pipeline refinement and dataset expansion to 100+ records
- Months 4–6: annotation guidelines and first labeled release
- Months 7–9: multimodal feature engineering and model evaluation
- Months 10–12: refinement, interpretability analysis, and final dataset release

## Expected outputs
- Public dataset of Portuguese financial misinformation cases
- Multimodal baseline models and evaluation report
- Reproducible collection and analysis pipeline

## Why YouTube support matters
YouTube is the dominant platform for video-based financial advice. Access to its Researcher Program enables responsible, large-scale data collection, robust evaluation, and transparent methodology tailored to public interest outcomes.
