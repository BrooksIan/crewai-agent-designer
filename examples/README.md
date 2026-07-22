# Examples

Hand-picked crews that were built in the Agent Designer and exported. Each
subdirectory contains the same layout the app produces at export time:

```
<crew>/
├── config/agents.yaml
├── config/tasks.yaml
├── crew.py
├── requirements.txt
├── README.md            # instructions for running this specific crew
└── design.json          # source design — reopen in the designer
```

## `simple-research-crew/`

A two-agent, two-task sequential crew: a researcher gathers findings using
web search, then a writer turns them into a two-paragraph brief. Uses one
tool (`SerperDevTool`). Runs against any OpenAI-compatible LLM.

Open `design.json` in the Agent Designer to see the source, or run
directly:

```bash
cd simple-research-crew
pip install -r requirements.txt
export OPENAI_API_KEY="…"
export SERPER_API_KEY="…"
python -c "from crew import ResearchCrew; print(ResearchCrew().crew().kickoff(inputs={'topic': 'small language models'}))"
```

### `simple-research-crew/cas-workflow/`

The Cloudera Agent Studio bundle for the *same* design. Contains
`workflow_template.json` at the top plus `studio-data/tool_templates/…`
with `tool.py` + `requirements.txt` for each tool — everything CAS needs
in a single upload. See `docs/exported-yaml.md` for the schema, the
upload procedure, and the list of Design fields that don't survive the
projection (task context, per-task tools, etc.).
