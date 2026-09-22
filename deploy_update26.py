"""Deploy, update, invoke, or delete the AICT observability demo agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import vertexai
from vertexai import types

PROJECT = "project-2cd7ecad-fc72-4535-92e"
LOCATION = "us-central1"
STAGING_BUCKET = f"gs://{PROJECT}-aict-agent-staging"
# Use the regular runtime service account so Cloud Trace / Telemetry API tokens work.
RUNTIME_SERVICE_ACCOUNT = f"aict-agent-runtime@{PROJECT}.iam.gserviceaccount.com"

ROOT_DIR = Path(__file__).resolve().parent
AGENT_DIR = ROOT_DIR / "agent"
STATE_FILE = ROOT_DIR / "deployment.json"

# Agent Engine packages these files as top-level modules. Keep its package layout
# compatible with the deployed artifact while retaining the repository's agent/ layout.
os.chdir(AGENT_DIR)
sys.path.insert(0, str(AGENT_DIR))

# AdkApp reads the legacy global project context while demo_agent imports.
vertexai.init(project=PROJECT, location=LOCATION, staging_bucket=STAGING_BUCKET)
from demo_agent_25 import app  # noqa: E402

REQUIREMENTS = [
    "google-cloud-aiplatform[agent_engines,adk]==1.153.1",
    "google-adk==1.39.1",
    "google-cloud-trace",
    "opentelemetry-exporter-gcp-trace",
    "opentelemetry-exporter-otlp-proto-http==1.41.1",
    "opentelemetry-sdk==1.41.1",
    "opentelemetry-instrumentation-google-genai",
    "opentelemetry-instrumentation-httpx",
    "opentelemetry-instrumentation-grpc",
    "pydantic>=2,<3",
    "cloudpickle>=3,<4",
]
PROMPTS = (
    "Check the external vendor risk policy.",
    "What is the status of the model monitoring standard?",
    "Check the synthetic policy named customer-data-retention.",
)
EXPECTED_ENV_VARS = {
    "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
    "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental",
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "SPAN_ONLY",
}


def client():
    """Initialize both current and legacy Vertex Agent Engine contexts."""
    vertexai.init(project=PROJECT, location=LOCATION, staging_bucket=STAGING_BUCKET)
    return vertexai.Client(project=PROJECT, location=LOCATION)


def resource_name(remote_agent) -> str:
    return remote_agent.api_resource.name


def deployment_config(*, creating: bool = False) -> dict:
    config = {
        "requirements": REQUIREMENTS,
        "extra_packages": ["policy_tool.py", "demo_agent_25.py"],
        "staging_bucket": STAGING_BUCKET,
        "display_name": "AICT Observability Demo Agent",
        "description": "Synthetic ADK agent for ServiceNow AI Control Tower observability demos.",
        "labels": {"purpose": "aict-observability-demo", "data": "synthetic"},
        "env_vars": EXPECTED_ENV_VARS,
        "min_instances": 0,
        "max_instances": 1,
    }
    if creating:
        config["identity_type"] = types.IdentityType.SERVICE_ACCOUNT
        config["service_account"] = RUNTIME_SERVICE_ACCOUNT
    return config


def deploy():
    remote_agent = client().agent_engines.create(
        agent=app, config=deployment_config(creating=True)
    )
    STATE_FILE.write_text(
        json.dumps({"resource_name": resource_name(remote_agent)}, indent=2) + "\n"
    )
    return remote_agent


def load():
    name = json.loads(STATE_FILE.read_text())["resource_name"]
    return client().agent_engines.get(name=name)


def update():
    name = json.loads(STATE_FILE.read_text())["resource_name"]
    return client().agent_engines.update(
        name=name, agent=app, config=deployment_config()
    )


def verify_live_env(name: str):
    """Read the deployed revision back and reject stale upload-hook settings."""
    remote_agent = client().agent_engines.get(name=name)
    resource = remote_agent.api_resource.model_dump(mode="json")
    env = {
        item["name"]: item["value"]
        for item in resource["spec"]["deployment_spec"].get("env", [])
    }
    if env != EXPECTED_ENV_VARS:
        raise RuntimeError(
            f"Unexpected deployed env vars: {json.dumps(env, sort_keys=True)}"
        )
    print("Verified deployed env:", json.dumps(env, sort_keys=True))
    return remote_agent


async def invoke(remote_agent) -> None:
    for number, prompt in enumerate(PROMPTS, 1):
        print(f"\n--- prompt {number}: {prompt}")
        async for event in remote_agent.async_stream_query(
            user_id="aict-demo-user", message=prompt
        ):
            content = event.get("content", {}) if isinstance(event, dict) else {}
            for part in content.get("parts", []):
                if "text" in part:
                    print(part["text"])


def delete() -> None:
    remote_agent = load()
    remote_agent.delete(force=True)
    STATE_FILE.unlink(missing_ok=True)
    print("Deleted", resource_name(remote_agent))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    if args.delete:
        delete()
        return
    remote_agent = update() if args.update else load() if STATE_FILE.exists() else deploy()
    remote_agent = verify_live_env(resource_name(remote_agent))
    print("Agent resource:", resource_name(remote_agent))
    asyncio.run(invoke(remote_agent))


if __name__ == "__main__":
    main()
