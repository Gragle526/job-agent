import pytest

from job_agent.engine import Engine
from job_agent.models import Constraint, Fact, Job, State, Checkpoint
from job_agent.providers import Snapshot
from types import SimpleNamespace
from job_agent.requirements import RequirementOperation as Op, RequirementPlan as Plan, compile_plan
from job_agent.store import Store


@pytest.mark.parametrize('value', ['30000', '月薪30000元'])
def test_monthly_salary_comparator_and_units_cannot_be_lost(value):
    message = '月薪三万以上'
    intent = compile_plan(State(), message, Plan(mode='recommend', operations=[
        Op(slot='salary', operation='set', value=value, quote=message)]))
    assert intent.changes[0].text == message


def test_replacement_uses_existing_legacy_key_and_explicit_permission():
    state = State(constraints={'role_direction': Constraint(key='role_direction', text='Agent',
                                                            kind='hard', quote='Agent')})
    intent = compile_plan(state, '改找Python Web', Plan(mode='recommend', operations=[
        Op(slot='role', operation='replace', value='Python Web', strength='hard', quote='改找Python Web')]))
    assert intent.changes[0].key == 'role_direction'
    assert intent.changes[0].explicit


def test_append_preserves_old_skills_but_correction_replaces_experience():
    state = State(background={'skills': Fact(key='skills', text='Python、RAG', quote='Python、RAG'),
                              'experience': Fact(key='experience', text='5年', quote='5年')})
    intent = compile_plan(state, '我还会FastAPI，纠正一下我没有工作经验', Plan(mode='recommend', operations=[
        Op(slot='skills', operation='append', value='FastAPI', quote='我还会FastAPI'),
        Op(slot='experience', operation='replace', value='没有工作经验', quote='我没有工作经验')]))
    assert 'Python、RAG' in intent.changes[0].text and 'FastAPI' in intent.changes[0].text
    assert intent.changes[1].text == '没有工作经验'
    assert all(c.scope == 'background' for c in intent.changes)


def test_background_never_becomes_a_job_qualification_requirement():
    intent = compile_plan(State(), '我是硕士会Python', Plan(mode='recommend', operations=[
        Op(slot='education', operation='set', value='硕士', quote='我是硕士'),
        Op(slot='skills', operation='set', value='Python', quote='会Python')]))
    assert all(c.scope == 'background' for c in intent.changes)


def test_refinement_preserves_hard_role():
    state = State(constraints={'role': Constraint(key='role', text='前端', kind='hard', quote='前端')})
    intent = compile_plan(state, '最好Vue', Plan(mode='recommend', operations=[
        Op(slot='role', operation='refine', value='Vue', strength='soft', quote='最好Vue')]))
    assert intent.changes[0].key == 'preference_role'
    assert not intent.changes[0].explicit


@pytest.mark.parametrize('ref,quote', [(1, '全程远程'), (1, '第三个'), (3, '第二个')])
def test_reference_requires_actual_matching_user_target(ref, quote):
    with pytest.raises(ValueError, match='matching user target'):
        compile_plan(State(), quote, Plan(mode='reference', reference=ref, reference_quote=quote))


def test_reference_uses_original_question_and_cannot_mutate_state():
    message = '第三个要求训练经验吗？'
    intent = compile_plan(State(), message, Plan(mode='reference', reference=3, reference_quote='第三个'))
    assert intent.reference == 3 and intent.question == message
    with pytest.raises(ValueError, match='must not mutate'):
        compile_plan(State(), message, Plan(mode='reference', reference=3, reference_quote='第三个',
            operations=[Op(slot='technology', operation='set', value='训练', strength='hard', quote='训练')]))


def test_conflict_clarification_does_not_partially_apply_new_requirements():
    plan = Plan(mode='clarify', clarification='请选择城市', operations=[
        Op(slot='city', operation='set', value='北京', strength='hard', quote='北京')])
    with pytest.raises(ValueError, match='preserve state'):
        compile_plan(State(), '北京还是上海', plan)


def test_compare_existing_cannot_schedule_browser_calls_but_can_reassess(tmp_path):
    job = Job(id='j', source='test', source_id='j', title='前端', city='北京', description='Vue开发',
              url='https://example.invalid/j', provenance='imported')
    store = Store(tmp_path / 'state.sqlite')
    store.save_job(job)
    state = State(version=1, known_ids=['j'], queries=['前端'])
    intent = compile_plan(state, '只用已读岗位，薪资不限', Plan(mode='compare_existing'))
    state.output['search_policy'] = Fact(key='search_policy', text='existing_only', quote='只用已读岗位')
    cp = Checkpoint(state=state, intent=intent)
    allowed = Engine(store, Snapshot([job]), SimpleNamespace(name="cloud")).allowed(cp)
    assert [a.kind for a in allowed] == ['assess_batch']
    later = compile_plan(state, '重新找前端', Plan(mode='recommend', queries=['前端']))
    assert later.changes[0].op == 'remove'
    assert later.changes[0].key == 'search_policy'


def test_non_reference_query_cannot_smuggle_a_reference():
    with pytest.raises(ValueError, match='cannot carry'):
        compile_plan(State(), '只用已有岗位', Plan(mode='compare_existing', reference=1))


def test_unknown_target_cannot_redirect_update_to_an_arbitrary_key():
    with pytest.raises(ValueError, match='existing state entry'):
        compile_plan(State(), '上海', Plan(mode='recommend', operations=[
            Op(slot='city', target_key='invented', operation='set', value='上海', strength='hard', quote='上海')]))


def test_legacy_weekly_capacity_is_not_confused_with_weekend_schedule():
    state = State(constraints={'work_schedule': Constraint(key='work_schedule', text='每周最多6天',
                                                           kind='hard', quote='每周最多6天')})
    intent = compile_plan(state, '改为每周最多3天，最好双休', Plan(mode='recommend', operations=[
        Op(slot='availability', operation='replace', value='每周最多3天', strength='hard', quote='每周最多3天'),
        Op(slot='schedule', operation='set', value='双休', strength='soft', quote='最好双休')]))
    assert [c.key for c in intent.changes] == ['work_schedule', 'schedule']


def test_reaffirmation_reuses_known_strength_without_weakening_it():
    state = State(constraints={'role': Constraint(key='role', text='前端', kind='hard', quote='前端')})
    intent = compile_plan(state, '前端保留', Plan(mode='recommend', operations=[
        Op(slot='role', operation='set', value='前端', quote='前端保留')]))
    assert intent.changes[0].kind == 'hard'


def test_preserve_conditions_is_not_authorization_to_switch_formal_to_internship():
    state = State(constraints={'employment': Constraint(key='employment', text='正式', kind='hard', quote='正式')})
    intent = compile_plan(state, '之前岗位条件不变', Plan(mode='recommend', operations=[
        Op(slot='employment', operation='replace', value='实习', quote='之前岗位条件不变')]))
    assert not intent.changes


def test_personal_python_fact_cannot_become_job_technology_constraint():
    intent = compile_plan(State(), '我会Python，也会FastAPI', Plan(mode='recommend', operations=[
        Op(slot='technology', operation='set', value='Python', quote='我会Python'),
        Op(slot='skills', operation='set', value='FastAPI', quote='也会FastAPI')]))
    assert len(intent.changes) == 1
    assert intent.changes[0].scope == 'background'
    assert 'Python' in intent.changes[0].text and 'FastAPI' in intent.changes[0].text


def test_cannot_redirect_city_slot_to_existing_salary_key():
    state = State(constraints={'salary': Constraint(key='salary', text='15k以上', kind='hard', quote='15k以上')})
    with pytest.raises(ValueError, match='different requirement category'):
        compile_plan(state, '北京', Plan(mode='recommend', operations=[
            Op(slot='city', operation='replace', target_key='salary', value='北京', quote='北京')]))


def test_attendance_inverted_word_order_keeps_unit_and_does_not_modify_rest_schedule():
    state = State(constraints={'schedule': Constraint(key='schedule', text='双休', kind='hard', quote='双休')})
    intent = compile_plan(state, '可以每周4天，双休仍必须', Plan(mode='recommend', operations=[
        Op(slot='availability', operation='replace', value='4', quote='可以每周4天')]))
    assert intent.changes[0].key == 'availability'
    assert intent.changes[0].text == '每周最多4天'
    assert intent.changes[0].kind == 'hard'


def test_actual_training_project_is_background_not_a_new_job_training_gate():
    intent = compile_plan(State(), '我其实也做过LoRA项目', Plan(mode='recommend', operations=[
        Op(slot='technology', operation='append', value='LoRA', quote='我其实也做过LoRA项目')]))
    assert intent.changes[0].scope == 'background'


def test_only_an_explicit_existing_material_command_disables_search():
    intent = compile_plan(State(), '更偏产品设计，请重新比较', Plan(mode='compare_existing', operations=[
        Op(slot='career_preference', operation='replace', value='产品设计', strength='soft', quote='更偏产品设计')]))
    assert not any(c.key == 'search_policy' for c in intent.changes)


def test_append_existing_skill_does_not_discard_other_skills():
    state = State(background={'skills': Fact(key='skills', text='Python、FastAPI', quote='会Python、FastAPI')})
    intent = compile_plan(state, '补充我会Python', Plan(mode='recommend', operations=[
        Op(slot='skills', operation='append', value='Python', quote='补充我会Python')]))
    assert intent.changes[0].text == 'Python、FastAPI'
