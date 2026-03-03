# Assessment of the YouTube Data API v3 Developer Guide

## Assessment objective

This assessment translates the key points of the API guide into a library design aimed at data collection for research on multimodal financial fake news in Portuguese.

## Critical points from the guide and architectural implications

1. **Credit-based quota system**
   - Each method has a credit cost.
   - `search.list` has a high cost (100 credits).
   - Read endpoints like `videos.list`, `channels.list`, `commentThreads.list`, and `comments.list` tend to have a low cost (1 credit).
   - **Implication**: the client needs to record cost per call and cumulative per session for budget management.

2. **Pagination with `nextPageToken`**
   - Large collections require multiple calls per endpoint.
   - **Implication**: the client should support `max_pages` and persist responses per page.

3. **Required parameters per endpoint**
   - Example: `part` is required in most methods.
   - **Implication**: declarative parameter validation per endpoint to reduce errors in notebooks and jobs.

4. **Heterogeneous responses with rich metadata**
   - Snippets, statistics, content details, captions, and comments have different structures.
   - **Implication**: persist raw payload in JSON to maintain traceability and enable later feature engineering.

5. **Use in research and data engineering environments**
   - Interactive (Jupyter) and scheduled (batch) execution.
   - **Implication**: a simple Python API (`fetch`) + a reusable recurring orchestration mechanism.

## Endpoints prioritized in the project

- `search.list`
- `videos.list`
- `comments.list`
- `channels.list`
- `captions.list`
- `commentThreads.list`

## Recommendation for the research scenario

For the multimodal financial fake news project, it is recommended to:

- use `search` for the initial discovery of Portuguese-language financial videos;
- enrich with `videos` + `channels` for metadata and production context;
- use `captions` as a text input aligned with audiovisual content;
- use `commentThreads` and `comments` for signals of engagement, perception, and possible community contestation.
