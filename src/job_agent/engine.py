import time
import re
import traceback

from .constraints import (attendance_evidence, employment_evidence, explicit_weekly_limit,
                          metadata_conflict, remote_evidence, unrestricted_city, location_evidence, is_output_policy,
                          salary_evidence, schedule_evidence, employment_type_evidence, product_direction_evidence,
                          company_size_evidence, employment_background_only)
from .context import detail_pool, refresh_progress, remember, gap_terms
from .evidence_cache import remember_evidence, reusable_evidence

from .models import Action, Assessment, Checkpoint, Constraint, Evidence, Fact, Intent, MatchOutput, Status, TaskObservation
from .providers import Snapshot
from .boss_cdp import BossCDP
from .tencent import TencentCareers
from .store import BudgetExceeded, StaleRun, Store


class Engine:
    def __init__(self, store: Store, provider, reasoner, tool_limit=12, step_limit=24, seconds_limit=180):
        self.store, self.provider, self.reasoner = store, provider, reasoner
        self.tool_limit, self.step_limit, self.seconds_limit = tool_limit, step_limit, seconds_limit
        self.deadlines = {}

    def start(self, sid: str, message: str):
        if not message.strip():
            raise ValueError("empty message")
        return self.store.begin(sid, message.strip())

    def run(self, rid: str, state=None):
        start = time.monotonic()
        self.deadlines[rid] = start + self.seconds_limit
        try:
            if state is None:
                row = self.store.run(rid)
                if not row["checkpoint"]:
                    state = self.store.session(row["session_id"])
                else:
                    cp = Checkpoint.model_validate_json(row["checkpoint"])
                    self.store.check(rid)
                    if self.store.is_paused(rid):
                        self.store.commit(rid, cp, "running", resume=True)
                    if cp.completed:
                        self.store.commit(rid, cp, "completed")
                        return cp
            if state is not None:
                self.store.check(rid)
                # Switching data sources must never carry demo recommendations into live mode.
                if isinstance(self.provider, (BossCDP, TencentCareers, Snapshot)):
                    permitted = (set(self.provider.catalog) if isinstance(self.provider, Snapshot) else
                                 {j for j in state.known_ids if self.store.job(j).provenance == "live"
                                  and self.store.job(j).source == ("boss" if isinstance(self.provider, BossCDP)
                                                                   else TencentCareers.source)})
                    removed = set(state.known_ids) - permitted
                    state.known_ids = [j for j in state.known_ids if j in permitted]
                    state.assessments = {j: a for j, a in state.assessments.items() if j in permitted}
                    if removed:
                        state.display = []
                        state.displays = []
                cp = self.prepare(rid, state)
            while cp.steps < self.step_limit and time.monotonic() - start < self.seconds_limit:
                self.store.check(rid)
                if self.store.is_paused(rid):
                    return cp
                cp.remaining_seconds = max(0, self.deadlines[rid] - time.monotonic())
                allowed = self.allowed(cp)
                if len(allowed) == 1 and not (allowed[0].kind == "detail_batch" or
                        (allowed[0].kind == "search_batch" and not allowed[0].queries)):
                    action = allowed[0]
                else:
                    jobs = [self.store.job(j) for j in cp.state.known_ids]
                    action = self.reasoner.choose(rid, cp, allowed, jobs)
                    exact = any((a.kind != "search_batch" or a.queries) and
                                (a.kind, a.query, a.job_id, a.queries, a.job_ids) ==
                                (action.kind, action.query, action.job_id, action.queries, action.job_ids)
                                for a in allowed)
                    new_query = (action.kind == "search" and 0 < len(action.query.strip()) <= 80
                                 and action.query not in cp.searched
                                 and any(a.kind == "search" and not a.query for a in allowed))
                    new_batch = (action.kind == "search_batch" and 0 < len(action.queries) <= min(
                                 3, 6 - len(cp.searched), self.tool_limit - cp.tool_calls)
                                 and len(set(action.queries)) == len(action.queries)
                                 and all(0 < len(q.strip()) <= 80 and q not in cp.searched for q in action.queries)
                                 and not action.job_ids and not action.job_id and not action.query
                                 and any(a.kind == "search_batch" and not a.queries for a in allowed))
                    eligible = {j.id for j in detail_pool(cp, jobs)}
                    selected_details = (action.kind == "detail_batch" and
                        any(a.kind == "detail_batch" for a in allowed) and
                        0 < len(action.job_ids) <= min(3, self.tool_limit - cp.tool_calls) and
                        len(set(action.job_ids)) == len(action.job_ids) and
                        set(action.job_ids) <= eligible and not action.job_id and
                        not action.query and not action.queries)
                    if not (exact or new_query or new_batch or selected_details):
                        self.store.event(rid, "rejected_action", {"proposed": action.model_dump(),
                                                                  "allowed": [a.model_dump() for a in allowed]})
                        raise ValueError("policy selected an inadmissible action")
                if action.kind in ('search', 'search_batch') and hasattr(self.reasoner, 'review_search'):
                    proposed = action.queries if action.kind == 'search_batch' else [action.query]
                    accepted = self.reasoner.review_search(rid, cp, proposed)
                    self.store.check(rid)
                    if self.store.is_paused(rid):
                        return cp
                    if (len(accepted) != len(set(accepted)) or not set(accepted) <= set(proposed)):
                        raise ValueError('search scope review cannot introduce new queries')
                    if not accepted:
                        self.store.event(rid, 'search_scope_rejected', {'queries': proposed})
                        cp.notices.append('本轮提出的检索方向偏离了保留的岗位要求，已取消该搜索；以下仅依据已取得的候选，未擅自改变条件。')
                        self.finish(cp, rid)
                        self.store.commit(rid, cp, 'completed')
                        return cp
                    if action.kind == 'search_batch':
                        action.queries = accepted
                    else:
                        action.query = accepted[0]
                self.store.check(rid)
                if self.store.is_paused(rid):
                    return cp
                if time.monotonic() >= self.deadlines[rid]:
                    break
                self.store.event(rid, "action", {**action.model_dump(), "version": cp.state.version,
                                                "tool_calls_before": cp.tool_calls})
                if action.objective:
                    cp.state.objective = action.objective
                cp.steps += 1
                self.execute(rid, cp, action)
                self.store.check(rid)
                # A pause during an external call keeps its observation but does not publish results.
                status = "paused" if self.store.is_paused(rid) else ("completed" if cp.completed else "running")
                self.store.commit(rid, cp, status)
                if cp.completed or status == "paused":
                    return cp
            cp.notices.append("本轮达到步骤或时间预算，保留已完成结果。")
            self.finish(cp, rid)
            self.store.commit(rid, cp, "completed")
            return cp
        except StaleRun:
            self.store.event(rid, "superseded", {"message": "old results were not published"})
            return None
        except Exception as exc:
            known = isinstance(exc, (BudgetExceeded, ValueError, RuntimeError))
            error = {'type': type(exc).__name__, 'message': str(exc)[:250] if known else
                     'Unexpected worker exception; checkpoint retained when available'}
            if not known:
                error['location'] = [{'file': f.filename, 'line': f.lineno, 'function': f.name}
                                     for f in traceback.extract_tb(exc.__traceback__)[-8:]]
            self.store.event(rid, "error", error)
            # Keep last completed checkpoint for resumption; do not fabricate an answer.
            row = self.store.run(rid)
            if row["checkpoint"]:
                last = Checkpoint.model_validate_json(row["checkpoint"])
                try:
                    if isinstance(exc, BudgetExceeded):
                        last.notices.append("本轮模型预算不足，停止调用并保留已有证据；结果可能不完整。")
                        self.finish(last)
                        self.store.commit(rid, last, "completed")
                        return last
                    if isinstance(exc, ValueError) and last.intent.response_mode == 'reply':
                        last.answer = '本轮回答未通过证据检查，未发布未经核实的结论。可以重述问题或指定岗位名称。'
                        self.finish(last)
                        self.store.commit(rid, last, 'completed')
                        return last
                    self.store.commit(rid, last, "paused")
                except StaleRun:
                    pass
                except Exception as recovery_error:
                    self.store.event(rid, 'error', {'type': type(recovery_error).__name__,
                        'message': 'Error recovery could not complete; no unverified answer published'})
                    self.store.mark_failed(rid)
            else:
                if isinstance(exc, (ValueError, BudgetExceeded)):
                    # Roll back incomplete preparation; do not silently apply or endlessly
                    # replay a rejected update on every later message.
                    safe = self.store.session(row['session_id'])
                    safe.unresolved_messages.extend(safe.unapplied_messages)
                    safe.unapplied_messages.clear()
                    message = ('本轮模型预算不足，最新修改尚未确认生效。' if isinstance(exc, BudgetExceeded)
                               else '本轮未能可靠理解你的请求，最新修改尚未确认生效。')
                    message += '\n当前有效要求：' + ('；'.join(c.text for c in safe.constraints.values()) or '尚未建立。')
                    message += '\n请求已保留，可重述需要修改的条件或继续询问已有岗位。'
                    recovered = Checkpoint(state=safe,
                        intent=Intent(response_mode='reply', clarification=message), answer=message)
                    self.finish(recovered)
                    self.store.commit(rid, recovered, 'completed')
                    return recovered
                self.store.mark_failed(rid)
            return None
        finally:
            self.deadlines.pop(rid, None)

    def prepare(self, rid, state):
        """Understand and apply a versioned user requirement update."""
        original_hard_keys = {key for key,c in state.constraints.items() if c.kind=='hard'}
        pending_text = "\n".join(state.unapplied_messages) or state.pending
        supported = sorted(getattr(self.provider, "supported_filters", {"city", "job_type"}))
        state.search_capabilities = {"supported_filters": supported,
            "notes": getattr(self.provider, "filter_notes", "只使用明确可支持的筛选参数")}
        intent = self.reasoner.understand(rid, state, pending_text)
        self.store.check(rid)
        before = {scope: {k: v.model_dump() for k, v in values.items()} for scope, values in
                  [('condition', state.constraints), ('background', state.background), ('output', state.output)]}
        if intent.changes or intent.queries:
            state.objective = ''
        changed = False
        for key, constraint in list(state.constraints.items()):
            if employment_background_only(constraint.text, constraint.quote):
                state.constraints.pop(key)
                changed = True
                self.store.event(rid, 'noncondition_ignored', {'key': key,
                    'reason': '正式工作经历属于个人背景，不是岗位用工要求'})
                continue
            if is_output_policy(constraint.text):
                state.output[f"policy_{key}"] = Fact(key=f"policy_{key}", text=constraint.text,
                                                   quote=constraint.quote)
                state.constraints.pop(key)
                changed = True
                self.store.event(rid, "scope_corrected", {"key": key, "to": "output",
                                                          "reason": "回答规则不属于岗位条件"})
        for change in intent.changes:
            if change.scope == "condition" and change.op == "set" and is_output_policy(change.text):
                self.store.event(rid, "scope_corrected", {"key": change.key, "to": "output",
                                                          "reason": "回答规则不属于岗位条件"})
                change.scope = "output"
                change.key = f"policy_{change.key}"
            if not change.quote.strip() or change.quote not in pending_text:
                raise ValueError("requirement patch has no user evidence")
            if (change.scope == 'condition' and change.op == 'set'
                    and employment_background_only(change.text, change.quote)):
                self.store.event(rid, 'noncondition_ignored', {'key': change.key,
                    'reason': '正式工作经历属于个人背景，不是岗位用工要求'})
                continue
            if change.scope != "condition":
                if change.scope == 'output' and change.key in ('output_max_results', 'output_instructions'):
                    original_key = change.key
                    change.key = change.key.removeprefix('output_')
                    self.store.event(rid, 'output_key_normalized', {
                        'from': original_key, 'to': change.key})
                target = state.background if change.scope == "background" else state.output
                if change.op == "remove":
                    if not change.explicit:
                        raise ValueError("background/output removal requires explicit feedback")
                    removed = target.pop(change.key, None) is not None
                    changed |= change.scope == "background" and removed
                else:
                    if change.scope == "output" and change.key == "max_results":
                        if not change.text.isdigit() or not 1 <= int(change.text) <= 10:
                            raise ValueError("max_results must be 1..10")
                    fact = Fact(key=change.key, text=change.text, quote=change.quote)
                    # Background changes can affect fit; output changes alone need no re-scoring.
                    changed |= change.scope == "background" and target.get(change.key) != fact
                    target[change.key] = fact
                continue
            previous = state.constraints.get(change.key)
            if change.op == 'set' and re.search(r'薪资|工资|月薪', change.text) and re.search(
                    r'不限|不(?:是|作为|作)硬条件|不是硬性|无(?:硬性)?要求', change.text):
                # A waived restriction is not an unknown salary requirement.
                if previous:
                    change.op, change.explicit = 'remove', True
                else:
                    self.store.event(rid, 'noncondition_ignored', {'key': change.key,
                        'reason': '明确无薪资限制不新增岗位核验项'})
                    continue
            if (previous and previous.kind == 'hard' and change.op == 'set' and change.kind == 'soft'
                    and re.search(r'优先|偏好|最好|倾向|更想', change.quote)
                    and not re.search(r'取消|放宽|不再|不用|改为|改成|不要求|不限|去掉|删除', change.quote)):
                # A preference refinement does not revoke an existing hard requirement.
                old_key = change.key
                change.key = f'preference_{old_key}'
                previous = state.constraints.get(change.key)
                self.store.event(rid, 'preference_refinement', {'hard_key_preserved': old_key,
                                                             'preference_key': change.key})
            same_condition = (previous is not None and change.op == "set" and
                              (previous.text, previous.kind) == (change.text, change.kind))
            if previous and previous.kind == "hard" and not change.explicit and not same_condition:
                intent.clarification = f"请明确是否修改硬约束：{previous.text}"
                continue
            if change.op == "remove":
                if not change.explicit:
                    intent.clarification = "请明确要删除的条件"
                    continue
                changed |= state.constraints.pop(change.key, None) is not None
                if change.key in state.filters:
                    state.filters.pop(change.key)
                if change.key in ('city', 'location', 'base'):
                    state.filters.pop('city', None)
            else:
                constraint = Constraint(key=change.key, text=change.text, kind=change.kind,
                                        quote=change.quote)
                if explicit_weekly_limit(change.quote):
                    constraint.kind = "hard"
                changed |= not same_condition
                if not same_condition:
                    # A changed/removal condition invalidates its old platform filter.
                    state.filters.pop(change.key, None)
                state.constraints[change.key] = constraint
        # Unrestricted geography is the absence of a constraint, not an unknown JD fact.
        for key, constraint in list(state.constraints.items()):
            if unrestricted_city(constraint.text):
                state.constraints.pop(key)
                state.filters.pop("city", None)
                changed = True
        if intent.queries:
            state.queries = intent.queries
        if intent.filters:
            rejected = {c.key for c in intent.changes if c.key in original_hard_keys
                        and c.key in state.constraints
                        and state.constraints[c.key].kind == "hard" and not c.explicit}
            for key, value in intent.filters.items():
                if key in rejected:
                    continue
                state.filters[key] = value
        if state.filters.get("city") in ("全国", "不限"):
            state.filters.pop("city")
        unsupported = {k: v for k, v in state.filters.items() if k not in supported}
        for key in unsupported:
            state.filters.pop(key)
        if unsupported:
            self.store.event(rid, "search_filter_removed", {
                "unsupported": unsupported, "message": "不支持的搜索参数已移除；岗位条件仍保留用于核验"})
        if changed:
            state.assessments.clear()
        else:
            for assessment in state.assessments.values():
                assessment.version = state.version
        for scope, values in [('condition', state.constraints), ('background', state.background),
                              ('output', state.output)]:
            after = {k: v.model_dump() for k, v in values.items()}
            for key in sorted(set(before[scope]) | set(after)):
                if before[scope].get(key) != after.get(key):
                    state.requirement_history.append({'scope': scope, 'key': key,
                        'before': before[scope].get(key), 'after': after.get(key),
                        'message': pending_text, 'version': state.version})
        cp = Checkpoint(intent=intent, state=state)
        # Re-check retained evidence when validation rules improve across versions.
        for jid, assessment in list(state.assessments.items()):
            job = self.store.job(jid)
            if job.description and {e.key for e in reusable_evidence(state, job)} != set(state.constraints):
                # A newly fetched JD or a legacy record cannot stamp old semantic
                # judgments with the new document's fingerprint.
                state.assessments.pop(jid)
                continue
            self.apply_assessment(cp, jid, MatchOutput(evidence=assessment.evidence))
        state.unapplied_messages.clear()
        self.store.event(rid, "intent", intent.model_dump())
        self.store.commit(rid, cp, "paused" if self.store.is_paused(rid) else "running")
        return cp

    def allowed(self, cp: Checkpoint) -> list[Action]:
        cp.remaining_tools = max(0, self.tool_limit - cp.tool_calls)
        jobs = [self.store.job(jid) for jid in cp.state.known_ids]
        for job in jobs:
            if job.id not in cp.state.assessments and not job.description:
                conflicts = [e for c in cp.state.constraints.values() if (e := metadata_conflict(c, job))]
                if conflicts:
                    self.apply_assessment(cp, job.id, MatchOutput(evidence=conflicts))
        refresh_progress(cp, jobs)
        cp.eligible_detail_ids = [j.id for j in detail_pool(cp, jobs)]
        if cp.intent.clarification:
            return [Action(kind="clarify", reason=cp.intent.clarification)]
        if cp.answer:
            return [Action(kind="stop", reason="answer complete")]
        if cp.intent.response_mode == 'reply':
            for jid in cp.intent.target_ids:
                job = self.store.job(jid)
                if not job.description and jid not in cp.failed_details and cp.tool_calls < self.tool_limit:
                    return [Action(kind='detail', job_id=jid, reason='仅补读用户明确引用的岗位')]
            return [Action(kind='reply', reason='直接回答当前问题，不重新搜索或推荐')]
        if (cp.intent.changes and all(c.scope == "output" for c in cp.intent.changes)
                and not cp.intent.queries and not cp.intent.reference
                and any(a.version == cp.state.version for a in cp.state.assessments.values())):
            return [Action(kind="stop", reason="仅调整交付形式，复用已有核验结果")]
        # A number is resolved against the last published display, never against a live sorted list.
        if cp.intent.reference:
            if cp.intent.reference > len(cp.state.display):
                cp.intent.clarification = "该编号不在最近展示中，请指出岗位名称或有效编号。"
                return [Action(kind="clarify", reason=cp.intent.clarification)]
            jid = cp.state.display[cp.intent.reference - 1]
            job = self.store.job(jid)
            if not job.description and jid not in cp.failed_details and cp.tool_calls < self.tool_limit:
                return [Action(kind="detail", job_id=jid, reason="read the referenced JD")]
            return [Action(kind="answer", job_id=jid, reason="answer the referenced requirement")]
        actions = []
        existing_only = bool(cp.state.output.get('search_policy') and
                             cp.state.output['search_policy'].text == 'existing_only')
        if cp.tool_calls < self.tool_limit and not existing_only:
            queries = [q for q in cp.state.queries if q not in cp.searched]
            if queries:
                actions.append(Action(kind="search_batch", queries=queries[:1],
                                      reason="按批次取得候选，不改变岗位条件"))
            if cp.searched and len(cp.searched) < 6 and (cp.progress.persistent_gap_keys or
                    all(q in cp.searched for q in cp.state.queries)):
                actions.append(Action(kind="search_batch", reason="可规划下一批新检索词，保留现有岗位条件"))
        missing, unevaluated = [], []
        evidence_size = 0
        for jid in cp.state.known_ids:
            job = self.store.job(jid)
            assessment = cp.state.assessments.get(jid)
            if not assessment and not job.description:
                conflicts = [e for c in cp.state.constraints.values() if (e := metadata_conflict(c, job))]
                if conflicts:
                    self.apply_assessment(cp, jid, MatchOutput(evidence=conflicts))
                    assessment = cp.state.assessments[jid]
            if assessment and assessment.category == "excluded" and jid not in cp.force_assess:
                continue
            if not job.description and jid in cp.eligible_detail_ids:
                if cp.tool_calls < self.tool_limit and not existing_only:
                    missing.append(jid)
            elif job.description and (not assessment or jid in cp.force_assess):
                size = len(job.evidence_text().encode())
                if len(unevaluated) < 3 and (not unevaluated or evidence_size + size <= 10000):
                    unevaluated.append(jid)
                    evidence_size += size
        if missing:
            actions.append(Action(kind="detail_batch", job_ids=missing[:min(3, self.tool_limit - cp.tool_calls)],
                                  reason="补齐一批详情后再决策"))
        if unevaluated:
            actions.append(Action(kind="assess_batch", job_ids=unevaluated,
                                  reason="结合完整需求批量核验岗位"))
            # Matching already acquired JDs precedes further retrieval or publication.
            # It consumes model budget, not another browser call.
            return [actions[-1]]
        if not cp.state.queries and not cp.state.known_ids:
            cp.intent.clarification = "你希望寻找什么岗位或工作内容？"
            return [Action(kind="clarify", reason=cp.intent.clarification)]
        viable_count = sum(a.version == cp.state.version and a.category != "excluded"
                           for a in cp.state.assessments.values())
        viable = bool(viable_count)
        concrete_opportunity = any(a.kind == "detail_batch" or
                                   (a.kind == "search_batch" and a.queries) for a in actions)
        new_direction_unsearched = bool(cp.intent.queries and not cp.searched and
                                       any(a.kind == "search_batch" and a.queries for a in actions))
        incomplete_shortlist = bool(missing and viable_count < cp.state.result_limit())
        if new_direction_unsearched:
            # Reserve the opportunity to search before old-pool details consume the entire budget.
            return [a for a in actions if a.kind == "search_batch" and a.queries]
        if cp.progress.persistent_gap_keys or cp.progress.duplicate_searches:
            if cp.progress.duplicate_searches:
                actions = [a for a in actions if a.kind != 'search_batch']
            if cp.progress.persistent_gap_keys:
                terms = [term for k in cp.progress.persistent_gap_keys for term in gap_terms(cp.state.constraints[k].text)]
                actions = [a for a in actions if a.kind != 'search_batch' or not a.queries
                           or any(term in q for term in terms for q in a.queries)]
            actions.append(Action(kind='stop', reason=cp.progress.summary))
            return actions
        if incomplete_shortlist:
            # One cached pending job is not evidence that a changed search task is done.
            return actions
        if not viable and concrete_opportunity and cp.state.assessments:
            # No deliverable yet: use the bounded remaining opportunities before giving up.
            return actions
        actions.append(Action(kind="stop", reason="return available evidence or report insufficient candidates"))
        return actions

    def execute(self, rid: str, cp: Checkpoint, action: Action):
        if action.kind in ("search_batch", "detail_batch", "assess_batch"):
            if action.kind == "assess_batch" and hasattr(self.reasoner, "match_many"):
                jobs = [self.store.job(j) for j in action.job_ids]
                result = self.reasoner.match_many(rid, cp.state, jobs)
                if (len(result.jobs) != len(jobs) or
                        {j.job_id for j in result.jobs} != set(action.job_ids)):
                    raise ValueError("batch match changed or duplicated job identity")
                self.store.check(rid)
                for item in result.jobs:
                    self.apply_assessment(cp, item.job_id, MatchOutput(evidence=item.evidence))
                return
            steps = ([Action(kind="search", query=q) for q in action.queries]
                     if action.kind == "search_batch" else
                     [Action(kind="detail" if action.kind == "detail_batch" else "assess", job_id=j)
                      for j in action.job_ids])
            for step in steps:
                self.store.check(rid)
                if self.store.is_paused(rid) or cp.completed:
                    break
                if time.monotonic() >= self.deadlines.get(rid, float("inf")):
                    break
                if step.kind in ("search", "detail") and cp.tool_calls >= self.tool_limit:
                    break
                self.execute(rid, cp, step)
                if cp.completed:
                    break
                # Persist each completed tool step; resume reconstructs only unfinished work.
                self.store.commit(rid, cp, "paused" if self.store.is_paused(rid) else
                                  ("completed" if cp.completed else "running"))
            return
        if action.kind in ("search", "detail"):
            cp.tool_calls += 1
            started = time.monotonic()
            if action.kind == "search":
                result = self.provider.search(action.query, cp.state.filters)
                cp.searched.append(action.query)
            else:
                result = self.provider.fetch_detail(self.store.job(action.job_id))
                cp.detailed.append(action.job_id)
                if result.status != Status.OK:
                    cp.failed_details.append(action.job_id)
            # Raw observations are useful even if a newer turn has invalidated the decision.
            for job in result.jobs:
                self.store.save_job(job)
            self.store.event(rid, "tool", {"action": action.model_dump(), "result": result.model_dump(mode="json")})
            self.store.check(rid)
            new_ids = [j.id for j in result.jobs if j.id not in cp.state.known_ids]
            remember(cp.state, TaskObservation(version=cp.state.version, kind=action.kind,
                status=result.status.value, query=action.query, filters=dict(cp.state.filters),
                job_ids=([action.job_id] if action.kind == 'detail' and result.status != Status.OK
                         else [j.id for j in result.jobs]), new_candidates=len(new_ids),
                repeated_candidates=len(result.jobs) - len(new_ids),
                elapsed_seconds=round(time.monotonic() - started, 3), objective=cp.state.objective))
            cp.state.known_ids = list(dict.fromkeys(new_ids)) + cp.state.known_ids
            if result.status not in (Status.OK, Status.EMPTY):
                cp.notices.append(f"{action.kind}: {result.status} — {result.message}")
            if result.status in (Status.LOGIN, Status.BLOCKED, Status.UNAVAILABLE):
                # End this turn; no fallback to invented or synthetic jobs.
                self.finish(cp, rid)
        elif action.kind == "assess":
            job = self.store.job(action.job_id)
            output = self.reasoner.match(rid, cp.state, job)
            self.store.check(rid)
            self.apply_assessment(cp, job.id, output)
        elif action.kind == "answer":
            job = self.store.job(action.job_id)
            if not job.description:
                cp.answer = "该岗位详情尚未成功取得，暂时无法核实这一要求。"
            else:
                answer = self.reasoner.answer(rid, job, cp.intent.question)
                if not answer.quote.strip() or answer.quote not in job.evidence_text():
                    answer.verdict, answer.quote = "unknown", ""
                labels = {"required": "属于必需要求", "bonus": "属于加分项", "not_required": "明确不要求",
                          "unknown": "JD 中未找到足够依据，待确认"}
                cp.answer = f"{job.title}：{labels[answer.verdict]}。"
                if answer.quote:
                    cp.answer += f"\n原文：{answer.quote}"
                cp.answer += f"\n来源：{job.url}"
        elif action.kind == 'reply':
            ids = cp.intent.target_ids or list(dict.fromkeys(cp.state.display + cp.state.known_ids))[:20]
            jobs = [self.store.job(jid) for jid in ids]
            selecting = not cp.intent.target_ids and bool(re.search(
                r'保留|留下|各留|选.*(?:两个|两条|一个|最匹配|挑战)|给出两条比较', cp.intent.question))
            if selecting:
                jobs = [j for j in jobs if not any(metadata_conflict(c, j) for c in cp.state.constraints.values())]
                if re.search(r'(?:休息制度|双休).*未知', cp.intent.question):
                    schedule = Constraint(key='schedule', text='双休', kind='hard', quote=cp.intent.question)
                    jobs = [j for j in jobs if schedule_evidence(schedule, j).verdict == 'unknown']
            background_changes = [c for c in cp.intent.changes if c.scope == 'background']
            if (background_changes and not cp.intent.target_ids
                    and all(c.scope in ('background', 'output') for c in cp.intent.changes)):
                cp.answer = '已更新你的背景：' + '；'.join(cp.state.background[c.key].text
                    for c in background_changes if c.op == 'set' and c.key in cp.state.background)
                cp.answer += '。后续会按更新后的经历评估岗位。'
            elif hasattr(self.reasoner, 'reply'):
                output = self.reasoner.reply(rid, cp.state, cp.intent.question, jobs)
                self.store.check(rid)
                by_id = {j.id: j for j in jobs}
                selected = output.selected_job_ids
                limit = 2 if re.search(r'两个|两条|各留一个', cp.intent.question) else cp.state.result_limit()
                if (len(selected) != len(set(selected)) or set(selected) - set(by_id)
                        or len(selected) > limit):
                    raise ValueError('reply selection is outside the eligible pool or limit')
                if any(q.job_id not in by_id or not q.quote.strip()
                       or q.quote not in by_id[q.job_id].evidence_text() for q in output.evidence):
                    raise ValueError('reply cites an unavailable job or unverified quote')
                quota_requested = any('编制' in m['content'] for m in cp.state.history if m['role'] == 'user')
                cp.answer = output.text if quota_requested else output.text.replace('正式编制', '正式用工')
                if cp.intent.target_ids:
                    from .requirements import ordinal_targets
                    bindings = []
                    for target in ordinal_targets(cp.state, cp.intent.question):
                        if target.display <= len(cp.state.displays) and target.position <= len(cp.state.displays[target.display - 1]):
                            jid = cp.state.displays[target.display - 1][target.position - 1]
                            if jid in by_id:
                                j = by_id[jid]
                                bindings.append(f'{target.quote}＝{j.company} · {j.title}')
                    if bindings:
                        cp.answer = '本次比较对象：' + '；'.join(bindings) + '。\n' + cp.answer
                if re.search(r'区分.*(?:空结果|未读|故障)|工具故障|执行情况', cp.intent.question):
                    all_jobs = [self.store.job(jid) for jid in cp.state.known_ids]
                    read = sum(bool(j.description) for j in all_jobs)
                    searches = [o for o in cp.state.observations if o.kind == 'search']
                    failures = [o for o in cp.state.observations if o.kind == 'detail' and o.status != 'ok']
                    # Model prose must not replace deterministic counts with counts
                    # inferred from its truncated input pool. Keep job comparisons.
                    cp.answer = '\n'.join(line for line in cp.answer.splitlines() if not (
                        re.match(r'本轮|本次|本会话|当前|其中|剩余|在已读', line.strip())
                        and re.search(r'\d+\s*(?:个|条|次)', line)))
                    cp.answer = (f'本会话有{len(all_jobs)}个候选，{read}条已取得正文，'
                        f'{len(all_jobs)-read}条尚未取得正文。保留执行记录中搜索{len(searches)}次，'
                        f'空结果{sum(o.status=="empty" for o in searches)}次，详情获取失败{len(failures)}次。\n'
                        + cp.answer)
                if re.search(r'总结.*(?:条件|要求)|最终.*(?:条件|要求)', cp.intent.question):
                    cp.answer = re.sub(r'^(?:最终|当前)[^：\n]{0,12}(?:要求|条件)[:：][^。\n]*[。]?\s*', '', cp.answer)
                    saved = '；'.join(c.text for c in cp.state.constraints.values()) or '尚未建立岗位条件'
                    cp.answer = f'当前已保存要求：{saved}。\n' + cp.answer
                for jid, job in by_id.items():
                    cp.answer = cp.answer.replace(jid, f'{job.company} · {job.title}')
                for jid in dict.fromkeys(q.job_id for q in output.evidence):
                    job = by_id[jid]
                    cp.answer += f'\n[{job.company} · {job.title}]({job.url})'
                if selecting:
                    cp.state.display = selected
                    cp.intent.publish_selection = True
                    for jid in selected:
                        job = by_id[jid]
                        requirements = re.split(r'任职要求[:：]?|岗位要求[:：]?|任职资格[:：]?', job.description, maxsplit=1)
                        if len(requirements) > 1:
                            block = re.split(r'加分项|优先条件|岗位职责', requirements[1])[0]
                            key_lines = [line.strip() for line in block.splitlines() if re.search(
                                r'\d+\s*年|[三四五六七八九十]年以上|熟练使用|精通', line)]
                            if key_lines:
                                cp.answer += f'\n{job.company}的关键门槛（JD原文）：' + '；'.join(key_lines[:2])
            else:
                cp.answer = '当前要求：' + '；'.join(c.text for c in cp.state.constraints.values())
                if jobs:
                    cp.answer += '\n已保存岗位：' + '、'.join(j.title for j in jobs)
            self.finish(cp, rid)
        elif action.kind == "clarify":
            cp.answer = cp.intent.clarification
            self.finish(cp, rid)
        else:
            self.finish(cp, rid)

    def apply_assessment(self, cp, jid, output):
        job = self.store.job(jid)
        raw = {e.key: e for e in output.evidence}
        evidence = []
        for c in cp.state.constraints.values():
            e = (location_evidence(c, job, cp.state.filters.get("city")) or salary_evidence(c, job)
                 or schedule_evidence(c, job) or attendance_evidence(c, job)
                 or raw.get(c.key, Evidence(key=c.key, verdict="unknown")))
            e = employment_type_evidence(c, job, e)
            e = product_direction_evidence(c, e)
            e = company_size_evidence(c, e)
            if e.verdict != "unknown" and (not e.quote.strip() or e.quote not in job.evidence_text()):
                e = Evidence(key=c.key, verdict="unknown", explanation="引用无法在原始岗位中核实")
            e = employment_evidence(c, e)
            e = remote_evidence(c, e)
            evidence.append(e)
        hard = [e for e in evidence if cp.state.constraints[e.key].kind == "hard"]
        category = "excluded" if any(e.verdict == "violated" for e in hard) else (
            "uncertain" if any(e.verdict == "unknown" for e in hard) else "recommended")
        score = sum({"satisfied": 1, "violated": -1, "unknown": 0}[e.verdict] for e in evidence)
        cp.state.assessments[jid] = Assessment(job_id=jid, version=cp.state.version,
                                             evidence=evidence, category=category, score=score)
        remember_evidence(cp.state, job, evidence)
        if jid in cp.force_assess:
            cp.force_assess.remove(jid)

    def finish(self, cp: Checkpoint, rid=None):
        state = cp.state
        refresh_progress(cp, [self.store.job(jid) for jid in state.known_ids])
        # Q&A and clarification preserve the referenced display, including ordering.
        if not cp.intent.reference and not cp.intent.clarification and cp.intent.response_mode != 'reply':
            current = [a for a in state.assessments.values()
                       if a.version == state.version and a.category != "excluded"]
            current.sort(key=lambda a: (a.category != "recommended", -a.score, a.job_id))
            state.display = [a.job_id for a in current[:state.result_limit()]]
        if not cp.answer:
            if rid and hasattr(self.reasoner, "deliver"):
                try:
                    jobs = [self.store.job(j) for j in state.display]
                    if hasattr(self.reasoner, "select_delivery") and not cp.intent.reference and not cp.intent.clarification:
                        pool = [self.store.job(a.job_id) for a in state.assessments.values()
                                if a.version == state.version and a.category != "excluded"]
                        delivery = self.reasoner.select_delivery(rid, state, pool, cp.notices)
                        self.store.check(rid)
                        # The model already compares relevance, background and uncertainty.
                        # A caution label is not a ranking score: a relevant job with an
                        # unresolved qualification may outrank an easy but unrelated one.
                        selected = [item.job_id for item in delivery.items]
                        if (len(selected) != len(set(selected))
                                or not set(selected) <= {j.id for j in pool}):
                            raise ValueError("selection must be unique current eligible candidates within limit")
                        if len(selected) > state.result_limit():
                            self.store.event(rid, 'delivery_result_quota_capped', {
                                'requested': len(selected), 'accepted': state.result_limit()})
                            delivery.items = delivery.items[:state.result_limit()]
                            selected = selected[:state.result_limit()]
                        jobs = [self.store.job(jid) for jid in selected]
                        answer = self.render_delivery(state, jobs, delivery)
                        state.display = selected
                        cp.answer = answer
                    else:
                        delivery = self.reasoner.deliver(rid, state, jobs, cp.notices)
                        self.store.check(rid)
                        cp.answer = self.render_delivery(state, jobs, delivery)
                    self.store.event(rid, "delivery", delivery.model_dump())
                except (BudgetExceeded, ValueError, RuntimeError) as exc:
                    cp.notices.append("综合交付未完成，下面仅展示已核验的证据。")
                    self.store.event(rid, "delivery_error", {"type": type(exc).__name__,
                        'message': str(exc)[:180] if not hasattr(exc, 'errors') else 'delivery schema validation failed'})
        if not cp.answer:
            recommended = sum(j in state.assessments and state.assessments[j].version == state.version
                              and state.assessments[j].category == "recommended" for j in state.display)
            cp.answer = f"已整理 {len(state.display)} 个候选，其中 {recommended} 个已满足已核查的硬约束；其余待确认。"
            if not state.display:
                cp.answer = "当前没有足够依据推荐岗位。可以补充关键词、取得详情或检查数据接入。"
            for index, jid in enumerate(state.display, 1):
                job = self.store.job(jid)
                assessment = state.assessments.get(jid)
                unknown = [state.constraints[e.key].text for e in assessment.evidence
                           if e.verdict == 'unknown' and e.key in state.constraints] if assessment else []
                cp.answer += f'\n{index}. {job.company} · {job.title} · {job.city} · {job.salary}'
                if unknown:
                    cp.answer += '\n待确认：' + '、'.join(unknown)
                if not assessment or assessment.version != state.version:
                    cp.answer += '\n匹配状态：尚未按最新要求重新核验。'
                cp.answer += f'\n来源：{job.url}'
            if cp.progress.persistent_gap_keys or cp.progress.failed_reads:
                cp.answer += '\n' + cp.progress.summary
        if cp.notices:
            cp.answer += "\n" + "\n".join(cp.notices)
        if not cp.intent.reference and not cp.intent.clarification and cp.intent.response_mode != 'reply':
            state.displays.append(list(state.display))
        elif cp.intent.publish_selection:
            state.displays.append(list(state.display))
        state.history.append({"role": "assistant", "content": cp.answer})
        state.pending = ""
        cp.completed = True

    def render_delivery(self, state, jobs, delivery):
        quota_requested = any('编制' in m['content'] for m in state.history if m['role'] == 'user')
        def readable(text):
            return text if quota_requested else text.replace('正式编制', '正式用工')
        ids = [item.job_id for item in delivery.items]
        if len(ids) != len(set(ids)) or set(ids) != {j.id for j in jobs}:
            raise ValueError("delivery must cover exactly the current displayed jobs")
        items = {item.job_id: item for item in delivery.items}
        common_unknown = [k for k, c in state.constraints.items() if c.kind == 'hard' and jobs and all(
            any(e.key == k and e.verdict == 'unknown' for e in state.assessments[j.id].evidence) for j in jobs)]
        common_terms = [term for k in common_unknown for term in gap_terms(state.constraints[k].text)]
        overview = readable(delivery.overview.strip())
        named_companies = [job.company for job in jobs if len(job.company) >= 2
                           and job.company not in ('公司', '企业', '集团')]
        if named_companies:
            overview = ''.join(sentence for sentence in re.findall(r'[^。！？]+[。！？]?', overview)
                               if not any(name in sentence for name in named_companies))
        # Never revive the old, unchecked statistical summary as the opening.
        if overview and any(a.category != 'recommended' for a in
                            (state.assessments[j.id] for j in jobs)) and re.search(
                r'全部(?:符合|满足)|均(?:符合|满足)|都(?:符合|满足)', overview):
            overview = ''
        lines = [overview or ('下面是现有岗位的初步比较，重点看适合之处和入职门槛。' if jobs
                              else '现有结果中还没有值得优先推荐的岗位，先不要勉强凑清单。')]
        labels = {'consider': '值得优先了解', 'stretch': '可以尝试，门槛较高',
                  'conditional': '确认资格后再考虑', 'not_first': '暂不优先'}
        def condition_label(key):
            condition = state.constraints[key]
            if re.search(r'type|用工|性质', key, re.I) and '正式' in condition.text:
                return '是否正式用工'
            return condition.text
        for index, job in enumerate(jobs, 1):
            assessment = state.assessments[job.id]
            if assessment.version != state.version or assessment.category == "excluded":
                raise ValueError("delivery attempted to publish stale or excluded job")
            item = items[job.id]
            evidence = {e.key: e for e in assessment.evidence}
            if set(item.evidence_keys) - set(evidence):
                raise ValueError("delivery cites unknown condition evidence")
            if any(not q.strip() or q not in job.evidence_text() for q in item.supporting_quotes):
                raise ValueError('delivery explanation cites text absent from the job')
            label = labels.get(item.priority, '可进一步了解')
            salary_unknown = any(e.verdict == 'unknown' and state.constraints[e.key].kind == 'hard'
                and re.search(r'salary|薪资|工资|月薪', e.key + state.constraints[e.key].text, re.I)
                for e in assessment.evidence)
            if item.priority == 'consider' and salary_unknown:
                label = '先确认薪资再考虑'
            lines.append(f"\n**{index}. {job.company or job.title} · {job.title}｜{label}**")
            location_salary = ' · '.join(v for v in (job.city, job.salary) if v)
            if location_salary:
                lines.append(location_salary)
            lines.append(f'适合之处：{readable(item.fit)}')
            if item.concern:
                lines.append(f'主要顾虑：{readable(item.concern)}')
            else:
                useful = [g for g in item.gaps if not any(t in g for t in
                          common_terms + ['不构成差距', '不构成缺口', '不构成不足', '不是缺口'])]
                if useful:
                    lines.append('主要顾虑：' + '；'.join(readable(v) for v in useful[:2]))
            if item.advice:
                lines.append(f'建议：{readable(item.advice)}')
            else:
                questions = [q for q in item.questions if not any(t in q for t in common_terms)]
                if questions:
                    lines.append('建议核实：' + '；'.join(readable(v) for v in questions[:1]))
            # Row-specific uncertainty is short; full quotes, timestamps and all keys stay in the panel.
            unknown = [condition_label(e.key) for e in assessment.evidence
                       if state.constraints[e.key].kind == 'hard' and e.verdict == 'unknown'
                       and e.key not in common_unknown and not (
                           re.search(r'salary|薪资|工资|月薪', e.key + state.constraints[e.key].text, re.I)
                           and '薪资' in item.concern)]
            if unknown:
                lines.append('另需确认：' + '、'.join(unknown))
            lines.append(f'[查看岗位]({job.url})')
        if common_unknown:
            lines.append('\n还需统一核实：' + '、'.join(condition_label(k) for k in common_unknown)
                         + '。现有信息还不能确认这些条件，不代表全站没有符合岗位。')
        # Shared verification gaps and execution statistics already have their own UI locations.
        limitations = [v for v in delivery.limitations if not any(t in v for t in common_terms)
                       and not re.search(r'检索范围|本会话|获取时间|已核验|已检查|候选数量', v)]
        if limitations:
            lines.append('\n需要注意：' + '；'.join(readable(v) for v in limitations[:2]))
        if delivery.next_steps:
            lines.append('\n接下来：' + '；'.join(readable(v).rstrip('。；;') for v in delivery.next_steps[:2]))
        return "\n\n".join(lines)
