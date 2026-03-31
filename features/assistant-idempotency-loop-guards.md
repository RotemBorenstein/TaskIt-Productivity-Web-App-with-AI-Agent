# Assistant Idempotency and Loop Guards

This note documents the assistant duplicate-call protection feature as it exists in the repo today. It explains what was implemented, why it exists, how it works at runtime, what is observable in production, and the main limits that still remain.

## Purpose

The TaskIt assistant can use tools multiple times in one user request. Before this feature, the model could repeat the same tool call and fall into a loop until `max_iterations` stopped it.

The guard was added to prevent that behavior from:

- creating duplicate writes
- repeating identical reads
- wasting model quota
- increasing latency
- ending too often in the generic max-iterations fallback message

## Main Decisions Taken

### 1. Keep the guard deterministic

The duplicate-call detection is algorithmic, not LLM-based.

That means the control logic does not ask another model whether the assistant is looping. Instead, it compares normalized tool signatures inside the same request.

### 2. Make the guard request-scoped

The protection applies only during one `/api/agent/` request.

It does not persist across separate chat messages. That keeps the behavior simple and avoids cross-request state problems.

### 3. Use a two-stage stop strategy

The feature does not hard-abort on the first duplicate immediately.

Instead:

- first exact duplicate: return a structured `duplicate_blocked` observation so the model can still finish naturally
- repeated duplicate of the same call: abort deterministically with a fixed user-facing answer

This keeps normal responses natural when the model cooperates, but still guarantees termination when it does not.

### 4. Apply the guard to reads and writes

The older request-local idempotency behavior mainly protected write-style tools.

This feature extends duplicate protection to all assistant tools that matter for loops, including:

- `add_task`
- `add_event`
- `get_tasks`
- `get_events`
- `analyze_stats`
- `add_subject`
- `add_note`
- `search_knowledge`

## Runtime Behavior

### Core flow

The main logic lives in:

- [main/agent/idempotency.py](c:/Users/rotem/OneDrive/technion/TaskIt/main/agent/idempotency.py)
- [main/agent/agent_tools.py](c:/Users/rotem/OneDrive/technion/TaskIt/main/agent/agent_tools.py)
- [main/views/agent_views.py](c:/Users/rotem/OneDrive/technion/TaskIt/main/views/agent_views.py)

For each tool call, the assistant builds a normalized signature from:

- tool name
- normalized arguments
- current request id
- current user id

If that signature has not been seen yet:

- the tool runs normally
- the result is stored
- a deterministic fallback answer is prepared in case the model loops later

If the same signature appears again in the same request:

- the tool does not run again
- the assistant returns a structured result like:
  - `STATUS: duplicate_blocked`
  - a short instruction to use the previous result
  - the previous result payload
  - `STOP`

If the same signature appears yet again:

- the tool guard raises `AssistantDuplicateLoopAbort`
- the endpoint catches it
- the endpoint returns a deterministic final answer without another model call

### What “deterministic final answer” means

The hard-stop answer depends on the type of prior result:

- duplicate successful create/update:
  - “The task was already created, so I stopped the duplicate action.”
- duplicate read with no result:
  - “I already checked that and found no matching tasks.”
- duplicate lookup with a prior result:
  - “I already retrieved that information and stopped the duplicate lookup.”
- generic fallback:
  - “I already ran that exact TaskIt action and stopped the duplicate repeat.”

### Prompt integration

The prompt now explicitly tells the model that:

- `STOP` means do not call that tool again
- `STATUS: duplicate_blocked` means the same tool already ran in this request
- it should use the previous result and write the final answer

This helps the model exit naturally after the first duplicate block, but the prompt is not the only protection. The hard-stop logic exists outside the model.

## Observability

The feature currently uses lightweight structured logs and Redis-backed daily counters.

Important log events:

- `assistant_tool_duplicate_blocked`
- `assistant_tool_duplicate_abort`

Important assistant signals:

- `tool_duplicate_blocked`
- `tool_duplicate_abort`

### How we will know if this fails in production

Useful failure signals are:

- `assistant_agent_max_iterations` still appears often for duplicate-like requests
- `tool_duplicate_abort` is unexpectedly high
- users report repeated “already checked” responses for calls that were not actually duplicates
- users still report duplicate task/event creation inside a single request

In practice:

- high `tool_duplicate_abort` means the model is still ignoring the first duplicate block too often, or signatures are too broad
- high `assistant_agent_max_iterations` means some loop path is still escaping this guard

## Testing That Was Added

Focused Django tests now cover:

- request-scoped duplicate guard unit behavior
- write-tool duplicate blocking
- read-tool duplicate blocking
- repeated invalid read inputs
- `search_knowledge` duplicate behavior
- real agent-executor duplicate flows using a scripted tool-calling model

Important scenarios tested:

- first duplicate returns `duplicate_blocked`
- second duplicate raises the hard-stop exception
- duplicate task creation does not create two tasks
- duplicate `get_tasks` calls do not rerun the underlying query
- repeated invalid `get_tasks` input is also guarded
- same `search_knowledge` query with different `top_k` no longer reuses the wrong cached result
- real agent flow can:
  - soft-block a duplicate and still produce a natural final answer
  - hard-stop repeated duplicate loops before `max_iterations`

## Current Limits

There is still one important edge case:

- if the tool framework rejects a malformed tool payload before the Python tool body executes, that happens before the request-scoped duplicate guard can run

So this feature covers:

- duplicate calls that reach the tool body
- duplicate invalid inputs handled inside the tool code

But it does not fully cover:

- framework-level schema validation failures that occur before the tool body starts

## Practical Result

The assistant now has layered loop protection:

- prompt-level instruction to stop after duplicate-blocked observations
- tool-level request-scoped duplicate detection
- endpoint-level hard-stop handling
- `max_iterations` remains only as the final safety net

This is a much stronger setup than relying on prompt instructions or `max_iterations` alone.
