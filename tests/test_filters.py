from datetime import date

from scanner.filters import is_recent, location_tags, parse_posted, title_match

T = date(2026, 10, 24)


def m(title):
    return title_match(title)[0]


def test_titles_in():
    for t in ["Data Engineer", "Sr. Data Engineer", "Senior Data Engineer - GCP", "Azure Data Engineer",
              "Analytics Engineer II", "PySpark Developer", "Big Data Engineer", "Software Engineer - Data",
              "Software Engineer, Data Platform", "Data Software Engineer", "ETL Developer",
              "Spark Developer", "Databricks Engineer", "Hadoop Admin", "Lead Data Engineering Manager",
              "Senior Engineer - Data", "Data Engineer III", "Snowflake Developer", "Associate, Data Engineering",
              "Engineer II, Big Data", "Senior Associate - Data Engineer (Python, Spark)"]:
        assert m(t), t


def test_titles_out():
    for t in ["Data Scientist", "Data Analyst", "Data Center Technician", "Data Entry Operator",
              "Software Engineer", "Backend Engineer", "Data Engineering Intern", "Product Manager - Data",
              "Data Privacy Engineer", "Apprentice - Data Engineer", "Pipeline Integrity Engineer",
              "Lead Software Engineer - Java Spring Boot, Kafka"]:
        assert not m(t), t


def test_borderline():
    assert title_match("Data Quality Engineer")[2]
    assert title_match("Airflow Engineer")[2]
    assert not title_match("Senior Data Engineer")[2]


def test_locations():
    assert location_tags(["Pune, Maharashtra, India"])[0] == {"Pune"}
    assert location_tags(["INDPune"])[0] == {"Pune"}
    assert location_tags(["Ind- Pune Sez 1 All Floors"])[0] == {"Pune"}
    assert location_tags(["Navi Mumbai, IND"])[0] == {"Navi Mumbai"}
    assert location_tags(["Airoli"])[0] == {"Navi Mumbai"}
    assert location_tags(["Mumbai", "Navi Mumbai"])[0] == {"Mumbai", "Navi Mumbai"}
    assert location_tags(["Goregaon East"])[0] == {"Mumbai"}
    assert location_tags(["Hinjewadi Phase 2"])[0] == {"Pune"}
    assert location_tags(["Bengaluru, India"])[0] == set()
    assert location_tags(["Remote - Bangalore"])[0] == {"Remote"}
    assert location_tags(["Hybrid/Remote, Mumbai"])[0] == {"Mumbai", "Remote"}
    assert location_tags(["Remote - US"])[0] == set()
    assert location_tags(["Remote"], country="IN")[0] == {"Remote"}
    tags, notes = location_tags(["Remote"])
    assert tags == {"Remote"} and notes
    assert location_tags(["Remote, EMEA"])[0] == set()
    assert location_tags(["India"], remote_flag=True)[0] == {"Remote"}
    assert location_tags(["Maharashtra, India"]) == (set(), ["Maharashtra - city not stated"])
    assert location_tags(["Multiple Locations"]) == (set(), ["multiple locations - check"])


def test_dates():
    assert parse_posted("Posted 5 Days Ago", T) == date(2026, 10, 19)
    assert parse_posted("Posted Today", T) == T
    assert parse_posted("Posted Yesterday", T) == date(2026, 10, 23)
    assert not is_recent(parse_posted("Posted 30+ Days Ago", T), T)
    assert is_recent(parse_posted("2 weeks ago", T), T)
    assert parse_posted("October 20, 2026", T) == date(2026, 10, 20)
    assert parse_posted("Oct 20, 2026", T) == date(2026, 10, 20)
    assert parse_posted("20-Oct-2026", T) == date(2026, 10, 20)
    assert parse_posted("2026-10-01T10:00:00Z", T) == date(2026, 10, 1)
    assert parse_posted(1761264000000, T) == date(2025, 10, 24)
    assert is_recent(None, T)
