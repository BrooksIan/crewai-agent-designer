# Exported project layout

Every export is a self-contained CrewAI project. Unzip it and run it — no
edits required.

```
<crew-name>/
├── config/
│   ├── agents.yaml   # one entry per agent
│   └── tasks.yaml    # one entry per task, in execution order
├── crew.py           # @CrewBase class wiring it all together
├── requirements.txt  # crewai + crewai-tools
└── README.md         # short "how to run" for the exported project
```

## `agents.yaml`

Each top-level key is the agent's `name` — which is also the `@agent` method
name in `crew.py`. Prose fields (`role`, `goal`, `backstory`) use folded
block scalars (`>`) so they read cleanly at any width. Optional fields are
omitted when they match CrewAI's default.

```yaml
researcher:
  role: >
    Senior Researcher
  goal: >
    Find and summarize recent developments on {topic}.
  backstory: >
    A veteran researcher with a knack for cutting through noise.
```

## `tasks.yaml`

Each top-level key is the task's `name` — also the `@task` method name.
Task order in the file follows the **Crew tab** ordering; if you didn't set
one, tasks appear in the order you added them.

```yaml
research:
  description: >
    Investigate {topic} using the tools available.
  expected_output: >
    A markdown list of 5–10 findings with sources.
  agent: researcher
```

## `crew.py`

Uses the canonical `@CrewBase` / `@agent` / `@task` / `@crew` decorators.
Tools declared in the designer are imported and constructed inline; agents
and tasks reference the config sections above.

```python
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import SerperDevTool


@CrewBase
class ResearchCrew:
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def researcher(self) -> Agent:
        return Agent(
            config=self.agents_config["researcher"],
            tools=[SerperDevTool()],
        )

    @task
    def research(self) -> Task:
        return Task(config=self.tasks_config["research"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
```

## Run it

```bash
pip install -r requirements.txt
export OPENAI_API_KEY="…"   # or SERPER_API_KEY if you use web search
python crew.py
```

## Re-generate vs. hand-edit

The exported files are designed to be re-generated: keep editing your
design JSON in the Agent Designer and re-export whenever the shape
changes. Once you fork the project and start hand-editing, treat the
export as the initial scaffold — don't re-export over top of your edits.

---

# Exporting to Cloudera Agent Studio (CAS)

CAS accepts a full workflow bundle — one zip containing the workflow
definition **and** every tool it references. Pick **Cloudera Agent Studio
workflow** in the Export tab's radio.

## Layout

```
<crew>_cas_workflow.zip
├── workflow_template.json
└── studio-data/
    ├── dynamic_assets/
    │   ├── agent_template_icons/           # empty; CAS populates on import
    │   ├── mcp_template_icons/             # empty
    │   └── tool_template_icons/            # empty (we don't render icons yet)
    └── tool_templates/
        └── <tool-name>_<8char-hash>/
            ├── tool.py
            └── requirements.txt
```

The `<8char-hash>` on each tool directory is a deterministic slice of the
tool name's SHA-256, so re-exports of the same design produce byte-
identical output — clean git diffs, snapshot-friendly tests.

## What's inside `workflow_template.json`

Everything CAS's importer needs, in the same schema shape as the reference
under `ClouderaAgentStudioeamples/workflow_template_5njz3ywr/`:

- **`workflow_template`** — top-level metadata: `id`, `name`,
  `description`, `process` (always `"hierarchical"` — see below), the lists
  of agent/task/tool template IDs, `manager_agent_template_id`, and a
  handful of flags (`is_conversational`, `smart_workflow`, `planning`).
- **`agent_templates`** — one entry per agent in your Design, plus a
  synthesized **Workflow Manager** the CAS runtime uses to route each user
  turn. Non-manager agents carry `tool_template_ids` referencing the tools
  they declared in the Tools tab.
- **`task_templates`** — one entry that concatenates all of your Design's
  tasks into a single instruction block, wrapped in CAS's conversational
  template (`Respond to the user's message: '{user_input}'…`) so the CAS
  session runtime can inject `{user_input}` and `{context}` at run time.
- **`tool_templates`** — one entry per tool, each pointing at
  `studio-data/tool_templates/<slug>_<hash>/` in the bundle.
- **`mcp_templates`** — empty. We don't emit MCP servers yet.

Every ID is a `uuid5` derived from a fixed namespace and the entity's name,
so the JSON is fully deterministic across re-exports.

## Manager agent — why it's always synthesized

CAS's hierarchical process expects a manager. Rather than ask users to
design one by hand, we always add a **Workflow Manager** agent to the
export. Its backstory instructs it to answer conversational turns
directly, otherwise delegate to the specialist agent that fits the
request. `use_default_manager` is set to `false` so CAS uses our
synthesized manager rather than falling back to its built-in one.

If your Design's process is `sequential`, the CrewAI export still honors
that (it emits `Process.sequential` in `crew.py`). The CAS export flips
to hierarchical because CAS's runtime is manager-driven — same intent,
different orchestration model.

## What gets dropped, and why

The Design model carries some fields CAS's schema doesn't have. The Export
tab surfaces each dropped field as a **Note** warning before you download:

| Design field | Why it drops |
|---|---|
| `Task.context` (dependencies on other tasks) | CAS uses a single manager-orchestrated task template. Dependencies dissolve into the manager's routing. |
| `Task.tools` (task-level tool overrides) | Tools bind at the agent level in CAS. |
| `Task.async_execution` | No CAS equivalent. |
| `Task.human_input` | No CAS equivalent. |
| `Task.output_file` | CAS artifacts land in the session workspace. |
| `Crew.memory` (crew-level memory) | Only per-agent `cache` is exposed in the CAS schema. |
| `Crew.task_order` | The manager decides task order at run time; ordering only survives via description prose. |

None of these block the export — they're informational. If you need full
fidelity, keep re-generating the CrewAI project alongside; it preserves
every field.

## Upload procedure

1. Download the `<crew>_cas_workflow.zip` from the Export tab.
2. In CAS, open your workspace → Workflow Templates → Import.
3. Point at the ZIP file. CAS unpacks it, registers each tool template,
   creates the agents (including the synthesized manager), and lands you
   on the workflow.
4. Run the workflow — the CAS session runtime handles `{user_input}` and
   `{context}` substitution automatically.

## The CrewAI export still works

The two targets are independent. Keep re-generating the CrewAI project
whenever you want a design artifact humans can read (or a runnable crew
outside CAS). Nothing in the CrewAI export path knows about CAS, and
nothing in the CAS export path knows about `crew.py`.

## An example bundle lives in the repo

`examples/simple-research-crew/cas-workflow/` contains the CAS export of
the same design that produces the `crew.py` next to it — unzipped so you
can browse the layout. Compare the two to see how the same `Design`
object is projected onto each target.
