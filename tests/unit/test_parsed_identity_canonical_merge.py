from pipeline.parsed_identity_canonical_merge import is_generic_business_name
from pipeline.company_matching import normalize_vendor_name


def test_generic_business_name_flags_short_and_trade_names():
    assert is_generic_business_name("Demolition Ltd.")
    assert is_generic_business_name("Construction Inc")
    assert not is_generic_business_name("LQ Design GROUP Ltd")


def test_ledcor_construction_norm_key():
    assert normalize_vendor_name("Ledcor Construction Limited") == "ledcorconstruction"


def test_applicant_only_ledcor_key_variants_share_norm():
    assert normalize_vendor_name("Ledcor Construction Ltd.") == "ledcorconstruction"
