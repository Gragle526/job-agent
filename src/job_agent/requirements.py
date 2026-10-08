"""Compile explicit user updates into stable, evidence-bearing state patches."""
import re
from typing import Literal, get_args

from pydantic import Field
from pydantic import ValidationError

from .models import Change, Intent, Model, State
from .constraints import weekly_limit, explicit_weekly_limit

Slot = Literal['city', 'role', 'employment', 'technology', 'availability', 'remote', 'schedule',
               'salary', 'career_preference', 'company_scale', 'exclusion', 'education',
               'graduation', 'experience', 'skills', 'projects', 'max_results', 'instructions',
               'other_condition', 'other_background']
BACKGROUND = {'education', 'graduation', 'experience', 'skills', 'projects', 'other_background'}
OUTPUT = {'max_results', 'instructions'}
ALIASES = {
    'city': {'location', 'base'},
    'role': {'role_direction', 'role_focus', 'job_direction', 'job_role'},
    'employment': {'job_type', 'employment_type', 'employment_status'},
    'availability': {'days', 'availability_days', 'weekly_days', 'attendance', 'time_limit'},
    'schedule': {'work_schedule', 'weekends', 'weekend'},
    'remote': {'remote_preference', 'remote_work', 'work_mode'},
    'company_scale': {'company_size', 'company_preference'},
    'technology': {'tech', 'technical_requirements', 'python'},
}


class RequirementOperation(Model):
    slot: Slot
    operation: Literal['set', 'replace', 'append', 'refine', 'remove', 'restore']
    value: str = ''
    strength: Literal['hard', 'soft'] = 'hard'
    quote: str
    target_key: str = ''


class DisplayTarget(Model):
    display: int = Field(ge=1)
    position: int = Field(ge=1)
    quote: str


class RequirementPlan(Model):
    mode: Literal['recommend', 'compare_existing', 'reference', 'reply', 'clarify']
    operations: list[RequirementOperation] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list, max_length=3)
    reference: int | None = Field(default=None, ge=1)
    reference_quote: str = ''
    clarification: str = ''
    targets: list[DisplayTarget] = Field(default_factory=list, max_length=4)


PROTOCOL = """理解用户本轮消息，输出需求更新协议，不生成回答，不重新抄写整个状态。
1. mode：recommend=搜索或按新需求推荐；compare_existing=明确仅比较已读岗位、禁止再搜索；
reference=用户明确针对一个已展示岗位提问；clarify=确实存在影响执行的冲突或歧义。
reply=确认规则、总结已知信息、说明数量/共同缺口或跨轮比较，不搜索、不重新设置旧条件。
跨轮比较用reply和targets：display引用display_history的清单编号，position是该清单实际展示序号，
quote为本轮原文指代。最初/第一轮查历史首份清单；上轮查最近清单，不用搜索原始排序。
单纯总结不生成operations。确认新背景可用reply并更新background，仍不启动搜索。
修改条件不是单岗位追问。reference提供从1开始的展示编号和原文指代reference_quote，不能编造问题或指代。
2. operations只包含本轮新增或修改，quote必须是本轮message中的连续原文。未提及的条件由程序保留。
slot表示用途：city地点；role岗位职责方向；employment实习/正式等类型；technology岗位技术要求；
availability本人每周能投入的天数（用于岗位可行性核验）；remote办公方式；schedule休息制度；salary薪资门槛；
career_preference职责偏好；company_scale公司规模偏好；exclusion明确排除的工作；
education/graduation/experience/skills/projects仅为本人背景；max_results/instructions为输出要求。
本人学历和技能不是岗位硬门槛。需要岗位使用Python才是technology；我会Python是skills。
找Python Agent研发实习，分别记录technology=Python、role=Agent研发、employment=实习。
3. operation：set新增或重申；replace明确更正或替换已有值；append补充本人技能或项目且保留旧经历；
refine在原硬方向内增加软偏好；remove明确撤销。明确修改无需再次询问用户。
更正五年经历为零经验用replace；新增LoRA项目用append；后端改算法再改回来用replace同一个career_preference。
城市不限、薪资不限用remove；双休不再必须但仍优先用replace成soft；不用再关注则remove。
strength只能为hard或soft：直接指定的岗位条件为hard，优先/最好/倾向为soft。
背景和输出操作的strength填soft，程序不会将它们作为岗位条件。
单纯新增偏好不能放宽旧硬条件，使用refine；明确取消或替换硬限制才用replace/remove。
同一个条件本轮仅一个操作，新值替换只需replace，不先set又remove。target_key只能引用输入中已有的key。
OR方向保持完整可选含义，不能变成同时满足；出勤天数与展示数量分别处理，保留薪资数值和比较关系。
4. queries只是最多3个简短岗位检索词，不能代替上述role/technology/employment条件。
仅recommend允许queries；compare_existing/reference/clarify必须空数组。单岗位问答不改变条件。
已有状态是依据；不要把旧原话作为本轮新增证据。"""
PROTOCOL += ("\n恢复最初某字段可用restore，程序从requirement_history恢复初始值及强度，其他字段不动。"
             "旧会话没有该字段的变更记录时，可根据user_history的明确原文用replace恢复，"
             "quote仍引用本轮恢复命令；不能确认则澄清。只确认或比较已有信息优先reply。"
             "reply时reference=null、reference_quote空字符串、targets留空，程序从当前用户原话解析历史编号。"
             "不要把岗位名填进指代quote。RAG或微调二选一的解释要求属于instructions，不是新增排除条件。")
PROTOCOL += ("\n用户只改城市或薪资时，保留现有岗位职责，queries也必须针对该职责。"
             "不能因为薪资高就擅自改搜经理、主管或不同职业；无法找到时如实说明，先征得用户同意再改变方向。"
             "薪资value必须保留币种单位、月薪/年薪口径和上下限，例如月薪至少30000元，不能只写30000。")


def ordinal_targets(state, message):
    targets = []
    pattern = r'(最初|最开始|第一轮|上轮|最近|当前|这轮)?\s*第\s*([一二三四五六七八九十]|\d+)\s*[个号条]'
    for match in re.finditer(pattern, message):
        position = int(match[2]) if match[2].isdigit() else '一二三四五六七八九十'.index(match[2]) + 1
        display = 1 if match[1] in ('最初', '最开始', '第一轮') else len(state.displays)
        if display:
            targets.append(DisplayTarget(display=display, position=position, quote=match.group().strip()))
    return targets


def scope_for(slot):
    return 'background' if slot in BACKGROUND else 'output' if slot in OUTPUT else 'condition'


def resolve_key(state, operation):
    scope = scope_for(operation.slot)
    target = {'condition': state.constraints, 'background': state.background, 'output': state.output}[scope]
    aliases = ALIASES.get(operation.slot, set()) | {operation.slot}
    if operation.target_key:
        if operation.target_key not in target:
            if operation.target_key in aliases:
                # An echoed slot name is not a model-authorized identity: use the program's canonical key.
                return operation.slot
            raise ValueError('requirement target_key must refer to an existing state entry')
        known_keys = set(get_args(Slot)) | set(ALIASES)
        known_keys.update(k for values in ALIASES.values() for k in values)
        capacity_key = operation.slot == 'availability' and (
            weekly_limit(target[operation.target_key].text) or weekly_limit(target[operation.target_key].quote))
        if operation.target_key in known_keys and operation.target_key not in aliases and not capacity_key:
            raise ValueError('target_key belongs to a different requirement category')
        return operation.target_key
    keys = [k for k in target if k in aliases]
    if operation.slot == 'availability':
        keys = list(dict.fromkeys(keys + [k for k, c in target.items()
                    if weekly_limit(c.text) or weekly_limit(c.quote)]))
    if operation.slot == 'schedule':
        keys = [k for k in keys if not weekly_limit(target[k].text) and not weekly_limit(target[k].quote)]
    if len(keys) > 1:
        raise ValueError('ambiguous legacy requirement keys; select an existing target_key')
    return keys[0] if keys else operation.slot


def compile_plan(state: State, message: str, plan: RequirementPlan, displayed=None) -> Intent:
    normalized = []
    for op in plan.operations:
        if (op.slot == 'salary' and op.operation in ('set', 'replace', 'refine', 'append')
                and op.quote in message and re.search(r'月薪|每月|月工资', op.quote)
                and re.search(r'至少|以上|不低于|以内|以下|不超过', op.quote)):
            # Preserve explicit period, units and comparator from user evidence.
            # The model's shorter value is not allowed to drop those semantics.
            op = op.model_copy(update={'value': op.quote})
        personal_employment = (op.slot == 'employment' and re.search(
            r'(?:正式|全职)(?:工作|任职)(?:经验|经历)', op.quote)
            and not re.search(r'找|岗位|招聘|求职|希望|想要|接受', op.quote))
        if personal_employment:
            if any(o.slot == 'experience' for o in plan.operations):
                continue
            op = op.model_copy(update={'slot': 'experience', 'operation': 'replace',
                                       'value': op.quote, 'strength': 'soft', 'target_key': ''})
        normalized.append(op)
    plan = plan.model_copy(update={'operations': normalized})
    if (plan.mode == 'clarify' and normalized
            and all(scope_for(o.slot) == 'background' for o in normalized)):
        plan = plan.model_copy(update={'mode': 'reply', 'clarification': ''})
    if plan.mode != 'recommend' and plan.queries:
        raise ValueError('only recommendation mode may propose search queries')
    if plan.mode == 'reference':
        if (not plan.reference or not plan.reference_quote or plan.reference_quote not in message
                or plan.operations):
            raise ValueError('reference requires a user target and must not mutate requirements')
        ordinal = re.search(r'第\s*([一二三四五六七八九十]|\d+)\s*[个号条]', plan.reference_quote)
        number = (int(ordinal[1]) if ordinal and ordinal[1].isdigit() else
                  '一二三四五六七八九十'.find(ordinal[1]) + 1 if ordinal else None)
        named = (displayed or {}).get(plan.reference, {})
        if number != plan.reference and not any(name and name in plan.reference_quote for name in named.values()):
            raise ValueError('reference number has no matching user target')
        return Intent(reference=plan.reference, question=message)
    if plan.mode != 'reply' and plan.reference is not None:
        raise ValueError('non-reference mode cannot carry a job reference')
    if plan.mode == 'clarify':
        if not plan.clarification.strip() or plan.operations:
            raise ValueError('clarification must preserve state until user resolves ambiguity')
        return Intent(clarification=plan.clarification)
    short_reply = plan.mode == 'reply' or (plan.mode == 'compare_existing' and not plan.operations
        and not re.search(r'(?:薪资|城市|岗位|出勤|双休).*(?:不限|改成|改为|调整|降到|放宽|取消)', message))
    intent = Intent(queries=plan.queries, response_mode='reply' if short_reply else 'recommend',
                    question=message if short_reply else '')
    if plan.targets and not short_reply:
        raise ValueError('historical targets require a reply without search')
    targets = plan.targets
    if short_reply and (not targets or any(t.quote not in message for t in targets)):
        # Ignore unused model metadata, never guessed IDs. User ordinals are deterministic.
        targets = ordinal_targets(state, message)
    for target in targets:
        if not target.quote.strip() or target.quote not in message:
            raise ValueError('historical target requires current user evidence')
        ordinal = re.search(r'第\s*([一二三四五六七八九十]|\d+)\s*[个号条]', target.quote)
        number = (int(ordinal[1]) if ordinal and ordinal[1].isdigit() else
                  '一二三四五六七八九十'.find(ordinal[1]) + 1 if ordinal else None)
        if number != target.position:
            raise ValueError('historical target position does not match user quote')
        if re.search(r'最初|第一轮|最开始', target.quote) and target.display != 1:
            raise ValueError('initial reference must use the first published display')
        if re.search(r'上轮|最近|当前|这轮', target.quote) and target.display != len(state.displays):
            raise ValueError('latest reference must use the last published display')
        if target.display > len(state.displays) or target.position > len(state.displays[target.display - 1]):
            intent.clarification = '该次清单中没有这个编号，请指出岗位名称或对应的清单。'
            return intent
        jid = state.displays[target.display - 1][target.position - 1]
        if jid not in intent.target_ids:
            intent.target_ids.append(jid)
    seen = set()
    for op in plan.operations:
        if not op.quote.strip() or op.quote not in message:
            if plan.mode == 'reply':
                # A summary may echo a historic change; this is not fresh authorization.
                # Keep that field unchanged rather than letting the echo kill the reply.
                continue
            raise ValueError('requirement operation has no current user evidence')
        if (plan.mode == 'reply' and scope_for(op.slot) == 'condition'
                and re.search(r'别把|不要因为|别因|不应', op.quote)
                and re.search(r'缺口|扣分|差距', op.quote)):
            op = op.model_copy(update={'slot': 'instructions', 'operation': 'set',
                                       'target_key': '', 'value': op.quote, 'strength': 'soft'})
        if plan.mode == 'reply' and scope_for(op.slot) == 'condition':
            raise ValueError('reply must not rewrite job conditions; use recommend for explicit changes')
        if (op.slot == 'technology' and re.match(r'我(?:其实)?(?:也|还|之前)?(?:会|有|做过|学过)', op.quote.strip())
                and not re.search(r'找|岗位|要求|职位', op.quote)):
            op = op.model_copy(update={'slot': 'skills', 'operation': 'append', 'target_key': '', 'strength': 'soft'})
        scope = scope_for(op.slot)
        key = resolve_key(state, op)
        target = {'condition': state.constraints, 'background': state.background, 'output': state.output}[scope]
        old = target.get(key)
        if op.operation == 'restore':
            if not re.search(r'恢复|改回|还原', op.quote):
                raise ValueError('restore requires explicit restoration request')
            original = next((h['after'] for h in state.requirement_history
                             if h['scope'] == scope and h['key'] == key and h.get('after')), None)
            if original is None:
                raise ValueError('initial requirement is not recorded; use user history or clarify')
            op = op.model_copy(update={'operation': 'replace', 'value': original['text'],
                                       'strength': original.get('kind', 'soft')})
        unchanged_quote = re.fullmatch(r'(?:其他|其余|之前|原来|原有|原本)(?:的)?(?:岗位)?'
                                      r'(?:条件|要求)?(?:都|全部)?(?:不变|保留)', op.quote.strip('。 ，；'))
        if unchanged_quote and (op.operation == 'remove' or not old or op.value != old.text):
            continue
        if op.operation == 'refine':
            if scope != 'condition' or op.strength != 'soft':
                raise ValueError('refinement must add a soft job preference')
            if old and old.kind == 'hard':
                key = f'preference_{key}'
                old = target.get(key)
        merge_background = ((scope, key) in seen and op.slot in ('skills', 'projects')
                            and op.operation in ('set', 'append'))
        if (scope, key) in seen and not merge_background:
            raise ValueError('one requirement entry may be changed only once per turn')
        if merge_background:
            prior = next(c for c in intent.changes if c.scope == scope and c.key == key)
            old = prior
        seen.add((scope, key))
        if op.operation == 'remove':
            if old is not None:
                intent.changes.append(Change(op='remove', key=key, quote=op.quote, explicit=True, scope=scope))
            continue
        if not op.value.strip():
            raise ValueError('a requirement update needs a nonempty value')
        strength = op.strength
        if op.slot in ('role', 'employment', 'technology'):
            strength = 'hard'
        if scope == 'condition' and re.search(r'更想|更偏|偏好|优先|最好|倾向|喜欢', op.quote):
            if not re.search(r'必须|只能|只接受|只做|硬条件|硬要求', op.quote):
                strength = 'soft'
        adding_background = (scope == 'background' and re.search(r'补充|还|也|再', op.quote)
                             and not re.search(r'不是|没有|撤销|删|纠正|更正', op.quote))
        if op.operation == 'append' or adding_background or merge_background:
            if scope != 'background':
                raise ValueError('append is reserved for personal background')
            value = (old.text if op.value in old.text else f'{old.text}；补充：{op.value}') if old else op.value
        else:
            value = op.value
        city_expanded = (op.slot == 'city' and old is not None
            and bool(re.search(r'也可以|也考虑|也接受|新增|扩展|增加|仍然保留|继续保留', op.quote))
            and not re.search(r'不限|全国', value))
        if city_expanded and old.text not in value:
            value = old.text + '、' + value
        if op.slot == 'availability':
            branching = bool(re.search(r'实习', op.quote) and re.search(r'正式|全职', op.quote))
            if branching:
                value = op.value if '实习' in op.value and re.search(r'正式|全职', op.value) else op.quote
            capacity = None if branching else weekly_limit(op.quote)
            if capacity is None and not branching:
                capacity_match = re.search(r'(?:最多|只能(?:投入)?|不超过|可以(?:投入)?|可投入|能投入)'
                                           r'\s*([一二三四五六七1-7])\s*天', op.quote)
                if capacity_match:
                    capacity = int(capacity_match[1]) if capacity_match[1].isdigit() else (
                        '一二三四五六七'.index(capacity_match[1]) + 1)
            if capacity is None and not branching:
                inverted = re.search(r'(?:可以|只能|可|能)(?:投入)?每周\s*([一二三四五六七1-7])\s*天', op.quote)
                if inverted:
                    capacity = int(inverted[1]) if inverted[1].isdigit() else '一二三四五六七'.index(inverted[1]) + 1
            if capacity is not None:
                value = f'每周最多{capacity}天'
                if explicit_weekly_limit(op.quote) or not re.search(r'最好|优先|尽量|希望', op.quote):
                    strength = 'hard'
            elif not re.search(r'[一二三四五六七1-7].*天', value):
                raise ValueError('weekly capacity must retain its number and day unit')
        if op.slot == 'exclusion' and not re.search(r'不|拒绝|排除|避免', value):
            value = '不接受：' + value
        if scope == 'output' and key == 'max_results':
            count = re.fullmatch(r'(10|[1-9])\s*(?:个|条)(?:岗位|候选|职位)?', value)
            if count:
                value = count[1]
        if scope == 'output' and key == 'max_results' and (not value.isdigit() or not 1 <= int(value) <= 10):
            raise ValueError('output result count must be between 1 and 10')
        if merge_background:
            intent.changes = [c for c in intent.changes if not (c.scope == scope and c.key == key)]
        intent.changes.append(Change(op='set', key=key, text=value, quote=message if merge_background else op.quote,
                                    kind=strength or 'soft', scope=scope,
                                    explicit=op.operation in ('replace', 'append') or bool(
                                        re.search(r'不再|撤销|取消|不限|改找|改为|改成|纠正|更正', op.quote))
                                    or city_expanded))
        if scope == 'condition' and op.slot == 'city' and strength == 'hard' and re.fullmatch(r'[\u4e00-\u9fff]{2,6}', value):
            if ('city' in state.search_capabilities.get('supported_filters', [])
                    and not re.search(r'和|或|及|与|同时|全国|不限', value)):
                intent.filters['city'] = value
    existing_command = re.search(r'只用|仅用|就用现有|不要.*(?:搜索|检索)|(?:别|不再|不用|停止).*'
                                 r'(?:搜索|检索)|沿用.*(?:已读|读过|资料|详情)|只重新比较', message)
    if plan.mode == 'compare_existing' and existing_command:
        intent.changes.append(Change(op='set', key='search_policy', scope='output', text='existing_only',
                                    quote=message, explicit=True))
    elif state.output.get('search_policy') and plan.mode == 'recommend':
        intent.changes.append(Change(op='remove', key='search_policy', scope='output', quote=message, explicit=True))
    if intent.changes and not any(c.scope == 'condition' for c in intent.changes):
        intent.queries = []
    return intent


def coverage_issues(state, message, plan, intent):
    """Narrow literal checks; not a claim to validate all natural-language semantics."""
    issues = []
    if (plan.mode != 'reply' and not re.search(r'比较|对比|最初|第一轮|上轮|排序', message)
            and re.search(r'第[一二三四五六七八九十\d]+[个号条].*(?:吗|是否|有没有|要求.*[？?])', message)):
        if not re.search(r'改找|改为|另外|重新搜索|重新检索', message) and plan.mode != 'reference':
            issues.append('用户是在追问明确编号的单个岗位，不是新增岗位条件；应使用reference且不修改条件。')
    if plan.mode == 'reference':
        return issues
    conditions = {k: c.text for k, c in state.constraints.items()}
    background = {k: c.text for k, c in state.background.items()}
    for c in intent.changes:
        target = conditions if c.scope == 'condition' else background if c.scope == 'background' else {}
        if c.op == 'remove':
            target.pop(c.key, None)
        else:
            target[c.key] = c.text
    for op in plan.operations:
        if op.slot == 'role' and op.operation != 'remove' and op.value in ('实习', '正式', '全职', '兼职'):
            issues.append('role必须保留用户指定的职责方向，用工类型只能放employment，不能用实习替代Agent等方向。')
    requests = list(re.finditer(r'(?:找|改找|想做)([^，。；！？]{1,60})', message))
    if requests:
        phrase = requests[-1][1]
        condition_text = '\n'.join(conditions.values()).lower()
        for term in ('Python', 'Agent'):
            if term.lower() in phrase.lower() and term.lower() not in condition_text:
                issues.append(f'本轮岗位要求明确包含{term}，不能只出现在queries；补充对应岗位条件。')
    personal = re.findall(r'(?:我)?(?:会|学过|熟悉|掌握|做过)([^，。；！？]+)', message)
    background_text = '\n'.join(background.values()).lower()
    for phrase in personal:
        for term in re.findall(r'[A-Za-z][A-Za-z0-9+#.-]*', phrase):
            if term.lower() not in background_text:
                issues.append(f'用户明确提供本人{term}技能或经历，必须保存在background，不能变成岗位硬门槛或遗漏。')
    return list(dict.fromkeys(issues))


def understand(reasoner, rid, state, message):
    displayed = {}
    for index, jid in enumerate(state.display, 1):
        try:
            job = reasoner.store.job(jid)
            displayed[index] = {'title': job.title, 'company': job.company}
        except KeyError:
            pass
    payload = {'message': message, 'current_state': {
        'conditions': {k: v.model_dump() for k, v in state.constraints.items()},
        'background': {k: v.model_dump() for k, v in state.background.items()},
        'output': {k: v.model_dump() for k, v in state.output.items()}},
        'displayed_jobs': displayed, 'previous_queries': state.queries,
        'user_history': [m for m in state.history if m['role'] == 'user'],
        'unresolved_messages': state.unresolved_messages,
        'requirement_history': state.requirement_history,
        'display_history': [{'display': n, 'jobs': [
            {'position': i, 'job_id': jid, 'title': reasoner.store.job(jid).title,
             'company': reasoner.store.job(jid).company} for i, jid in enumerate(ids, 1)]}
            for n, ids in enumerate(state.displays, 1)]}
    try:
        plan = reasoner.call(rid, PROTOCOL, payload, RequirementPlan)
    except ValidationError as exc:
        reasoner.store.event(rid, 'requirement_schema_retry', {'paths': [list(e['loc']) for e in exc.errors()]})
        plan = reasoner.call(rid, PROTOCOL + '\nmode只能是recommend/compare_existing/reference/reply/clarify；'
                             'refine/append/remove是operation，不能填入mode。所有字段必须符合schema。',
                             payload, RequirementPlan)
    try:
        intent = compile_plan(state, message, plan, displayed)
        issues = coverage_issues(state, message, plan, intent)
        if issues:
            raise ValueError('; '.join(issues))
    except ValueError as exc:
        reasoner.store.event(rid, 'requirement_plan_retry', {'reason': str(exc), 'draft': plan.model_dump()})
        plan = reasoner.call(rid, PROTOCOL + '\n只修复error中定位的具体错误，保留其他正确操作。'
                             '不要额外解释，不修改未被用户提及的条件。输出完整修正计划。',
                             {**payload, 'draft': plan.model_dump(), 'error': str(exc)}, RequirementPlan)
        intent = compile_plan(state, message, plan, displayed)
        issues = coverage_issues(state, message, plan, intent)
        if issues:
            raise ValueError('requirement repair still fails coverage: ' + '; '.join(issues))
    reasoner.store.event(rid, 'requirement_plan', plan.model_dump())
    return intent
