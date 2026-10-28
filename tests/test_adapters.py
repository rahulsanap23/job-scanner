from scanner.adapters import csb_unify, jazzhr, kula

KULA_PAGE = """
<style>.css-x{color:red}</style>
<div><div><p class="chakra-text css-a">Senior Data Engineer</p>
<div><p class="chakra-text css-b"><span>Engineering<!-- --> </span> • Mumbai, Maharashtra, India<!-- --> • Full time<!-- --> • On-Site</p></div></div>
<a class="chakra-link" href="/acme/123-senior-data-engineer"><span>Apply Now</span></a></div>
<div><div><p class="chakra-text css-a">Account Executive</p>
<div><p class="chakra-text css-b"><span>Sales </span> • Mexico • Full time • Remote</p></div></div>
<a class="chakra-link" href="/acme/456-account-executive"><span>Apply Now</span></a></div>
"""

JAZZ_PAGE = """
<ul class='list-group'>
 <li class="list-group-item"> <h3 class='list-group-item-heading'>
  <a href="https://acme.applytojob.com/apply/AbC123xyz/Data-Engineer"> Data Engineer </a> </h3>
  <ul class='list-inline list-group-item-text'> <li><i class='fa fa-map-marker'></i>Pune, MH, India</li> </ul> </li>
 <li class="list-group-item"> <h3 class='list-group-item-heading'>
  <a href="https://acme.applytojob.com/apply/ZZZ999aaa/Recruiter"> Recruiter </a> </h3>
  <ul class='list-inline list-group-item-text'> <li><i class='fa fa-map-marker'></i>Remote, remote, Mexico</li> </ul> </li>
</ul>
"""


def test_kula_parse():
    jobs = kula.parse(KULA_PAGE, "acme")
    assert [j["title"] for j in jobs] == ["Senior Data Engineer", "Account Executive"]
    assert jobs[0]["locations"] == ["Mumbai, Maharashtra, India"]
    assert jobs[0]["url"] == "https://careers.kula.ai/acme/123-senior-data-engineer"
    assert jobs[0]["remote"] is None and jobs[1]["remote"] is True


def test_jazzhr_parse():
    jobs = jazzhr.parse(JAZZ_PAGE)
    assert [(j["id"], j["title"], j["locations"]) for j in jobs] == [
        ("AbC123xyz", "Data Engineer", ["Pune, MH, India"]),
        ("ZZZ999aaa", "Recruiter", ["Remote, remote, Mexico"])]


def test_csb_unify_dates():
    assert csb_unify._date("10/27/26", "en_US") == "2026-10-27"
    assert csb_unify._date("27/10/2026", "en_GB") == "2026-10-27"
    assert csb_unify._date("27/10/2026", "en_US") == "2026-10-27"  # day > 12 decides the order
    assert csb_unify._date("", "en_GB") is None


def test_epam_job():
    from scanner.adapters import epam
    j = epam.to_job("blt1", {"name": "Senior Data Engineer", "vacancy_type": "Office",
                             "city": [{"name": "Pune"}, {"name": "Hyderabad"}], "country": [{"name": "India"}],
                             "seo": {"url": "/vacancy/senior-data-engineer-blt1"},
                             "created_at": "2026-10-21T14:47:23.602Z"})
    assert j["locations"] == ["Pune, India", "Hyderabad, India"]
    assert j["url"] == "https://careers.epam.com/en/vacancy/senior-data-engineer-blt1_en"
    assert j["remote"] is None and j["country"] == "India"
