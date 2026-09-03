"""Company-enrichment worker service (docs/COMPANY_CONTACT_PROVIDER_PHASE3C_EXECUTION_PLAN.md).

A separate, standalone HTTP service -- NOT imported by, and never imported
from, api/internal.py or pipeline/company_enrichment/orchestrator.py. This
package is deliberately not wired into anything that runs in the main
API's own process; it exists to be deployed as its own Railway service
(not done by this change -- see the execution plan's own "not built by
this plan" scoping for the Dockerfile/railway.toml/deploy steps).
"""

from __future__ import annotations
