# Environment variables

The designer picks a backend at startup from the environment. The first
configured backend wins; the rest are ignored. If none are set, the app
still runs but the **Assist** buttons and the **Generate** tab show a hint.

## Precedence

1. **Cloudera AI Inference** — if both `CDP_INFERENCE_ENDPOINT` and
   `CDP_INFERENCE_API_KEY` are set.
2. **OpenAI-compatible** — if `OPENAI_API_KEY` is set.
3. **Anthropic** — if `ANTHROPIC_API_KEY` is set.

The active backend is shown in the sidebar.

## Full reference

| Variable | Used by | Default | Description |
|---|---|---|---|
| `CDP_INFERENCE_ENDPOINT` | Cloudera | — | Base URL of the OpenAI-compatible endpoint, e.g. `https://ml-workspace.example.com/v2/models/inference/openai/v1`. |
| `CDP_INFERENCE_API_KEY` | Cloudera | — | Bearer token for the endpoint. |
| `CDP_TOKEN` | Cloudera | — | Fallback name for the bearer token — read only if `CDP_INFERENCE_API_KEY` is unset. |
| `CDP_INFERENCE_MODEL` | Cloudera | `meta-llama-3-1-70b-instruct` | Model id served on the endpoint. |
| `OPENAI_API_KEY` | OpenAI-compat | — | API key. |
| `OPENAI_BASE_URL` | OpenAI-compat | `https://api.openai.com/v1` | Override to point at a local vLLM, TGI, LiteLLM proxy, etc. |
| `OPENAI_MODEL` | OpenAI-compat | `gpt-4o-mini` | Model id. |
| `ANTHROPIC_API_KEY` | Anthropic | — | API key. |
| `ANTHROPIC_MODEL` | Anthropic | `claude-opus-4-8` | Model id. |

The Cloudera AMP manifest at [`.project-metadata.yaml`](../.project-metadata.yaml)
prompts for these on first deploy. All are optional — the designer runs without
an LLM; Assist and Generate show a configuration hint until a backend is set.
See also [`cml-deploy.md`](cml-deploy.md).
