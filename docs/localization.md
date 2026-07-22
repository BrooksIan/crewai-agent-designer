# Localization

The designer ships with English (`en`) and Spanish (`es`). Users flip
between them with the button at the top of the sidebar — the button label
is the *other* language, so it always tells you where you're going.

## What the language toggle affects

- **All UI strings** — tab titles, form labels, button text, error and help
  messages. Every string routed through `t(key)` in `app/i18n.py`.
- **LLM assist prompts** — the system prompts sent to the model when a user
  clicks **Assist** on an agent or task. When Spanish is active, the model
  is asked to draft `role`/`goal`/`backstory` (or `description`/
  `expected_output`) in Spanish.

## What it does *not* affect

- **Exported YAML** — persona and task text are whatever the user typed or
  the LLM produced. The tool does not translate on export.
- **The generated `README.md` inside the exported zip** — kept in English
  because it's for developers running the crew.

## Adding a new language

1. Open `app/i18n.py`.
2. Copy the entire `"en"` dict inside `STRINGS`, rename the top-level key
   to your language's two-letter code (e.g. `"fr"`), and translate every
   value. Keep the keys unchanged.
3. Update the language-toggle button in `app/streamlit_app.py`'s sidebar
   to cycle through `en → es → fr → en` (currently it toggles between
   only `en` and `es`).
4. Add matching entries to the LLM system prompts in `app/llm.py` — the
   `_AGENT_SYSTEM` and `_TASK_SYSTEM` dicts.
5. Run `pytest tests/` — nothing should fail; adding a language is
   additive.

There is no build step and no external translation service in the loop.
