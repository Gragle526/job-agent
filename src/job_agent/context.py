"""Program-derived task observations and compact, current decision context."""
import re

from .models import Checkpoint, Job, ReviewRound, State, TaskObservation, TaskProgress


def remember(state: State, observation: TaskObservation):
    state.observations.append(observation)
    state.observations = state.observations[-80:]


def recent_dialogue(state: State):
    # The structured state is authoritative; old user requests may have been replaced.
    return [m for m in state.history if m['role'] == 'user'][-6:]


def quote_fragments(job: Job):
    fragments = []
    for line in job.evidence_text().splitlines():
        for start in range(0, len(line), 200):
            text = line[start:start + 200].strip()
            if text:
                fragments.append(text)
    return fragments


def refresh_progress(cp: Checkpoint, jobs: list[Job]) -> TaskProgress:
    state = cp.state
    detailed = {j.id for j in jobs if j.description}
    assessed = {jid: a for jid, a in state.assessments.items()
                if a.version == state.version and jid in state.known_ids}
    new_reviewed = [jid for jid in state.known_ids if jid in assessed and jid in detailed
                    and jid not in cp.reviewed_ids]
    hard_keys = [k for k, c in state.constraints.items() if c.kind == 'hard']
    if new_reviewed:
        viable = [assessed[jid] for jid in new_reviewed if assessed[jid].category != 'excluded']
        unknown = [k for k in hard_keys if viable and all(
            next((e.verdict for e in a.evidence if e.key == k), 'unknown') == 'unknown' for a in viable)]
        cp.review_rounds.append(ReviewRound(job_ids=new_reviewed, viable_count=len(viable), unknown_keys=unknown,
                                          fresh_reads=sum(jid in cp.detailed for jid in new_reviewed)))
        cp.reviewed_ids.extend(new_reviewed)
        remember(state, TaskObservation(version=state.version, kind='review', job_ids=new_reviewed))
    counts = {k: dict.fromkeys(('satisfied', 'violated', 'unknown'), 0) for k in hard_keys}
    for a in assessed.values():
        for k in hard_keys:
            verdict = next((e.verdict for e in a.evidence if e.key == k), 'unknown')
            counts[k][verdict] += 1
    persistent = []
    rounds = cp.review_rounds[-2:]
    if (len(rounds) == 2 and sum(r.viable_count for r in rounds) >= 4
            and sum(r.fresh_reads for r in rounds) >= 4 and all(r.fresh_reads and r.viable_count for r in rounds)):
        for k in hard_keys:
            if all(k in r.unknown_keys for r in rounds) and not any(
                    a.category != 'excluded' and any(e.key == k and e.verdict == 'satisfied' for e in a.evidence)
                    for a in assessed.values()):
                persistent.append(k)
    searches = [o for o in state.observations if o.kind == 'search' and o.version == state.version
                and o.status in ('ok', 'empty')][-2:]
    duplicates = len(searches) == 2 and all(o.new_candidates == 0 for o in searches)
    progress = TaskProgress(known_candidates=len(state.known_ids), details_available=len(detailed),
        assessed=len(assessed), confirmed=sum(a.category == 'recommended' for a in assessed.values()),
        uncertain=sum(a.category == 'uncertain' for a in assessed.values()),
        excluded=sum(a.category == 'excluded' for a in assessed.values()), failed_reads=len(cp.failed_details),
        coverage=counts, persistent_gap_keys=persistent, duplicate_searches=duplicates)
    summary = (f'本会话有 {progress.known_candidates} 个候选、{progress.details_available} 条完整详情；'
               f'最新需求已核验 {progress.assessed} 个，{progress.confirmed} 个满足已核验硬条件，'
               f'{progress.uncertain} 个待核实、{progress.excluded} 个已排除。')
    if persistent:
        labels = '、'.join(state.constraints[k].text for k in persistent)
        summary += f'最近两批可保留候选均未提供「{labels}」的明确依据，应换查法或先交付；不能推断全站缺失。'
    if duplicates:
        summary += '最近两次成功搜索均未增加候选，应改变查法或停止重复搜索。'
    if cp.failed_details:
        summary += f'{len(cp.failed_details)} 次详情获取失败，不能视为岗位未说明条件。'
    progress.summary = summary
    cp.progress = progress
    return progress


def gap_terms(text: str) -> list[str]:
    if re.search(r'双休|休息|大小周|单休', text):
        return ['双休', '大小周', '单休', '周末', '休息制度', '工作制度']
    if re.search(r'远程|线上', text):
        return ['远程', '线上', '办公', '到岗']
    if re.search(r'正式|全职|实习', text):
        return ['正式', '全职', '实习', '劳动合同']
    if re.search(r'外包|派遣|驻场', text):
        return ['外包', '派遣', '驻场', '劳动合同']
    if re.search(r'每周|天/周', text):
        return ['天/周', '每周', '出勤']
    return [text]  # No speculative synonyms for arbitrary user conditions.


def has_gap_clue(job: Job, cp: Checkpoint) -> bool:
    return any(term in job.evidence_text() for k in cp.progress.persistent_gap_keys
               for term in gap_terms(cp.state.constraints[k].text))


def detail_pool(cp: Checkpoint, jobs: list[Job]) -> list[Job]:
    pool = [j for j in jobs if not j.description and j.id not in cp.failed_details and
            (j.id in cp.force_assess or j.id not in cp.state.assessments
             or cp.state.assessments[j.id].category != 'excluded')]
    if cp.progress.persistent_gap_keys:
        pool = [j for j in pool if has_gap_clue(j, cp)]
    return pool


def decision_context(cp: Checkpoint, jobs: list[Job]) -> dict:
    state = cp.state
    return {
        'requirements': {k: v.model_dump() for k, v in state.constraints.items()},
        'background': {k: v.model_dump() for k, v in state.background.items()},
        'dialogue': recent_dialogue(state),
        'output_requirements': {k: v.model_dump() for k, v in state.output.items()},
        'current_objective': state.objective,
        'task_progress': cp.progress.model_dump(),
        'recent_observations': [o.model_dump() for o in state.observations[-8:]],
        'search_history': [o.model_dump(exclude={'job_ids'}) for o in state.observations
                           if o.kind == 'search'][-16:],
        'remaining_seconds': cp.remaining_seconds,
        'remaining_tools': cp.remaining_tools,
        'question': cp.intent.question, 'initial_queries': state.queries,
        'searched_queries': cp.searched, 'filters': state.filters, 'tool_calls_used': cp.tool_calls,
        'candidates': [{'id': j.id, 'title': j.title, 'city': j.city, 'salary': j.salary,
                        'job_type': j.job_type, 'metadata': j.detail_metadata, 'has_detail': bool(j.description)}
                       for j in jobs],
        'assessments': [a.model_dump() for a in state.assessments.values() if a.version == state.version],
        'notices': cp.notices[-4:],
    }
