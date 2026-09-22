"""Google ADK agent used for the AICT observability demo."""

from google.adk.agents import Agent
from opentelemetry.sdk.trace import SpanProcessor
from vertexai import agent_engines

from policy_tool import get_demo_policy_status


class _VertexAIProviderSpanProcessor(SpanProcessor):
    """Add the required provider value to Vertex inference spans.

    The Google ADK instrumentation creates the GenAI operation span, but the
    current runtime omits `gen_ai.provider.name`. Set it at span creation so
    every exporter sees the standard attribute before the span is completed.
    """

    _INFERENCE_OPERATION_PREFIXES = ("generate_content", "chat", "text_completion")

    def on_start(self, span, parent_context=None):
        operation = span.attributes.get("gen_ai.operation.name")
        if operation in self._INFERENCE_OPERATION_PREFIXES or span.name.startswith(
            self._INFERENCE_OPERATION_PREFIXES
        ):
            span.set_attribute("gen_ai.provider.name", "gcp.vertex_ai")

    def on_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30_000):
        return True


root_agent = Agent(
    model="gemini-2.5-flash",
    name="aict_observability_demo_agent",
    description="Synthetic policy assistant for AI Control Tower observability demos.",
    instruction=(
        "You are a concise AI governance demo assistant. Use the policy tool when "
        "asked about a policy. State that all results are synthetic demo data."
    ),
    tools=[get_demo_policy_status],
)


def _instrument_with_cloud_trace(project_id):
    """Keep the default Agent Engine telemetry and also export spans to Cloud Trace.

    ponytail: AdkApp's built-in enable_tracing only pushes OTLP to Agent Engine's
    own trace store, which the ServiceNow GCP Cloud Trace collector cannot read.
    This hook reuses the default setup, then attaches the Cloud Trace exporter
    to the same global tracer provider so both backends receive every span.
    """
    from vertexai.agent_engines.templates.adk import _default_instrumentor_builder

    _default_instrumentor_builder(
        project_id, enable_tracing=True, enable_logging=False
    )

    import opentelemetry.trace as trace_api

    provider = trace_api.get_tracer_provider()
    if not hasattr(provider, "add_span_processor"):
        return None
    if not getattr(provider, "_aict_vertex_provider_processor_attached", False):
        provider.add_span_processor(_VertexAIProviderSpanProcessor())
        provider._aict_vertex_provider_processor_attached = True
    if getattr(provider, "_aict_cloud_trace_attached", False):
        return None  # set_up ran again in this process; avoid double export
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        import google.auth
        from google.auth.transport import requests as requests_auth

        credentials, adc_project = google.auth.default()
        session = requests_auth.AuthorizedSession(credentials)
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    session=session,
                    endpoint="https://telemetry.googleapis.com/v1/traces",
                )
            )
        )
        provider._aict_cloud_trace_attached = True
    except Exception:
        # Cloud Trace export is best-effort; never break agent serving.
        pass
    return None


app = agent_engines.AdkApp(
    agent=root_agent,
    enable_tracing=True,
    instrumentor_builder=_instrument_with_cloud_trace,
)
