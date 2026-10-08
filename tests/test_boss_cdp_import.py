import pytest

from job_agent.boss_cdp_import import normalize_exports


def listing():
    return {"jobs": [{"title": "Agent engineer", "boss_name": "Example",
                      "job_link": "https://www.zhipin.com/job_detail/abc.html",
                      "encrypt_job_id": "abc", "security_id": "temporary"}]}


def test_list_is_not_detail_and_locator_is_not_identity():
    first = normalize_exports(listing(), [], "list-time", "detail-time")[0]
    changed = listing()
    changed["jobs"][0]["security_id"] = "rotated"
    second = normalize_exports(changed, [], "list-time", "detail-time")[0]
    assert first.id == second.id
    assert first.description == ""
    assert first.fetched_at == "list-time"


def test_detail_identity_and_empty_content_are_validated():
    detail = {"job_link": listing()["jobs"][0]["job_link"],
              "title": "Agent engineer", "jd": "Actual job description"}
    job = normalize_exports(listing(), [detail], "list-time", "detail-time")[0]
    assert job.description == detail["jd"]
    assert job.fetched_at == "detail-time"
    for update in ({"jd": " "}, {"title": "Other job"},
                   {"job_link": "https://www.zhipin.com/job_detail/other.html"}):
        with pytest.raises(ValueError):
            normalize_exports(listing(), [dict(detail, **update)], "list-time", "detail-time")
