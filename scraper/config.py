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

REDDIT_SUBREDDITS = ("britishcolumbia", "vancouver", "construction")
REDDIT_KEYWORDS = ("contractor", "construction", "building", "renovation", "permit")
REDDIT_SIGNALS_CSV = "reddit_signals.csv"

FEDERAL_STATUS_AWARDED = "1920"
CONTRACT_AWARDS_CSV = "contract_awards.csv"

JOB_BANK_BASE_URL = "https://www.jobbank.gc.ca"
JOB_BANK_SEARCH_PATH = "/jobsearch/jobsearch"
JOB_BANK_SEARCH_TERM = "construction"
JOB_BANK_LOCATION = "British Columbia"
JOB_BANK_JOBS_CSV = "jobs.csv"
