"""Narrow, inspectable checks for contradictions in a delivery's free text.

These checks do not establish general semantic entailment. They catch explicit
company-size claims when the current condition evidence remains unknown.
"""
import re

from .models import Delivery, State


def company_scale_issues(state: State, delivery: Delivery) -> list[dict]:
    keys = {c.key for c in state.constraints.values()
            if re.search(r'大公司|大企业|公司规模|企业规模', c.text)}
    issues = []
    for item in delivery.items:
        assessment = state.assessments.get(item.job_id)
        if not assessment or not any(e.key in keys and e.verdict == 'unknown' for e in assessment.evidence):
            continue
        for field in ('fit', 'concern', 'advice'):
            text = getattr(item, field)
            for sentence in re.split(r'[。！？；，,\n]', text):
                # Explicit uncertainty/negation is an acceptable description, not a size claim.
                if re.search(r'未(?:知|确认|核实|证实)|尚未|待(?:确认|核实)|无法确认|不能确认|不等于|'
                             r'不(?:是|属于|算)|并非|是否|若|如果', sentence):
                    continue
                if re.search(r'大厂|大公司|大企业|(?:体量|规模)(?:较|很|非常)?大|大型(?:企业|公司)', sentence):
                    issues.append({'job_id': item.job_id, 'field': field,
                                   'reason': 'company_size_unknown_but_asserted'})
                    break
    return issues
