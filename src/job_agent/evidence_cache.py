"""Reuse validated condition evidence only when its exact inputs remain unchanged."""
import hashlib
import json

from .models import EvidenceCache, Job, State
from .constraints import location_evidence, salary_evidence, attendance_evidence, schedule_evidence


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def condition_fingerprints(state):
    return {k: fingerprint((c.text, c.kind)) for k, c in state.constraints.items()}


def background_fingerprint(state):
    return fingerprint({k: f.text for k, f in state.background.items()})


def reusable_evidence(state: State, job: Job):
    cache = state.evidence_cache.get(job.id)
    if (not cache or not job.description or cache.job_fingerprint != fingerprint(job.evidence_text())
            or cache.background_fingerprint != background_fingerprint(state)):
        return []
    current = condition_fingerprints(state)
    return [e.model_copy(deep=True) for e in cache.evidence if e.key in current
            and cache.condition_fingerprints.get(e.key) == current[e.key]]


def program_evidence(state: State, job: Job):
    evidence = []
    for condition in state.constraints.values():
        result = (location_evidence(condition, job, state.filters.get('city'))
                  or salary_evidence(condition, job) or attendance_evidence(condition, job)
                  or schedule_evidence(condition, job))
        if result is not None:
            evidence.append(result)
    return evidence


def remember_evidence(state: State, job: Job, evidence):
    if job.description:
        state.evidence_cache[job.id] = EvidenceCache(
            job_fingerprint=fingerprint(job.evidence_text()),
            background_fingerprint=background_fingerprint(state),
            condition_fingerprints=condition_fingerprints(state),
            evidence=[e.model_copy(deep=True) for e in evidence])
