# Findings: AICT evaluation scoring never returns results for a GCP agent

Investigation log and defect report. Written 2026-09-22.

**Read this before re-investigating.** It records what has already been ruled out, with
record IDs and timestamps, so the same ground does not get covered twice.

---

## Summary

Traces from a Google ADK agent on Vertex AI Agent Engine ingest into ServiceNow AI
Control Tower correctly and completely — spans, prompts, completions, token counts, tool
calls. They are tagged for evaluation. Evaluation scores are **never** returned.

No error is surfaced anywhere: the trace connection shows `Success`, the asset shows no
missed traces and no retries, and the provider poll runs on schedule.

## Environment

| | |
|---|---|
| Instance | `demoalectriallwfab153028` |
| AI Control Tower | 7.0.2 |
| AICT Evaluations | 3.0.2 |
| AI Trace Collector | 3.0.2 |
| AI Control Tower Core | 8.0.8 |
| CI | `cmdb_ci_function_ai` `60e2627aebdb0310e0459eb6b2d15fd2` |
| Asset | `alm_ai_system_digital_asset` `64e2627a87db031003eea8e30cbb3539` |
| Scoring provider | Traceloop, ServiceNow-provisioned, `external_instance_id c66505b9-df44-465f-bb22-0bf0ea1e5bb6` |
| Reference working instance | `aeidscdemo3` (Azure AI Foundry collector) |

## Reference case — Trace #00002377

`otel_id 55a91926863beb50ee0b42e1eb6824cd`. Times are instance display time.

| Time | Event |
|---|---|
| 15:30:07 | config sync to Traceloop completed (`sync_pending` → `false`) |
| 15:32:50 | agent invoked; 5 spans including `execute_tool get_demo_policy_status` |
| 15:33:58 | exporter handshake `GET /api/sn_ai_observe/aict/instance_info` → **200** as `aict.external.trace.sender` |
| 15:43:27 | `scoring_provider.last_retrieved` advanced **past** the trace |
| — | `scoring_providers` empty; `quality_score` empty; `safety_score` empty |

This run was the first in which every precondition was true **simultaneously and in the
correct order**. Earlier runs each had at least one wrong.

## Verified preconditions — all green

1. **Span conformance**, read directly from the Cloud Trace v1 API (not from ServiceNow):
   inference span `generate_content gemini-2.5-flash` carries both spec-Required
   attributes — `gen_ai.operation.name = generate_content` and
   `gen_ai.provider.name = gcp.vertex_ai` — plus `gen_ai.input.messages`,
   `gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.definitions`,
   `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`.

2. **Content capture** enabled: `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=SPAN_ONLY`,
   `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`. Content confirmed present
   in the ingested `sn_ai_observe_ai_span` rows, not just in Cloud Trace.

3. **Evaluation activated** for external AI systems; metrics enabled; sample rate 95%;
   metrics added to a metric template.

4. **Trace selected for evaluation**: `sampled_metric_groups = security_metrics,sr_95`,
   which matches the working Azure instance exactly.

5. **Governance**: `governed = 1`, `evaluation_status = Enabled`, lifecycle advanced past
   *AI steward review*.

6. **Asset registration**: `sn_ai_observe_ai_trace_system` shows
   `registered_providers = traceloop` with `missed_traces`, `missed_spans`,
   `missed_sessions` and `providers_to_retry` all empty.

7. **Exporter credential** bound to a user holding `sn_ai_observe.ai_data_sender`;
   handshake returns 200 as that user.

8. **Retrieval** running; watermark advanced past the trace.

## Ruled out

| Hypothesis | Why it's dead |
|---|---|
| Missing prompt/completion content | Present in Cloud Trace *and* in the ingested spans |
| `gen_ai.provider.name` missing | Added via span processor; verified present at source |
| `gen_ai.operation.name` must be `chat` | `generate_content` is an explicitly valid spec value |
| Traceloop-side evaluators not configured | Traceloop tenant is ServiceNow-provisioned; the working instance needed no customer-side config |
| `evaluation_status` must be Enabled | The working instance's scored asset has it **Disabled** |
| Asset stuck in governance | Advanced to `Assess` / `In review`; no change |
| Metric template missing | Created; no change |
| Duplicate CIs splitting traces from config | The trace-receiving CI *is* the registered one |
| Exporter credential / auth | Handshake returns 200 as the `ai_data_sender` user |
| `evaluation_tokens` table empty proves no dispatch | That table tracks Galileo only, not Traceloop |
| Stale demo data as a reference | All scored traces on this instance are synthetic, from the hourly `Refresh Evaluations Demo Data` job (`sys_created_by = AiSecurityDemoDataGenerator`, `otel_id` like `scaled-trace-*`). Filter with `sys_created_by!=AiSecurityDemoDataGenerator` |

## What only ServiceNow engineering can check

The Traceloop tenant is ServiceNow-provisioned — AICT created it with no API key prompt —
so the customer has no visibility into it.

1. Did trace `55a91926863beb50ee0b42e1eb6824cd` actually **arrive** in the
   ServiceNow-provisioned Traceloop tenant (`external_instance_id`
   `c66505b9-df44-465f-bb22-0bf0ea1e5bb6`)?
2. If it arrived, were evaluators/monitors present, did they run, and what was the outcome?
3. If they ran, why did `ExternalMetricRetriever` not match results back to the trace?
4. **Has GCP Cloud Trace collection + evaluation scoring ever been validated end to end?**
   No working example could be found on any accessible instance — the reference instance
   runs only the Azure Foundry collector.

Question 4 is the most important. See defect 7 below: a stock ADK Agent Engine app does
not export to Cloud Trace at all, which suggests this combination may never have worked.

## Defects found along the way

All reproducible, all separate from the main issue.

**1. `Fetch and Upsert Metrics` hangs in `state=Running`.**
Observed stuck at `15:38:26` while sibling jobs in the same scope cycled normally. While
hung, `sn_ai_observe.galileo_fetch_start_time` and `scoring_provider.last_retrieved`
freeze and **no trace can ever score**. No error surfaced, no UI indication. Required a
manual job cancel to recover. A hang here is indistinguishable from "evaluation is just
slow."

**2. Config changes are silently debounced for 5 minutes before being pushed to the provider.**
`ConfigService.CONFIG_SYNC_DEBOUNCE_MS = 5 * 60 * 1000`, with a 60-minute ceiling
(`CONFIG_SYNC_MAX_DELAY_MS`). Each further edit resets `last_changed_on` and delays the
push again. There is **no UI indication** that a config change is pending sync, and
`sn_ai_observe_config_sync_request.sync_pending` is not surfaced anywhere in the product.

Consequence: anyone who changes evaluation config and immediately generates a test trace
gets a stale result and concludes the feature is broken. This cost multiple days of
misdiagnosis here. A "pending sync" indicator would have prevented all of it.

**3. Sample rate 100% appears to produce no `sr_NN` metric group.**
At 100%, six or more traces were tagged `security_metrics` only. At 95%, traces are tagged
`security_metrics,sr_95`. Needs confirmation as a genuine edge case — the change was made
at the same time as the first successful config sync, so the two are confounded. To
isolate: set the rate back to 100%, let the sync complete, and check whether an `sr_100`
group appears.

**4. The GCP trace collector mints duplicate CIs.**
It writes `cmdb_ci_function_ai.object_id` as `{engineId}:{agentName}` while the GCP
Service Graph Connector writes
`projects/.../reasoningEngines/{engineId}-{agentName}`. Identification for
`cmdb_ci_function_ai` is **`object_id`-only with no fallback** (single
`cmdb_identifier_entry`, `allow_null_attribute=false`), so the two paths can never
reconcile and each trace cycle creates a new CI.

The Azure Foundry collector does **not** exhibit this — its traces bind to the
SG-discovered CIs. Four CIs existed for one agent here. Deleting duplicates is not a fix;
the next trace recreates one.

**5. The GCP trace collector writes `install_status = 31`**, an invalid choice value with
no matching label. It also writes `manufacturer = "Google ADK"` where the SG connector
writes `"Service-now.com"`, contributing to defect 4.

**6. Provider-specific names on a provider-generic path.**
`sn_ai_observe.galileo_fetch_start_time` and `sn_ai_observe_ai_trace_system.galileo_log_stream_id`
are Galileo-named, but the job calls a generic `ExternalMetricRetriever`. On a
Traceloop-only instance this sends anyone debugging it chasing a provider that is not in
use.

**7. Documentation gap, and a possible architectural gap behind it.**
The "Add a GCP Cloud Trace connection" topic states the method works *"without requiring
SDK instrumentation"* and never mentions that ADK message-content capture must be enabled
for evaluation to function.

More seriously: `AdkApp(enable_tracing=True)` pushes OTLP only to Agent Engine's own trace
store, which the ServiceNow GCP Cloud Trace collector cannot read. Reaching Cloud Trace at
all required a custom `instrumentor_builder` hook (see `agent/demo_agent_25.py`). Taken
together with question 4 above, this suggests the documented GCP path may not work for a
stock ADK agent.

## Separate upstream bug (Google)

`opentelemetry-instrumentation-google-genai`, latest release **1.1b1** (2026-08-21), omits
the spec-Required `gen_ai.provider.name` on the inference span. It places the deprecated
`gen_ai.system = "gcp.vertex.agent"` — not a registered spec value — on the sibling
`call_llm` span instead.

Verified absent in the Cloud Trace API itself, so it is not a ServiceNow ingestion
artifact. The deployment already runs the newest release, so upgrading is not a fix.
Worked around locally by `_VertexAIProviderSpanProcessor` in `agent/demo_agent_25.py`.

Spec reference:
<https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md>

Should be reported to Google independently of the ServiceNow issue.

## Useful queries

```bash
# Real traces only — excludes the hourly synthetic demo data
now-sdk query sn_ai_observe_ai_trace \
  -q 'sys_created_by!=AiSecurityDemoDataGenerator^ORDERBYDESCstart_time' \
  -f number,otel_id,start_time,sampled_metric_groups,scoring_providers,quality_score,safety_score \
  --limit 5 -o json

# Is a config change waiting to be pushed to the provider?
now-sdk query sn_ai_observe_config_sync_request -q '' \
  -f sync_pending,first_changed_on,last_changed_on -o json

# Is the retrieval job wedged?
now-sdk query sys_trigger -q 'name=Fetch and Upsert Metrics' \
  -f name,state,next_action,sys_updated_on -o json

# Retrieval watermark vs your trace time
now-sdk query sn_ai_observe_scoring_provider -q 'name=traceloop' \
  -f title,api_endpoint,last_retrieved -o json
```

```bash
# Span attributes straight from Cloud Trace (no `view` param on v1)
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://cloudtrace.googleapis.com/v1/projects/project-2cd7ecad-fc72-4535-92e/traces/<TRACE_ID>"
```

**Note:** the trace connection's `Execution status: Success` means only that *collection
ran*. It does not mean traces were found, delivered, or evaluated. `Traces collected = 0`
with `Success` is the normal appearance of a completely idle pipeline.
