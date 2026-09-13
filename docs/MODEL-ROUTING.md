# Model Routing Policy

## Goal

Use free remote inference wherever it is sufficiently capable and policy-compatible, while preserving reproducibility, privacy and local degraded operation.

The architecture must not depend on any specific free model remaining available indefinitely.

## Seed OpenRouter route

B022 introduces one stable internal model alias:

```text
fzh-free-auto
    -> LiteLLM
    -> openrouter/openrouter/free
    -> OpenRouter openrouter/free
```

`fzh-free-auto` is the name Hermes/seed workers use. Provider/model implementation details remain behind the gateway.

OpenRouter's free router is intentionally treated as **opportunistic capacity**, not a deterministic model identity. The system records the actual returned model when evaluating work, and later B026 can prefer benchmarked explicit free models by role.

The OpenRouter API key exists only in the local LiteLLM secret environment. Hermes and normal workers receive a LiteLLM client credential, not the provider key.

## Routing order

For an eligible task as the system matures:

```text
specific benchmarked free model
  -> alternate benchmarked free model
  -> alternate approved free provider
  -> fzh-free-auto / OpenRouter free router
  -> local Ollama model
  -> queue/retry
```

Paid fallback is disabled by default. It may only be enabled by explicit owner budget policy.

## Free-tier exhaustion

Free-first does not mean infinite retry.

The seed engineering/job layer must impose finite model-call, retry and wall-clock budgets. Rate limits, provider exhaustion or temporary free-model absence should produce a deferred/retryable job state rather than recursive retry storms.

Until B024 local Ollama is available, exhaustion pauses/dequeues eligible work. After B024, low-risk compatible tasks may degrade to the approved local model. Paid routing remains disabled unless owner policy explicitly changes.

## Current-free discovery

`scripts/models/discover-openrouter-free.py` reads OpenRouter's model catalogue and reports models whose prompt and completion token prices are both zero.

This output is **discovery input only**. It must not directly rewrite production routing. Candidate free models must pass privacy/policy eligibility and later evaluation before becoming explicit preferred routes.

## Task roles

Model selection is role-based rather than hard-coded globally.

Initial roles:

- planner/reasoner;
- coding implementer;
- code reviewer;
- QA/test critic;
- security reviewer;
- extraction/classification;
- summarisation;
- local degraded assistant.

The same model/context that implements a change should not normally serve as the only independent reviewer for that change.

## Model registry fields

Every model/provider candidate should record at least:

```yaml
provider: example
model: example/model
cost_class: free
context_window: 0
tool_calling: false
structured_output: false
privacy:
  sensitive_data_allowed: false
  training_allowed: unknown
  retention: unknown
benchmarks:
  coding: null
  review: null
  tool_use: null
  latency_ms: null
  availability: null
status: candidate
```

Unknown privacy properties default to the more restrictive routing decision.

## Data classification

Initial classes:

- **PUBLIC** — may use any approved provider.
- **INTERNAL** — only providers approved for internal data.
- **CONFIDENTIAL** — approved zero-retention/no-training provider or local processing.
- **SECRET** — never intentionally sent to an LLM provider; handle through dedicated secret mechanisms.

Provider price does not override data policy. `fzh-free-auto` does not itself grant permission to route a classification remotely.

## Continuous free-model evaluation

The system may periodically discover free model candidates and benchmark them against a stable evaluation set.

Metrics include:

- task success;
- coding correctness;
- review defect-detection rate;
- structured-output adherence;
- tool-call reliability;
- latency;
- rate-limit/error frequency;
- effective context;
- policy/privacy eligibility.

Routing preferences may be updated automatically only within already approved provider/data boundaries.

A new provider itself requires the appropriate security/privacy approval.

## Shadow evaluation

Material model/prompt/router changes should first run in shadow mode where possible:

```text
real request
  -> current production model -> actual result
  -> candidate model          -> hidden result -> evaluator
```

Promotion requires evidence that the candidate meets or exceeds the defined quality/safety threshold.

## Local inference

The Dell XPS must retain useful offline/degraded capability through Ollama.

`llmfit` or equivalent measured benchmarking should select feasible local models based on the real CPU/RAM environment rather than assumptions about parameter count.

Initial local workloads should favour inexpensive tasks such as:

- classification;
- extraction;
- summarisation;
- basic log analysis;
- simple planning;
- emergency/degraded chat.

Only one heavy local model should be loaded at a time initially unless benchmarks demonstrate sufficient headroom.

## Resource/cost policy

Each job has finite retry, time and model-call limits. If free capacity cannot complete a task inside its budget, the task should pause/queue or use an explicitly permitted fallback rather than spin indefinitely.
