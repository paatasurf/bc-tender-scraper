from tests.unit.competitive_fixtures import make_company
from pipeline.competitive_intel.cohort_isolation import (
    _member_classification_text,
    GC_COHORT_ALLOWLIST_TERMS,
    is_allowed_gc_cohort_member,
)

cases = [
    ("lung", make_company(id=999, name="Danny Lung & Sharon Chen DBA: Lung Designs Group Ltd.", company_type="Unknown", project_types=["Interior Design"], total_projects=50)),
    ("aura", make_company(id=100, name="Ron Boram DBA: Aura Office Environments", company_type="Unknown", project_types=["Office Interiors"], total_projects=20)),
    ("arch", make_company(id=213, name="Khang Nguyen DBA: Architrix Design Studio", project_types=[], total_projects=324)),
]
for label, co in cases:
    t = _member_classification_text(co)
    hits = [term for term in GC_COHORT_ALLOWLIST_TERMS if term in t]
    print(label, hits, is_allowed_gc_cohort_member(co))
