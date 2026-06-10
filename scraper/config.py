from __future__ import annotations

REQUEST_DELAY_SECONDS = 1.5

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

BCBID_BASE_URL = "https://www.bcbid.gov.bc.ca"
BCBID_BROWSE_URL = f"{BCBID_BASE_URL}/page.aspx/en/rfp/request_browse_public"
BCBID_DETAIL_URL_TEMPLATE = (
    f"{BCBID_BASE_URL}/page.aspx/en/bpm/process_manage_extranet/{{tender_id}}"
)

# buyandsell.gc.ca now resolves to CanadaBuys; keep legacy host as first attempt.
FEDERAL_HOSTS = (
    "https://buyandsell.gc.ca",
    "https://canadabuys.canada.ca",
)
FEDERAL_LIST_PATH = "/en/tender-opportunities"

FEDERAL_STATUS_OPEN = "87"
FEDERAL_CATEGORY_CONSTRUCTION = "155"
FEDERAL_CATEGORY_SERVICES = "154"
FEDERAL_LOCATION_BC = "1525"

CATEGORY_KEYWORDS = (
    "construction",
    "architecture",
    "architectural",
    "engineering",
    "engineer",
)

OUTPUT_CSV = "tenders.csv"
OUTPUT_JSON = "tenders.json"

OPEN_DATA_DELAY_SECONDS = 0.25

VANCOUVER_PERMITS_API = (
    "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/"
    "issued-building-permits/records"
)
BUILDING_PERMITS_CSV = "building_permits.csv"

# Subreddit -> keyword filters (empty tuple = include all posts from that subreddit).
REDDIT_SOURCES: dict[str, tuple[str, ...]] = {
    "britishcolumbia": ("construction", "permit", "building", "contractor", "development"),
    "vancouver": ("construction", "permit", "building", "development"),
    "canadaconstruction": (),
    "VancouverRealEstate": (),
    "SurreyBC": ("construction", "building"),
}
REDDIT_SIGNALS_CSV = "reddit_signals.csv"

NEWS_SOURCES: tuple[dict[str, str], ...] = (
    {"publisher": "Business in Vancouver", "url": "https://www.biv.com/rss"},
    {"publisher": "Daily Hive Vancouver", "url": "https://dailyhive.com/feed"},
    {"publisher": "Vancouver Sun Business", "url": "https://www.vancouversun.com/category/business/feed/"},
    {"publisher": "CBC British Columbia", "url": "https://rss.cbc.ca/lineup/canada-britishcolumbia.xml"},
)
NEWS_KEYWORDS = (
    "construction",
    "building",
    "developer",
    "development",
    "permit",
    "contractor",
    "architecture",
    "renovation",
    "infrastructure",
    "housing",
    "condo",
    "tower",
)
NEWS_SIGNALS_CSV = "news_signals.csv"

LINKEDIN_HASHTAGS = (
    "BCConstruction",
    "VancouverConstruction",
    "BCBuilding",
    "BCArchitecture",
    "VancouverRealEstate",
)
LINKEDIN_SIGNALS_CSV = "linkedin_signals.csv"

FEDERAL_STATUS_AWARDED = "1920"
CONTRACT_AWARDS_CSV = "contract_awards.csv"

JOB_BANK_BASE_URL = "https://www.jobbank.gc.ca"
JOB_BANK_SEARCH_PATH = "/jobsearch/jobsearch"
JOB_BANK_SEARCH_TERM = "construction"
JOB_BANK_LOCATION = "British Columbia"
JOB_BANK_JOBS_CSV = "job_bank_jobs.csv"
ARCH_TENDERS_CSV = "arch_tenders.csv"
ARCHITECTURE_CATEGORY_LABEL = "Architecture & Engineering Services"

MERX_BASE_URL = "https://www.merx.com"
MERX_ARCH_LIST_PATH = "/public/solicitations/architect-and-engineering-services-10048"
MERX_OPEN_LIST_PATH = "/public/solicitations/open"
MERX_ARCH_BC_LOCATION = "379"

COMMERCIAL_TENDERS_CSV = "commercial_tenders.csv"
COMMERCIAL_CATEGORY_LABEL = "Commercial"

BIDCENTRAL_SCIP_URL = "https://apps.bidcentral.ca/project-mapper/scip.php"
BIDCENTRAL_BOBS_URL = "https://apps.bidcentral.ca/bobs.php"
BIDCENTRAL_BASE_URL = "https://apps.bidcentral.ca"

BC_HOUSING_PROJECTS_URL = "https://www.bchousing.org/projects-partners/projects"
BC_HOUSING_BASE_URL = "https://www.bchousing.org"
BC_HOUSING_ORG_NAME = "BC Housing"

CIVICINFO_BIDS_URL = "https://www.civicinfo.bc.ca/bids"
CIVICINFO_BASE_URL = "https://www.civicinfo.bc.ca"
