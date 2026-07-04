from pipeline.parsed_identity_canonical_merge import (
    is_generic_business_name,
    is_generic_bucket_company_name,
)
from pipeline.company_matching import normalize_vendor_name


def test_generic_business_name_flags_short_and_trade_names():
    assert is_generic_business_name("Demolition Ltd.")
    assert is_generic_business_name("Construction Inc")
    assert not is_generic_business_name("LQ Design GROUP Ltd")


def test_generic_bucket_company_name():
    assert is_generic_bucket_company_name("Architect")
    assert is_generic_bucket_company_name("construction")
    assert not is_generic_bucket_company_name("Kerr Construction")
    assert not is_generic_bucket_company_name("MWL Demolition")
    assert not is_generic_bucket_company_name("QI LI DBA: LQ Design GROUP Ltd")


def test_ledcor_construction_norm_key():
    assert normalize_vendor_name("Ledcor Construction Limited") == "ledcorconstruction"


def test_applicant_only_ledcor_key_variants_share_norm():
    assert normalize_vendor_name("Ledcor Construction Ltd.") == "ledcorconstruction"
