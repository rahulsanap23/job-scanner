"""Careers-platform adapters.

Each adapter is `scan(src, http) -> list[dict]` returning raw postings:
    {"id", "title", "locations": [str], "url", "posted": str|int|None,
     "remote": bool|None, "country": str|None}
`src` is one entry from a company's `sources` list in config/companies.yaml.
Adapters narrow the search server-side where the platform allows it (keyword
search, India filter); the shared filters in scanner/filters.py do the rest.
"""
from . import (amazon, ashby, atlassian, avature, csb, csb_rss, csb_unify, eightfold, goldman, greenhouse,
               jibe, jobsyn, jobvite, lever, oracle, phenom, smartrecruiters, visa, workable, workday)

ADAPTERS = {
    "workday": workday.scan,
    "greenhouse": greenhouse.scan,
    "lever": lever.scan,
    "ashby": ashby.scan,
    "smartrecruiters": smartrecruiters.scan,
    "oracle": oracle.scan,
    "amazon": amazon.scan,
    "eightfold": eightfold.scan,
    "phenom": phenom.scan,
    "csb": csb.scan,
    "avature": avature.scan,
    "jobvite": jobvite.scan,
    "goldman": goldman.scan,
    "csb_unify": csb_unify.scan,
    "jibe": jibe.scan,
    "jobsyn": jobsyn.scan,
    "atlassian": atlassian.scan,
    "workable": workable.scan,
    "csb_rss": csb_rss.scan,
    "visa": visa.scan,
}

# Search terms for platforms that need a keyword. "data" catches most titles;
# the rest catch skill-keyword titles that do not contain the word "data".
SEARCH_TERMS = ["data", "etl", "spark", "hadoop", "databricks", "airflow", "dbt",
                "analytics engineer", "big data"]
