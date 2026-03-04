# Research Proposal: Multimodal Financial Misinformation in Portuguese YouTube

## 1. Team and affiliation
- Diego de Almeida Miranda (MSc student, UNIFESP)
- Lilian Berton (advisor, UNIFESP)

## 2. Background and motivation
Financial advice has increasingly migrated to video platforms. While this expanded access has benefits, it also enabled the rapid spread of misleading or fraudulent financial claims. This is especially acute in Portuguese-speaking contexts, where language-specific slang, regional fraud patterns, and limited specialized datasets make detection and moderation harder. A multimodal approach is required to capture textual, audiovisual, and engagement signals of misinformation in this domain.

## 3. Research objectives
- Build a Portuguese-language dataset of financial misinformation from YouTube videos and related engagement artifacts.
- Develop a multimodal framework combining text, audiovisual, and engagement features.
- Evaluate detection performance against baseline approaches and provide interpretability insights.

## 4. Research questions
- Which multimodal signals most reliably distinguish misleading financial content in Portuguese?
- How do engagement signals (comments, threads, metadata) contribute to detection performance?
- What are the tradeoffs between recall and precision under strict quota constraints?

## 5. YouTube data sources and intended use
The project will use public data from the YouTube Data API v3:
- `search.list` to discover candidate videos with Portuguese financial terms
- `videos.list` to retrieve metadata, statistics, and content details
- `channels.list` to capture creator context
- `commentThreads.list` and `comments.list` to analyze community responses and filter candidates
- `captions.list` to obtain aligned text signals when available

## 6. Data acquisition workflow
1. **Candidate discovery**
   - Use search queries in Portuguese financial terminology to surface candidate videos.
2. **Metadata enrichment**
   - Fetch video and channel metadata for each candidate.
3. **Caption retrieval**
   - Query captions when available for transcript-level analysis.
4. **Engagement collection**
   - Collect comment threads and comments for signals of perception, contestation, and community feedback.
5. **Filtering and prioritization**
   - Use comment signals and metadata to prioritize candidates for full annotation.

## 7. Quota and efficiency strategy
- Track per-endpoint costs and total session credits.
- Minimize expensive `search.list` calls by caching terms and batching.
- Use lower-cost endpoints (`videos.list`, `channels.list`, `commentThreads.list`, `comments.list`) to expand context.
- Persist all raw responses for traceability and re-use.

## 8. Annotation plan and quality assurance
- Define a Portuguese-specific annotation schema for financial misinformation, including categories and severity.
- Two-stage annotation: initial labeling followed by adjudication.
- Periodic inter-annotator agreement checks.

## 9. Multimodal framework
- **Textual features**: captions, titles, descriptions, and comment content.
- **Audiovisual signals**: derived from metadata and external processing of video content.
- **Engagement features**: like/dislike ratios (when available), view counts, comment volume, and thread dynamics.
- Model evaluation against text-only and metadata-only baselines.

## 10. Risks and mitigations
- **Low yield (<1% usable videos)**: broaden search lexicon and refine filtering via comments.
- **Quota constraints**: credit monitoring and batching strategies.
- **Caption availability**: fallback to metadata and comment signals.
- **Bias in selection**: diversify search terms and channels.

## 11. Data governance and ethics
- Only public YouTube data will be stored.
- Public identifiers (videoId, channelId, commentId) will be preserved for reproducibility.
- Dataset intended for permanent public availability.
- Proposed open license: CC BY 4.0 (to be confirmed).

## 12. Timeline (12 months)
- Months 1–3: pipeline refinement and dataset expansion beyond 100 records.
- Months 4–6: annotation guidelines, labeling, and first dataset release.
- Months 7–9: multimodal feature engineering and baseline evaluation.
- Months 10–12: refinement, interpretability analysis, and final dataset release.

## 13. Tooling and client usage (this repository)
The project uses `youtube_data_pipeline`, a Python 3.12 client built for research collection with:
- declarative endpoint registry and validation
- credit monitoring per endpoint and session
- run-scoped response storage with a JSONL manifest index
- pagination support and notebook-friendly APIs

Relevant references:
- `docs/design.md` for architecture and design rationale
- `docs/usage.md` for collection examples

## 14. Expected outcomes
- A public Portuguese-language dataset of financial misinformation videos.
- A multimodal detection framework and evaluation report.
- A reproducible data collection workflow with transparent quota management.
