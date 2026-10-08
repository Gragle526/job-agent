import pytest

from job_agent.constraints import attendance_evidence, explicit_weekly_limit
from job_agent.models import Constraint
from job_agent.providers import import_jobs


@pytest.mark.parametrize('quote', ['月薪三万以上', '月薪至少3w', '月薪至少30000元', '月薪三十万元以上'])
def test_salary_legacy_value_uses_explicit_monthly_quote(quote):
    from job_agent.constraints import salary_evidence
    from job_agent.models import Job
    job = Job(id='cleaner', source='test', source_id='1', title='保洁',
              url='https://example.invalid/1', salary='5-6K', provenance='synthetic')
    result = salary_evidence(Constraint(key='salary', text='30000', kind='hard', quote=quote), job)
    assert result.verdict == 'violated'


@pytest.mark.parametrize('quote', ['30000', '年薪三万以上', '月薪三万以下'])
def test_salary_bare_value_does_not_invent_monthly_minimum(quote):
    from job_agent.constraints import salary_evidence
    from job_agent.models import Job
    job = Job(id='j', source='test', source_id='1', title='岗位', url='https://example.invalid/1',
              salary='5-6K', provenance='synthetic')
    assert salary_evidence(Constraint(key='salary', text='30000', kind='hard', quote=quote), job) is None


@pytest.mark.parametrize("jd,expected", [
    ("每周至少2天", "satisfied"), ("每周至少3天", "satisfied"),
    ("每周至少5天", "violated"), ("每周三天", "satisfied"),
    ("每周至少2天，另一个方向每周至少5天", "unknown")])
def test_attendance_is_feasibility_not_string_equality(jd, expected):
    job = import_jobs([{"title": "岗位", "url": "https://example.invalid/job", "description": jd}]).jobs[0]
    condition = Constraint(key="days_per_week", text="每周最多三天", kind="hard", quote="每周最多三天")
    evidence = attendance_evidence(condition, job)
    assert evidence.verdict == expected
    if evidence.quote:
        assert evidence.quote in jd


def test_preference_does_not_become_hard_limit():
    assert explicit_weekly_limit("每周最多三天")
    assert not explicit_weekly_limit("最好每周最多三天")


@pytest.mark.parametrize('quote', ['每周可以6天', '每周可投入六天', '每周能6天', '一周可以实习6天'])
@pytest.mark.parametrize('jd,expected', [('每周 4～5 天', 'satisfied'), ('每周至少5天', 'satisfied'),
                                      ('每周至少7天', 'violated')])
def test_capacity_wording_and_user_quote_prevent_reversed_comparison(quote, jd, expected):
    job = import_jobs([{'title': '岗位', 'url': 'https://example.invalid/job', 'description': jd}]).jobs[0]
    # Model's canonical text dropped the capacity qualifier; the original quote did not.
    condition = Constraint(key='days', text='每周6天', kind='hard', quote=quote)
    result = attendance_evidence(condition, job)
    assert result.verdict == expected
    assert result.quote in job.evidence_text()


def test_bare_six_days_is_not_silently_reinterpreted_as_capacity():
    job = import_jobs([{'title': '岗位', 'url': 'https://example.invalid/job', 'description': '每周5天'}]).jobs[0]
    condition = Constraint(key='days', text='每周6天', kind='hard', quote='每周6天')
    assert attendance_evidence(condition, job) is None


@pytest.mark.parametrize("description,card,limit,expected", [
    ("实习时间：每周 4～5 天", "杭州 4天/周", 4, "unknown"),
    ("实习时间：每周 4～5 天", "杭州 4天/周", 3, "violated"),
    ("实习时间：每周 4～5 天", "杭州 4天/周", 5, "satisfied"),
    ("每周三至五天", "3天/周", 4, "unknown"),
    ("每周4-5天，可协商", "4天/周", 5, "unknown"),
    ("每周5～4天", "4天/周", 4, "unknown"),
    ("每周2～3天", "5天/周", 4, "unknown"),
])
def test_attendance_range_cannot_be_overridden_by_card(description, card, limit, expected):
    job = import_jobs([{"title": "岗位", "url": "https://example.invalid/range",
                        "description": description, "detail_metadata": card}]).jobs[0]
    condition = Constraint(key="days", text=f"每周最多{limit}天", kind="hard", quote=f"每周最多{limit}天")
    evidence = attendance_evidence(condition, job)
    assert evidence.verdict == expected
    if evidence.quote:
        assert evidence.quote in job.evidence_text()


def test_remote_work_does_not_change_required_job_location():
    from job_agent.constraints import location_evidence
    from job_agent.models import Job
    job = Job(id='nanjing', source='boss', source_id='x', url='https://example.invalid/x',
              title='Python实习可远程', city='南京·鼓楼区', description='可远程', provenance='live')
    constraint = Constraint(key='city', text='只考虑北京', kind='hard', quote='只考虑北京')
    result = location_evidence(constraint, job, '北京')
    assert result.verdict == 'violated'
    assert result.quote == job.city
    assert location_evidence(constraint, job.model_copy(update={'city': ''}), '北京').verdict == 'unknown'
