# CI Workflow Template (future repositories)

Copy this pattern into any new TenderScope production repo. Do **not** invent a centralized monorepo CI unless all services move into one GitHub repository.

## Minimal workflow skeleton

```yaml
name: Quality Gate
on:
  pull_request:
    branches: [master]
  push:
    branches: [master]
  workflow_dispatch:
jobs:
  quality-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: ruff check .
      - run: pytest -q --junitxml=reports/unit-junit.xml
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: ci-reports
          path: reports/
```

## Required companion files

- `requirements-dev.txt` (or `pyproject.toml` `[dev]`) with pytest + ruff
- Empty / local-only `DATABASE_URL` in CI env
- Docs link back to ecosystem guide in `bc-tender-scraper/docs/engineering/`

## After adding a repo

1. Enable branch protection + Railway Wait-for-CI  
2. Add row to the integration diagram in `CI_INTEGRATION_DIAGRAM.md`
