import pytest

from job_agent.delivery_checks import company_scale_issues
from job_agent.models import Assessment, Constraint, Delivery, DeliveryItem, Evidence, State


def state(verdict='unknown'):
    return State(constraints={'scale': Constraint(key='scale', text='偏好大公司', kind='soft', quote='大公司')},
                 assessments={'j': Assessment(job_id='j', version=1,
                     evidence=[Evidence(key='scale', verdict=verdict)])})


@pytest.mark.parametrize('fit', ['大厂AI前端岗位', '公司体量较大，符合规模偏好', '大型公司平台',
                               '公司规模未知，但这是大厂岗位'])
def test_unknown_scale_cannot_be_a_verified_advantage(fit):
    delivery = Delivery(summary='', items=[DeliveryItem(job_id='j', evidence_keys=['scale'], fit=fit)])
    assert company_scale_issues(state(), delivery) == [
        {'job_id': 'j', 'field': 'fit', 'reason': 'company_size_unknown_but_asserted'}]
    assert not company_scale_issues(state('satisfied'), delivery)


@pytest.mark.parametrize('fit', ['公司规模尚未核实，不能确认是否为大公司', '若是大厂也需核实用工',
                               '上市不等于大公司', '负责前端工程化', '不是大公司'])
def test_uncertainty_and_non_size_advantages_are_preserved(fit):
    delivery = Delivery(summary='', items=[DeliveryItem(job_id='j', evidence_keys=['scale'], fit=fit)])
    assert not company_scale_issues(state(), delivery)
