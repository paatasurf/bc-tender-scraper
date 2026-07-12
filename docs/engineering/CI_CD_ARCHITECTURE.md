# CI/CD Architecture — bc-tender-scraper

**Workflow:** `.github/workflows/quality-gate.yml`  
**Target branch:** `master`  
**Deployment target:** Railway production environment

## Pipeline overview

```mermaid
flowchart TD
    subgraph Developer
        A[Developer pushes branch] --> B[Open Pull Request to master]
    end

    subgraph GitHubActions [GitHub Actions — Quality Gate]
        B --> C[Stage 1: Ruff]
        B --> D[Stage 1: Black --check]
        B --> E[Stage 2: pytest]
        B --> F[Stage 3: OpenCode Review]
        C --> G[Stage 4: Quality Gate aggregate]
        D --> G
        E --> G
        F --> G
        G -->|success| H[Stage 5: Deploy to Railway]
        G -->|failure| I[Block merge & deployment]
    end

    subgraph GitHub [GitHub branch protection]
        G --> J{Quality Gate passed?}
        J -->|yes| K[PR can be merged after review]
        J -->|no| L[Merge button disabled]
    end

    subgraph Railway [Railway]
        K --> M[Auto-deploy from master]
    end

    style I fill:#ffcccc
    style K fill:#ccffcc
```

## Stage descriptions

| Stage | Job | Purpose | Failure behavior |
|-------|-----|---------|------------------|
| 1 | **Ruff** | Static analysis (syntax errors, undefined names, serious defects) | Fails pipeline immediately |
| 1 | **Black** | Format check (`black --check`) | Fails pipeline immediately |
| 2 | **pytest** | Unit tests + import smoke + compileall | Fails pipeline immediately |
| 3 | **OpenCode Review** | Optional AI code review (runs only on PRs, skipped if no API key) | Fails pipeline if configured and review fails |
| 4 | **Quality Gate** | Aggregate gate; fails if any upstream stage failed | Blocks merge and deployment |
| 5 | **Deploy to Railway** | Triggered only on `master` push after Quality Gate succeeds | Skipped if token not configured |

## Branch protection rules

```mermaid
flowchart LR
    A[Pull Request opened] --> B{1+ approving review?}
    B -->|no| C[Merge blocked]
    B -->|yes| D{Quality Gate passed?}
    D -->|no| E[Merge blocked]
    D -->|yes| F{Branch up-to-date?}
    F -->|no| G[Update branch required]
    F -->|yes| H[Merge allowed]
    H --> I[master updated]
    I --> J[Railway auto-deploys]
```

## Key controls

- **No direct pushes to `master`**: Branch protection requires a pull request.
- **Mandatory review**: At least one approving review is required.
- **Mandatory quality gate**: The aggregate `Quality Gate` job must pass before merge.
- **Admin enforcement**: Protection rules apply to repository administrators.
- **Deployment gating**: Railway deployment only happens from `master`, and `master` can only be updated after Quality Gate passes.

## Notes

- The `Deploy to Railway` job is currently a placeholder. To make it the exclusive deployment path:
  1. Disable auto-deploy in Railway project settings.
  2. Store a Railway token as the `RAILWAY_TOKEN` GitHub secret.
  3. Replace the placeholder step with `railway up` or the Railway GitHub Action.
- Until the explicit deployment job is enabled, the existing Railway auto-deploy is gated by branch protection: it only runs after a PR is merged to `master`, and merging requires a green Quality Gate.
