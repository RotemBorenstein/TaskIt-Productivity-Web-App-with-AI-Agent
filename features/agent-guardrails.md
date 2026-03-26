# Agent Guardrails

This note documents the TaskIt assistant guardrails feature as it exists in the repo today. It is meant to capture the main decisions, tradeoffs, implementation details, and current limits without inventing anything beyond the current code and the testing that was actually run.

## Purpose

The guardrails were added to keep the TaskIt assistant focused on its intended scope and to reduce common failure modes:

- unrelated general-chat use
- direct prompt-injection attempts
- unsafe use of retrieved note content
- note-search requests that should fail closed instead of drifting into general knowledge
- blocked or fallback turns contaminating future chat memory

## Main Decisions Taken

### 1. Use a layered guardrail design, not just a stronger prompt

The feature was built as several checkpoints around the agent instead of relying on the assistant prompt alone:

- request classification before the agent runs
- per-request tool scoping
- retrieval sanitization for RAG note content
- server-side enforcement in the Django endpoint
- safe memory persistence rules
- safe chat rendering in the frontend

This is implemented mainly in:

- [main/agent/guardrails.py](c:/Users/rotem/OneDrive/technion/TaskIt/main/agent/guardrails.py)
- [main/views/agent_views.py](c:/Users/rotem/OneDrive/technion/TaskIt/main/views/agent_views.py)
- [main/agent/agent_tools.py](c:/Users/rotem/OneDrive/technion/TaskIt/main/agent/agent_tools.py)

### 2. Guard decisions are explicit modes

Incoming assistant requests are classified into explicit modes:

- `block_injection`
- `block_off_topic`
- `rag_only`
- `read_only`
- `write_allowed`

This keeps the backend control simple and makes tool access deterministic.

### 3. `rag_only` requests are treated specially

For note-search style requests, the design intentionally narrows the assistant:

- only `search_knowledge` should be available
- the endpoint now inspects `intermediate_steps` to see whether `search_knowledge` was actually called
- `search_knowledge` returns machine-readable status markers:
  - `RAG_STATUS: found`
  - `RAG_STATUS: not_found`

The endpoint uses these statuses to decide whether a `rag_only` turn should succeed or fail closed.

### 4. Retrieved notes are treated as untrusted input

Retrieved note content is not trusted as instructions. The implementation does two things:

- sanitize note content and note metadata labels locally
- optionally run the Groq-backed retrieval filter over retrieved note snippets

The retrieval filter can mark snippets as:

- `allow`
- `redact`
- `drop`

### 5. Blocked and fallback turns should not enter future memory

The assistant stores chat history in the database, but not every turn is allowed back into prompt memory. The `include_in_memory` flag was added so blocked or fallback turns can remain visible in history while being excluded from future memory windows.

### 6. RAG relevance moved from lexical matching to vector distance

The earlier token/substring relevance filter was replaced with pgvector cosine-distance metadata. Retrieved documents now carry a `distance` value, and `search_knowledge` filters out results whose distance is above the configured threshold.

Current setting:

- `AGENT_RAG_MAX_DISTANCE` with a repo default of `0.45`

## Tradeoffs and Observed Behavior

### Tradeoff: stronger fail-closed behavior vs. false not-found answers

The current design prefers returning a safe TaskIt-scoped not-found response over letting the assistant answer from unrelated or weak context. This improves safety, but it can also reject valid mixed-content note queries.

Observed example from manual Groq testing:

- `What did I write about Kubernetes?` now correctly fails closed
- `What did I write in my security notes?` still over-filters and returns not-found, even though there is relevant safe content in the note

### Tradeoff: external guardrail model vs. latency/rate limits

The Groq-backed guard path improves behavior, but it adds latency and can hit provider rate limits. During manual Groq runs, retrieval filtering was one of the slowest parts of the flow.

### Tradeoff: caching repeated note lookups vs. perfect retry behavior

`search_knowledge` includes lightweight exact-query caching inside a single request to reduce repeated identical retrieval work. This is useful for duplicate retries, but it is also one of the areas that still has known edge cases.

## Main Implementation Details

### Backend modules

- [main/agent/guardrails.py](c:/Users/rotem/OneDrive/technion/TaskIt/main/agent/guardrails.py)
  - request guard modes
  - Groq-backed request classifier
  - Groq-backed retrieval filter
  - local fallback classifier and local note sanitization
  - stable RAG result markers

- [main/views/agent_views.py](c:/Users/rotem/OneDrive/technion/TaskIt/main/views/agent_views.py)
  - agent endpoint flow
  - guard invocation before agent execution
  - tool scoping by guard decision
  - server-side `rag_only` enforcement from `intermediate_steps`
  - fail-closed handling for blocked and max-iteration cases

- [main/agent/agent_tools.py](c:/Users/rotem/OneDrive/technion/TaskIt/main/agent/agent_tools.py)
  - `search_knowledge`
  - retrieval sanitization
  - machine-readable RAG found/not-found tool results
  - pgvector distance filtering

- [main/agent/rag_utils.py](c:/Users/rotem/OneDrive/technion/TaskIt/main/agent/rag_utils.py)
  - pgvector retrieval adapter
  - retrieved document metadata now includes cosine `distance`

### Settings and env

Current assistant-guard settings live in [TaskIt/settings.py](c:/Users/rotem/OneDrive/technion/TaskIt/TaskIt/settings.py), including:

- `AGENT_GUARD_ENABLED`
- `AGENT_GUARD_PROVIDER`
- `AGENT_GUARD_MODEL`
- `AGENT_GUARD_API_BASE_URL`
- `AGENT_GUARD_TIMEOUT_SECONDS`
- `AGENT_GUARD_RAG_SCAN_ENABLED`
- `AGENT_RAG_MAX_DISTANCE`

Container env passthrough is defined in [docker-compose.yml](c:/Users/rotem/OneDrive/technion/TaskIt/docker-compose.yml).

### Memory and chat UI hardening

- [main/models.py](c:/Users/rotem/OneDrive/technion/TaskIt/main/models.py)
  - `AgentChatMessage.include_in_memory`
- [main/agent/memory_utils.py](c:/Users/rotem/OneDrive/technion/TaskIt/main/agent/memory_utils.py)
  - only turns with `include_in_memory=True` are loaded into future prompt memory
- [main/templates/main/base.html](c:/Users/rotem/OneDrive/technion/TaskIt/main/templates/main/base.html)
  - assistant chat rendering was hardened to avoid unsafe `innerHTML` insertion

## Observability

No dedicated metrics system was added for this feature. Current observability relies mainly on structured logs.

Important log events currently in code:

- `assistant_guard_result`
- `assistant_guard_decision`
- `assistant_guard_unavailable`
- `assistant_guard_rag_filter`
- `assistant_guard_rag_filter_fallback`
- `assistant_agent_max_iterations`

### How we will know if this fails in production

The current lightweight signals are:

- spikes in `assistant_guard_unavailable`
- frequent `assistant_agent_max_iterations` on note-lookups
- frequent `assistant_guard_rag_filter_fallback`
- valid user note-lookups returning not-found too often
- user reports that normal note queries are blocked or return empty results

At the moment, the best production monitoring path is to watch these logs and compare them to user-reported behavior.

## Testing That Was Actually Run

### Docker-backed Django tests

This focused suite was run in Docker:

- `AssistantGuardrailApiTests`
- `AssistantGuardrailMemoryTests`
- `AssistantRagFilteringTests`
- `NotesApiTests`

Latest focused result during this work:

- `Ran 15 tests`
- `OK`

### Manual Groq-backed checks

Manual Docker + Groq checks were run with seeded test notes. Observed results:

- `What did I write about deployment?` -> passed
- `What did I write about OAuth?` -> passed
- `What did I write about Kubernetes?` -> passed with safe not-found
- `What did I write in my security notes?` -> still returned safe not-found instead of the expected summarized note content

## Current Known Limitations

These are important to keep in mind because they affect whether the feature is ready for broad production rollout.

- The feature is promising, but not fully production-ready as a default-on behavior.
- Mixed-content note queries can still be over-filtered by the Groq retrieval guard path.
- Reviewer analysis identified that an earlier successful RAG hit can still be overwritten if the agent later ends in max-iterations.
- Reviewer analysis also identified that the duplicate-query cache currently ignores `top_k`, which can affect broader retries within the same request.
- Docker compose currently provides explicit defaults for guard env vars; production behavior depends on the actual deployed env values, not just Django defaults.

## Practical Verdict

The current guardrails improve the assistant noticeably for:

- direct injection blocking
- off-topic blocking
- basic note lookup behavior
- safe failure on absent topics

But the current implementation still fits better as:

- internal use
- feature-flagged rollout
- limited beta

rather than a fully trusted, default-on production feature for all users.
