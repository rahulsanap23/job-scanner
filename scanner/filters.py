"""Title, location and recency rules (Part 1 of prompt/job_board_prompt.md).

Every rule here is deliberately simple and listed in plain constants so it can be
tuned without touching the scrapers.
"""
import re
from datetime import date, datetime, timedelta, timezone

RECENCY_DAYS = 30

# ---------------------------------------------------------------- normalising

_ABBREV = [
    (r"\bsr\b\.?", "senior"), (r"\bsnr\b\.?", "senior"), (r"\bjr\b\.?", "junior"),
    (r"\beng\b\.?", "engineer"), (r"\bengr\b\.?", "engineer"), (r"\bdev\b\.?", "developer"),
    (r"\bmgr\b\.?", "manager"), (r"\bassoc\b\.?", "associate"),
]


def norm(text):
    """Lowercase, expand common abbreviations, turn punctuation into spaces."""
    t = (text or "").lower().replace("&", " and ")
    t = re.sub(r"[‐-―]", "-", t)
    for pat, rep in _ABBREV:
        t = re.sub(pat, rep, t)
    t = re.sub(r"[^a-z0-9+#]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _has(words, text):
    """True if any phrase in `words` appears in `text` on word boundaries."""
    return next((w for w in words if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text)), None)


# ---------------------------------------------------------------- job titles

# Step 2 title list (matched as phrases after normalising)
TITLE_PHRASES = [
    "data engineer", "data engineering", "analytics engineer", "analytics engineering",
    "big data", "pyspark developer", "software engineer data",
]
# Step 2 skill keywords: a title containing any of these qualifies on its own
SKILL_KEYWORDS = [
    "pyspark", "spark", "hadoop", "hive", "etl", "elt", "data pipeline", "data pipelines",
    "databricks", "data platform", "data warehouse", "data warehousing", "dwh", "data lake",
    "lakehouse", "dbt", "airflow", "snowflake", "bigquery", "redshift", "informatica",
    "talend", "data integration", "data infrastructure", "dataops", "data ops", "data fabric",
    "azure data factory", "adf",
]
# Never data-engineering roles, whatever else the title says
HARD_EXCLUDE = [
    "data center", "data centre", "datacenter", "datacentre", "data entry", "intern",
    "internship", "apprentice", "apprenticeship", "trainee", "summer analyst", "co op",
    "working student", "werkstudent", "student", "graduate program", "campus",
]
# Only exclude these when the title got in through the loose "data ... engineer" pattern
SOFT_EXCLUDE = [
    "data scientist", "data science", "data protection", "data privacy", "data loss",
    "data security", "data annotation", "data labeling", "data labelling", "data collection",
    "data steward", "clinical data", "data entry",
]
# Matched, but worth a second look
BORDERLINE_WORDS = [
    "quality", "test", "testing", "qa", "support", "storage", "governance", "management",
    "migration", "analyst", "scientist", "sales", "presales", "pre sales", "solutions",
    "consultant", "architect", "manager", "director", "head", "customer",
]
# A skill keyword in a title that is clearly not an engineering role
KEYWORD_BORDERLINE_WORDS = ["analyst", "test", "testing", "qa", "sales", "presales", "support",
                            "consultant", "director", "head", "recruiter", "customer", "admin"]


def title_match(title):
    """Return (matched, how, borderline).

    how: "title" (Step 2 list), "keyword:<kw>" (Step 2 skill keyword) or
         "pattern" (Software Engineer ... Data / Data ... Engineer word order).
    """
    t = norm(title)
    if not t or _has(HARD_EXCLUDE, t):
        return False, None, False
    phrase = _has(TITLE_PHRASES, t)
    kw = _has(SKILL_KEYWORDS, t)
    pattern = bool(re.search(r"\bdata\b.*\b(engineer|engineering|developer)\b", t) or
                   re.search(r"\b(engineer|developer)\b.*\bdata\b", t))
    soft = _has(SOFT_EXCLUDE, t)

    if phrase and not soft:
        how = "title"
    elif kw:
        how = "keyword:" + kw
    elif pattern and not soft:
        how = "pattern"
    else:
        return False, None, False

    borderline = False
    if how == "pattern" and _has(BORDERLINE_WORDS, t):
        borderline = True
    if soft:  # e.g. "Data Scientist (PySpark)" got in through a keyword
        borderline = True
    if how.startswith("keyword:") and _has(KEYWORD_BORDERLINE_WORDS, t) and \
            not re.search(r"\b(engineer|engineering|developer)\b", t):
        borderline = True
    if how.startswith("keyword:") and kw in ("airflow", "snowflake", "adf", "hive") \
            and "data" not in t and not re.search(r"\b(engineer|developer)\b", t):
        borderline = True
    if how.startswith("keyword:airflow") and "data" not in t:
        borderline = True  # HVAC "airflow" roles exist at industrial companies
    return True, how, borderline


# ---------------------------------------------------------------- locations

CITY_ALIASES = {
    "Navi Mumbai": ["navi mumbai", "new bombay", "airoli", "vashi", "belapur", "cbd belapur",
                    "ghansoli", "rabale", "mahape", "turbhe", "kharghar", "nerul", "sanpada",
                    "juinagar", "seawoods", "koparkhairane", "kopar khairane", "taloja", "ttc industrial"],
    "Mumbai": ["mumbai", "bombay", "goregaon", "powai", "andheri", "malad", "vikhroli", "bandra",
               "bkc", "lower parel", "worli", "kurla", "saki naka", "sakinaka", "marol",
               "santacruz", "dadar", "nariman point", "prabhadevi", "chandivali", "ghatkopar",
               "mulund", "borivali", "kandivali", "jogeshwari", "vile parle", "parel", "chembur",
               "bhandup", "kanjurmarg"],
    "Pune": ["pune", "poona", "pimpri", "chinchwad", "pimpri chinchwad", "haveli", "khed",
             "hinjewadi", "hinjawadi", "kharadi", "magarpatta", "hadapsar", "baner", "balewadi",
             "viman nagar", "yerwada", "yerawada", "wakad", "aundh", "kalyani nagar", "talawade",
             "chakan", "bhosari", "shivajinagar", "kothrud", "mundhwa", "wagholi", "eon free zone",
             "commerzone", "sp infocity", "phursungi", "bavdhan", "pashan"],
}
REMOTE_WORDS = ["remote", "remotely", "work from home", "wfh", "home based", "home office",
                "anywhere", "virtual", "telecommute", "distributed", "work from anywhere"]
INDIA_WORDS = ["india", "ind", "bharat", "bengaluru", "bangalore", "hyderabad", "chennai",
               "gurugram", "gurgaon", "noida", "delhi", "kolkata", "ahmedabad", "kochi", "jaipur",
               "coimbatore", "mohali", "chandigarh", "indore", "thiruvananthapuram", "trivandrum",
               "maharashtra", "karnataka", "telangana", "tamil nadu", "haryana"] + \
              [a for v in CITY_ALIASES.values() for a in v]
NOT_INDIA_WORDS = [
    "united states", "usa", "us", "u s", "america", "americas", "north america", "latam",
    "canada", "mexico", "brazil", "argentina", "colombia", "chile", "uk", "united kingdom",
    "england", "ireland", "germany", "france", "spain", "portugal", "italy", "netherlands",
    "poland", "romania", "ukraine", "sweden", "norway", "denmark", "finland", "switzerland",
    "austria", "belgium", "czech", "hungary", "serbia", "bulgaria", "greece", "turkey",
    "israel", "uae", "dubai", "saudi", "qatar", "egypt", "south africa", "nigeria", "kenya",
    "singapore", "malaysia", "philippines", "indonesia", "vietnam", "thailand", "japan",
    "korea", "china", "hong kong", "taiwan", "australia", "new zealand", "emea", "europe",
    "eu", "london", "new york", "san francisco", "seattle", "austin", "toronto", "vancouver",
    "berlin", "paris", "amsterdam", "dublin", "warsaw", "madrid", "lisbon", "sydney",
    "melbourne", "tokyo", "manila", "kuala lumpur", "sao paulo", "chicago", "boston",
    "denver", "atlanta", "dallas", "pakistan", "bangladesh", "sri lanka", "nepal",
]
COUNTRY_CODES_INDIA = {"in", "ind", "india"}


def location_tags(locations, remote_flag=None, country=None):
    """Return (tags, notes).

    tags  - subset of {"Pune", "Mumbai", "Navi Mumbai", "Remote"}
    notes - list of short review notes (e.g. remote job with no country stated)
    """
    texts = [norm(l) for l in (locations or []) if l]
    blob = " | ".join(texts)
    tags, notes = set(), []

    for city in ("Navi Mumbai", "Mumbai", "Pune"):
        for alias in CITY_ALIASES[city]:
            if re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", blob) or \
                    (alias in ("pune", "mumbai") and alias in blob.replace(" ", "")):
                tags.add(city)
                break
    # "Navi Mumbai" contains "mumbai": only tag Mumbai too if Mumbai proper appears separately
    if "Navi Mumbai" in tags and "Mumbai" in tags:
        rest = blob.replace("navi mumbai", "")
        if not any(re.search(r"(?<![a-z])" + re.escape(a) + r"(?![a-z])", rest)
                   for a in CITY_ALIASES["Mumbai"]):
            tags.discard("Mumbai")

    is_remote = bool(remote_flag) or bool(_has(REMOTE_WORDS, blob))
    if is_remote:
        c = norm(country or "")
        india = c in COUNTRY_CODES_INDIA or bool(_has(INDIA_WORDS, blob))
        foreign = (c and c not in COUNTRY_CODES_INDIA) or bool(_has(NOT_INDIA_WORDS, blob))
        if india:
            tags.add("Remote")
        elif not foreign:
            tags.add("Remote")
            notes.append("remote - country not stated")
    elif not tags and _has(["multiple locations", "various locations"], blob) and not _has(NOT_INDIA_WORDS, blob):
        notes.append("multiple locations - check")
    elif not tags and _has(["maharashtra"], blob):
        leftover = set(re.findall(r"[a-z]+", blob)) - {"maharashtra", "india", "in", "mh", "ind", "state", "of"}
        if not leftover:
            notes.append("Maharashtra - city not stated")
    return tags, notes


# ---------------------------------------------------------------- posting dates

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


IST = timezone(timedelta(hours=5, minutes=30))


def today_utc():
    """Today's date in India (the board's time zone)."""
    return datetime.now(IST).date()


def parse_posted(value, today=None):
    """Turn whatever a careers site gives us into a date (or None if unknown)."""
    today = today or today_utc()
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    low = s.lower()
    if re.fullmatch(r"\d{10,13}", s):
        return parse_posted(int(s), today)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    if "today" in low or "just now" in low or re.search(r"\b(hour|minute|second)s?\b", low):
        return today
    if "yesterday" in low:
        return today - timedelta(days=1)
    m = re.search(r"(\d+)\s*\+?\s*(day|week|month|year)s?", low)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
        if "+" in low[m.start():m.end() + 1]:
            days += 1  # "30+ days ago" is older than 30
        return today - timedelta(days=days)
    m = re.search(r"\b(?:an?|one)\s+(day|week|month)\b", low)
    if m:
        return today - timedelta(days={"day": 1, "week": 7, "month": 30}[m.group(1)])
    # "Oct 20, 2026", "20 Oct 2026", "20-Oct-2026", "October 20, 2026"
    m = re.search(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", low) or None
    if m and m.group(1) in _MONTHS:
        return _safe_date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
    m = re.search(r"(\d{1,2})[\s\-/]+([a-z]{3})[a-z]*[\s\-/,]+(\d{4})", low)
    if m and m.group(2) in _MONTHS:
        return _safe_date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", low)  # assume MM/DD/YYYY
    if m:
        return _safe_date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    return None


def _safe_date(y, mo, d):
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def is_recent(posted, today=None, days=RECENCY_DAYS):
    """Unknown posting dates are kept (Step 4)."""
    if posted is None:
        return True
    today = today or today_utc()
    return (today - posted).days <= days
