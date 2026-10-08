from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Status(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    LOGIN = "needs_login"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    PARSE = "parse_error"
    UNAVAILABLE = "unavailable"


class Job(Model):
    id: str
    source: str
    source_id: str
    url: str
    title: str
    company: str = ""
    city: str = ""
    salary: str = ""
    job_type: str = ""
    description: str = ""
    detail_metadata: str = ""
    fetched_at: str = Field(default_factory=now)
    provenance: Literal["live", "imported", "synthetic"]
    locator: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "source_id", "title", "url")
    @classmethod
    def nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("missing job identity/title/url")
        return v.strip()

    def evidence_text(self) -> str:
        return "\n".join([self.title, self.company, self.city, self.salary, self.job_type,
                          self.detail_metadata, self.description])


class ToolResult(Model):
    status: Status
    jobs: list[Job] = Field(default_factory=list)
    cursor: str | None = None
    message: str = ""
    raw: Any = None


class Constraint(Model):
    key: str
    text: str
    kind: Literal["hard", "soft"]
    quote: str


class Change(Model):
    op: Literal["set", "remove"]
    key: str
    text: str = ""
    kind: Literal["hard", "soft"] = "soft"
    quote: str
    explicit: bool = False
    scope: Literal["condition", "background", "output"] = "condition"


class Fact(Model):
    key: str
    text: str
    quote: str


class Intent(Model):
    changes: list[Change] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list, max_length=3)
    filters: dict[str, str] = Field(default_factory=dict)
    reference: int | None = Field(default=None, ge=1)
    question: str = ""
    clarification: str = ""
    response_mode: Literal['recommend', 'reply'] = 'recommend'
    target_ids: list[str] = Field(default_factory=list)
    publish_selection: bool = False


class ConversationTitle(Model):
    title: str = Field(min_length=1, max_length=28)


class ReplyQuote(Model):
    job_id: str
    quote: str = Field(default='', max_length=200)
    quote_index: int | None = Field(default=None, ge=0)


class GroundedReply(Model):
    text: str = Field(min_length=1, max_length=1200)
    evidence: list[ReplyQuote] = Field(default_factory=list, max_length=4)
    selected_job_ids: list[str] = Field(default_factory=list, max_length=10)


class Evidence(Model):
    key: str
    verdict: Literal["satisfied", "violated", "unknown"]
    quote: str = ""
    explanation: str = ""
    quote_index: int | None = Field(default=None, ge=0)


class Assessment(Model):
    job_id: str
    version: int
    evidence: list[Evidence]
    category: Literal["recommended", "uncertain", "excluded"] = "uncertain"
    score: float = 0


class MatchOutput(Model):
    evidence: list[Evidence]


class EvidenceCache(Model):
    job_fingerprint: str
    background_fingerprint: str
    condition_fingerprints: dict[str, str]
    evidence: list[Evidence]


class JobMatch(Model):
    job_id: str
    evidence: list[Evidence]


class BatchMatchOutput(Model):
    jobs: list[JobMatch]


class DeliveryItem(Model):
    job_id: str
    # Claims reference validated condition evidence; the program supplies URLs and quotes.
    evidence_keys: list[str]
    fit: str
    gaps: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    priority: Literal['consider', 'stretch', 'conditional', 'not_first'] | None = None
    concern: str = Field(default='', max_length=240)
    advice: str = Field(default='', max_length=160)
    supporting_quotes: list[str] = Field(default_factory=list, max_length=3)


class Delivery(Model):
    summary: str
    items: list[DeliveryItem] = Field(default_factory=list, max_length=10)
    limitations: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    overview: str = Field(default='', max_length=600)


class ReadableDeliveryItem(DeliveryItem):
    priority: Literal['consider', 'stretch', 'conditional', 'not_first']
    concern: str = Field(min_length=1, max_length=240)
    advice: str = Field(min_length=1, max_length=160)
    supporting_quote_indices: list[int] = Field(min_length=1)


class ReadableDelivery(Delivery):
    overview: str = Field(min_length=1, max_length=600)
    items: list[ReadableDeliveryItem] = Field(default_factory=list, max_length=10)

    @field_validator('overview')
    @classmethod
    def substantive_overview(cls, value):
        if not value.strip():
            raise ValueError('overview must answer the user before listing jobs')
        return value.strip()


class OverviewOutput(Model):
    overview: str = Field(min_length=1, max_length=600)


class NoCandidateOutput(OverviewOutput):
    limitations: list[str] = Field(default_factory=list, max_length=2)
    next_steps: list[str] = Field(default_factory=list, max_length=2)


class FragmentSelection(Model):
    job_id: str
    indices: list[int] = Field(min_length=1)


class FragmentSelections(Model):
    jobs: list[FragmentSelection]


class EvidenceKeySelection(Model):
    job_id: str
    keys: list[str] = Field(default_factory=list)


class EvidenceKeySelections(Model):
    jobs: list[EvidenceKeySelection]


class Answer(Model):
    verdict: Literal["required", "bonus", "not_required", "unknown"]
    quote: str = ""


class Action(Model):
    kind: Literal["search", "detail", "assess", "search_batch", "detail_batch", "assess_batch", "answer", "reply", "clarify", "stop"]
    query: str = ""
    job_id: str = ""
    reason: str = ""
    queries: list[str] = Field(default_factory=list, max_length=3)
    job_ids: list[str] = Field(default_factory=list, max_length=5)
    objective: str = Field(default="", max_length=160)


class ActionParameters(Model):
    query: str = ""
    queries: list[str] = Field(default_factory=list, max_length=3)
    detail_indices: list[int] = Field(default_factory=list, max_length=5)
    reason: str = ""
    objective: str = Field(default="", max_length=160)


class ActionSelection(ActionParameters):
    action_index: int = Field(ge=0)


class SearchQueryDecision(Model):
    index: int = Field(ge=0)
    preserves_requirements: bool
    reason: str = Field(max_length=240)


class SearchQueryReview(Model):
    queries: list[SearchQueryDecision] = Field(max_length=3)


class TaskObservation(Model):
    version: int
    kind: Literal["search", "detail", "review"]
    status: str = "ok"
    query: str = ""
    filters: dict[str, str] = Field(default_factory=dict)
    job_ids: list[str] = Field(default_factory=list)
    new_candidates: int = 0
    repeated_candidates: int = 0
    elapsed_seconds: float = 0
    objective: str = ""
    time: str = Field(default_factory=now)


class ReviewRound(Model):
    job_ids: list[str]
    viable_count: int
    unknown_keys: list[str] = Field(default_factory=list)
    fresh_reads: int = 0


class TaskProgress(Model):
    known_candidates: int = 0
    details_available: int = 0
    assessed: int = 0
    confirmed: int = 0
    uncertain: int = 0
    excluded: int = 0
    failed_reads: int = 0
    coverage: dict[str, dict[str, int]] = Field(default_factory=dict)
    persistent_gap_keys: list[str] = Field(default_factory=list)
    duplicate_searches: bool = False
    summary: str = ""


class State(Model):
    version: int = 0
    constraints: dict[str, Constraint] = Field(default_factory=dict)
    background: dict[str, Fact] = Field(default_factory=dict)
    output: dict[str, Fact] = Field(default_factory=dict)
    search_capabilities: dict[str, Any] = Field(default_factory=dict)
    queries: list[str] = Field(default_factory=list)
    filters: dict[str, str] = Field(default_factory=dict)
    known_ids: list[str] = Field(default_factory=list)
    assessments: dict[str, Assessment] = Field(default_factory=dict)
    evidence_cache: dict[str, EvidenceCache] = Field(default_factory=dict)
    display: list[str] = Field(default_factory=list)
    displays: list[list[str]] = Field(default_factory=list)
    history: list[dict[str, str]] = Field(default_factory=list)
    requirement_history: list[dict[str, Any]] = Field(default_factory=list)
    pending: str = ""
    unapplied_messages: list[str] = Field(default_factory=list)
    unresolved_messages: list[str] = Field(default_factory=list)
    observations: list[TaskObservation] = Field(default_factory=list)
    objective: str = ""

    def result_limit(self) -> int:
        value = self.output.get("max_results")
        if value and value.text.strip().isdigit():
            return max(1, min(10, int(value.text.strip())))
        return 5


class Checkpoint(Model):
    @model_validator(mode='before')
    @classmethod
    def read_previous_checkpoint(cls, value):
        # Ignore an obsolete execution selector in existing personal databases.
        if isinstance(value, dict) and 'policy' in value:
            value = {k: v for k, v in value.items() if k != 'policy'}
        return value

    intent: Intent
    state: State
    steps: int = 0
    tool_calls: int = 0
    searched: list[str] = Field(default_factory=list)
    detailed: list[str] = Field(default_factory=list)
    failed_details: list[str] = Field(default_factory=list)
    force_assess: list[str] = Field(default_factory=list)
    answer: str = ""
    notices: list[str] = Field(default_factory=list)
    completed: bool = False
    progress: TaskProgress = Field(default_factory=TaskProgress)
    reviewed_ids: list[str] = Field(default_factory=list)
    review_rounds: list[ReviewRound] = Field(default_factory=list)
    eligible_detail_ids: list[str] = Field(default_factory=list)
    remaining_seconds: float | None = None
    remaining_tools: int = 12
