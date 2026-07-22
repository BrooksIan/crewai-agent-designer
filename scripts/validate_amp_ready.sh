#!/usr/bin/env bash
# Offline AMP readiness checks (no CML workspace required).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== pytest =="
python -m pytest -q

echo "== AMP packaging tests =="
python -m pytest -q tests/test_amp_packaging.py

echo "== required paths =="
for p in \
  .project-metadata.yaml \
  catalog-entry.yaml \
  requirements.txt \
  LICENSE \
  0_session-install-dependencies/install_dependencies.py \
  1_app-crewai-designer/launch_app.py \
  app/streamlit_app.py \
  assets/cover.png \
  docs/cml-deploy.md
do
  test -e "$p" || { echo "missing $p"; exit 1; }
  echo "  ok $p"
done

echo
echo "Local AMP packaging checks passed."
echo "Remaining (manual, needs CML admin + git remote):"
echo "  1. git remote add origin <clone-url> && git push -u origin main"
echo "  2. Site Administration → AMPs → add Git Repository URL or Catalog File URL"
echo "  3. Launch AMP; confirm install session + Application on subdomain crewai-designer"
echo "  4. Smoke: save design, Export ZIP; with CDP_INFERENCE_* set, try Assist/Generate"
echo "See docs/cml-deploy.md"
