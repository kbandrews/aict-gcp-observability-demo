# AICT GCP Observability Demo Agent

A Google ADK agent on Vertex AI Agent Engine, used to test **ServiceNow AI Control Tower**
trace ingestion and evaluation scoring for a GCP-hosted agent.

The agent itself is trivial by design — a policy lookup over synthetic data. It exists
only as a telemetry source. **The interesting part is the observability path, which is
currently broken at the final hop.**

## Why this repo exists

Trace ingestion works end to end. **Evaluation scoring does not**, and every precondition
ServiceNow documents or exposes has been verified green. We need engineering eyes on the
one segment we cannot see: what happens inside the ServiceNow-provisioned Traceloop
tenant.

See [`docs/findings.md`](docs/findings.md) for the full evidence chain, timestamps, and a
written-up defect report. **Please read that before re-investigating** — it records what
has already been ruled out.

## Architecture

```
ADK agent (Vertex AI Agent Engine, reasoningEngines/1208738762146709504)
  │   OTel spans, GenAI semantic conventions
  ├──> Agent Engine's own trace store        (default AdkApp behaviour)
  └──> Cloud Trace via custom OTLP exporter  (added by _instrument_with_cloud_trace)
           │
           │  MID Server "servicenow_mid" polls Cloud Trace every 60s
           ▼
    ServiceNow AI Control Tower
           │  exporter handshake: GET /api/sn_ai_observe/aict/instance_info
           │  spans + content land in sn_ai_observe_ai_span / _ai_trace
           ▼
    Traceloop (ServiceNow-provisioned tenant)  ← evaluation happens here
           │
           │  "Fetch and Upsert Metrics" job polls for results every 5 min
           ▼
    scoring_providers / quality_score / safety_score   ← NEVER POPULATED
```

Note that ServiceNow is the *consumer* of evaluated traces, not the ingestion target. The
`sn_ai_observe` scoped REST API exposes only two GET operations (`/instance_info`,
`/spans/{traceId}`) — there is no ingest endpoint.

## Status

| Link | State |
|---|---|
| Spans carry OTel GenAI semantic conventions | works |
| `gen_ai.provider.name` on the inference span | works (via local workaround, see below) |
| Prompt/completion content on spans | works |
| Cloud Trace export | works (requires the custom instrumentor, see below) |
| MID Server collection | works |
| Ingestion into AICT | works |
| Trace selected for evaluation (`sr_95` metric group) | works |
| Config sync to Traceloop | works |
| Retrieval job advancing past the trace | works |
| **Evaluation scores returned** | **fails — no error surfaced anywhere** |

## Required environment variables

Set on the Agent Engine deployment. Without the first two, spans carry no prompt or
completion content and evaluation cannot work — this is **not** mentioned in the
ServiceNow "Add a GCP Cloud Trace connection" documentation.

```
OTEL_SEMCONV_STABILITY_OPT_IN                      = gen_ai_latest_experimental
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = SPAN_ONLY
GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY         = true
```

Two traps:

- Setting `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` is **invalid** under
  the latest semantic conventions and results in **no trace data at all**. Use
  `SPAN_ONLY` (or `SPAN_AND_EVENT`).
- Do not add `OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK=upload` with the
  `UPLOAD_FORMAT` / `UPLOAD_BASE_PATH` pair. That routes content to a GCS bucket instead
  of onto spans, and AICT cannot follow a GCS reference. This was set at one point and
  silently disabled content capture.

## Known issues in this code

The source here is committed **exactly as deployed**, so these are documented rather than
fixed.

### 1. Duplicate span export

`_instrument_with_cloud_trace()` calls `_default_instrumentor_builder(..., enable_tracing=True)`
(Agent Engine's own OTLP exporter) and then attaches a *second* `BatchSpanProcessor`
exporting to `https://telemetry.googleapis.com/v1/traces`. Two exporters on one tracer
provider means **every span is exported twice** — visible as duplicated spans in Cloud
Trace. The `_aict_cloud_trace_attached` guard only prevents re-attaching within a single
process; it does not prevent the double export.

Not believed to be the cause of the scoring failure, but it doubles trace volume and
could confuse any per-span counting.

### 2. `gen_ai.provider.name` workaround — upstream Google bug

`_VertexAIProviderSpanProcessor` exists to work around a conformance bug in
`opentelemetry-instrumentation-google-genai` (latest release 1.1b1, 2026-08-21):

The inference span `generate_content gemini-2.5-flash` **omits `gen_ai.provider.name`**,
which the [OTel GenAI span conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)
mark as **Required**. ADK instead places the deprecated `gen_ai.system = "gcp.vertex.agent"`
(not a registered value) on the sibling `call_llm` span. Verified absent in Cloud Trace
itself, so it is not a ServiceNow ingestion artifact.

Upgrading cannot fix it — the deployment already runs the newest release. **This should be
reported to Google separately.**

### 3. Stray docstring text

The `_instrument_with_cloud_trace` docstring begins with the word `ponytail:`, which is
meaningless leaked text from the tooling that generated the file. Harmless, should be
deleted.

### 4. Stock AdkApp does not reach Cloud Trace

Worth calling out explicitly, because it has architectural consequences:
`AdkApp(enable_tracing=True)` pushes OTLP **only to Agent Engine's own trace store**,
which the ServiceNow GCP Cloud Trace collector cannot read. The custom
`instrumentor_builder` hook in this file exists solely to also export to Cloud Trace.

This means the documented ServiceNow "GCP Cloud Trace connection" path **cannot see a
stock ADK Agent Engine app at all**.

## Deployment

The deploy script (`deploy_update26.py`) is **not in this repo** — it was not packaged
with the agent's dependency tarball and lives in a separate environment. The source here
was recovered from the deployed artifact:

```
gs://project-2cd7ecad-fc72-4535-92e-aict-agent-staging/agent_engine/dependencies.tar.gz
```

Deployment is via `vertexai.agent_engines` (`agent_engines.create()` / `.update()`) with
`env_vars` set as above. Agent Engine has **no console UI for environment variables** —
they must be passed through the SDK call, and they are baked into the deployed revision,
so any change requires a redeploy.

To verify a deployment's live configuration, read it back from the API rather than
trusting the deploy tool's output:

```bash
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/1070565822207/locations/us-central1/reasoningEngines/1208738762146709504" \
  | python3 -m json.tool
```

This matters: a deploy tool once reported `SPAN_ONLY` while the live value was
`NO_CONTENT`.

## Environment

| | |
|---|---|
| GCP project | `project-2cd7ecad-fc72-4535-92e` (number `1070565822207`) |
| Location | `us-central1` |
| Reasoning engine | `1208738762146709504` ("AICT Observability Demo Agent") |
| Model | `gemini-2.5-flash` |
| ServiceNow instance | `demoalectriallwfab153028` |
| MID Server | `servicenow_mid` |

All policy data in `agent/policy_tool.py` is synthetic. No production or customer data.
