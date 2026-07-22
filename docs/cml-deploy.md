# Deploying as a Cloudera AI AMP

How to install the CrewAI Agent Designer from a git repo into a Cloudera
Machine Learning (CML) / Cloudera AI workspace as an Applied ML Prototype.

## What the AMP does on install

[`.project-metadata.yaml`](../.project-metadata.yaml) runs two tasks:

1. **Install dependencies** (`0_session-install-dependencies/`) — `pip install -r requirements.txt`
2. **Start application** (`1_app-crewai-designer/`) — Streamlit on `$CDSW_APP_PORT`

No Jobs or Models are created. Designs and workplaces persist under the
project’s `designs/` and `workplaces/` directories.

## Prerequisites

- A Cloudera AI workspace with permission to launch Applications
- Python 3.11 Standard runtime available (or edit `runtimes` in `.project-metadata.yaml` to match your catalog)
- Git host reachable with `git clone` using simple auth (or public HTTPS)
- Optional: Cloudera AI Inference endpoint, or OpenAI / Anthropic keys for Assist / Generate

## Add as a custom AMP catalog source

1. As an Administrator, open **Site Administration → AMPs**.
2. Choose either:
   - **Git Repository URL** — paste the clone URL of this repo, or
   - **Catalog File URL** — paste the raw URL to [`catalog-entry.yaml`](../catalog-entry.yaml) if your host serves raw files without login.
3. Click **Add Source**. Enable the **CrewAI Agent Designer** catalog entry if needed.
4. From the AMP catalog, launch **CrewAI Agent Designer** into a project.

See also: [custom AMP catalogs](https://docs.cloudera.com/machine-learning/cloud/applied-ml-prototypes/topics/ml-amp-custom-amp-catalog.html).

## Environment variables on first deploy

The AMP prompts for LLM settings. All are optional — leave blank to use the
visual editor without AI Assist. Precedence and details: [`env-vars.md`](env-vars.md).

| Variable | Purpose |
| --- | --- |
| `CDP_INFERENCE_ENDPOINT` / `CDP_INFERENCE_API_KEY` | Cloudera AI Inference (preferred) |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | OpenAI-compatible backend |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Anthropic backend |

You can also set or change these later in project settings, or enter keys in
the app sidebar at runtime.

## After launch

1. Wait for the install session to finish, then open the **Application** URL
   (subdomain `crewai-designer`).
2. Confirm the sidebar shows either an active LLM backend or the “no LLM”
   hint.
3. Smoke-check: create an agent on **Agents**, save a design, **Export** a
   CrewAI ZIP (and CAS ZIP if desired).
4. With LLM configured: try **Assist** and the **Generate** tab.

## Local development (non-AMP)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
streamlit run app/streamlit_app.py
```

## Hardware

| Deployment | Minimum |
| --- | --- |
| Demo / AMP launch | 2 vCPU, 8 GB RAM (install session uses 8 GB for pip) |
| Heavier concurrent use | 4 vCPU, 16 GB RAM |

No GPU required when inference is remote.
