from job_agent.cdp_worker import validate_detail_fields


def test_explicit_job_section_keeps_legitimate_short_jd_and_preserves_other_validation():
    thresholds = []
    def extract(value, min_length):
        thresholds.append(min_length)
        if value.get('login_wall'):
            raise ValueError('login wall')
        if len(value['jd']) < min_length:
            raise ValueError('too short')
        return {'jd': value['jd']}
    short = {'jd': '负责Python后端开发、模型部署与应用维护。'}
    assert validate_detail_fields(extract, short, [{'text':'职位描述\n正文'}], 120) == short
    assert thresholds == [1]
    import pytest
    with pytest.raises(ValueError, match='too short'):
        validate_detail_fields(extract, short, [], 120)
    with pytest.raises(ValueError, match='login wall'):
        validate_detail_fields(extract, {'jd':'请登录', 'login_wall':True}, [{'text':'职位描述'}], 120)
