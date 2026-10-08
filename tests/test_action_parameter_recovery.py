import pytest

from job_agent.models import Action, ActionSelection, Checkpoint, Constraint, Intent, State, SearchQueryReview, SearchQueryDecision
from job_agent.reasoning import CloudReasoner
from job_agent.store import Store


def setup(tmp_path, replies):
    obj = object.__new__(CloudReasoner)
    obj.store = Store(tmp_path / 'action.sqlite')
    rid, _ = obj.store.begin('test', '找Python实习')
    calls = []
    responses = iter(replies)
    def call(*args):
        calls.append(args)
        return next(responses)
    obj.call = call
    return obj, rid, calls


def test_empty_search_parameters_get_one_explicit_repair(tmp_path):
    obj, rid, calls = setup(tmp_path, [
        ActionSelection(action_index=0, objective='搜索Python实习'),
        ActionSelection(action_index=0, queries=['Python后端实习'])])
    result = obj.choose(rid, Checkpoint(intent=Intent(), state=State()),
                        [Action(kind='search_batch')], [])
    assert result.queries == ['Python后端实习']
    assert len(calls) == 2
    assert calls[1][2]['draft']['queries'] == []


def test_single_explicit_query_is_a_batch_without_extra_call(tmp_path):
    obj, rid, calls = setup(tmp_path, [ActionSelection(action_index=0, query='Python实习')])
    result = obj.choose(rid, Checkpoint(intent=Intent(), state=State()),
                        [Action(kind='search_batch')], [])
    assert result.queries == ['Python实习']
    assert len(calls) == 1


def test_out_of_range_action_gets_one_repair_without_changing_allowed_actions(tmp_path):
    obj, rid, calls = setup(tmp_path, [ActionSelection(action_index=9),
                                      ActionSelection(action_index=1)])
    actions = [Action(kind='search', query='保洁'), Action(kind='stop')]
    result = obj.choose(rid, Checkpoint(intent=Intent(), state=State()), actions, [])
    assert result.kind == 'stop'
    assert len(calls) == 2
    assert calls[1][2]['valid_action_indices'] == [0, 1]
    assert actions[0].query == '保洁'


def test_invalid_index_repair_is_bounded_and_does_not_execute_arbitrary_action(tmp_path):
    obj, rid, calls = setup(tmp_path, [ActionSelection(action_index=9)] * 2)
    with pytest.raises(ValueError, match='after one repair'):
        obj.choose(rid, Checkpoint(intent=Intent(), state=State()),
                   [Action(kind='search', query='保洁'), Action(kind='stop')], [])
    assert len(calls) == 2


def test_single_action_requests_only_parameters_and_never_model_action_index(tmp_path):
    obj, rid, calls = setup(tmp_path, [ActionSelection(action_index=100)])
    result = obj.choose(rid, Checkpoint(intent=Intent(), state=State()),
                        [Action(kind='detail_batch', job_ids=['j'])], [])
    assert result.job_ids == ['j']
    assert 'action_index' not in calls[0][3].model_json_schema()['properties']
    assert len(calls) == 1


def test_search_review_can_filter_but_cannot_invent_or_reorder_query_indices(tmp_path):
    obj, rid, _ = setup(tmp_path, [SearchQueryReview(queries=[
        SearchQueryDecision(index=0, preserves_requirements=False, reason='职责改变'),
        SearchQueryDecision(index=1, preserves_requirements=True, reason='同一职业')])])
    cp = Checkpoint(intent=Intent(), state=State(constraints={
        'role': Constraint(key='role', text='清洁工', kind='hard', quote='清洁工')}))
    assert obj.review_search(rid, cp,
                             ['酒店经理', '保洁员']) == ['保洁员']
    obj.call = lambda *args: SearchQueryReview(queries=[
        SearchQueryDecision(index=99, preserves_requirements=True, reason='bad index')])
    with pytest.raises(ValueError, match='exactly'):
        obj.review_search(rid, cp, ['保洁员'])


def test_explanation_is_not_used_as_search_parameter_and_repair_is_bounded(tmp_path):
    obj, rid, calls = setup(tmp_path, [ActionSelection(action_index=0, reason='搜索Python实习')]*2)
    with pytest.raises(ValueError, match='explicit queries'):
        obj.choose(rid, Checkpoint(intent=Intent(), state=State()), [Action(kind='search_batch')], [])
    assert len(calls) == 2
