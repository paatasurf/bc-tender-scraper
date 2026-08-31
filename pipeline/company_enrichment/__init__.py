"""On-demand company enrichment (RFC: docs/COMPANY_ON_DEMAND_ENRICHMENT_RFC.md).

Phase 0+2 only: provider abstraction (provider.py, orgbook_adapter.py) and
job orchestration (orchestrator.py) with cache-first / in-flight-dedup /
verified-field-protection. No website provider, no Google provider, no
local-LLM structuring step yet (RFC Phases 3 and 6). Nothing in this
package is wired to any route unless ENRICHMENT_ENABLED=true (default
false, see api/internal.py).
"""

from __future__ import annotations
