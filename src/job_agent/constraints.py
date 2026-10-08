"""Small exact comparators for arithmetic; semantic preferences stay with the model."""
import re

from .models import Constraint, Evidence, Job

NUMBERS = dict(zip("一二三四五六七", range(1, 8)))


def unrestricted_city(text: str) -> bool:
    return text.strip() in {'城市不限', '地点不限', '不限城市', '不限地点', '全国', '城市不限制'}


def number(value: str) -> int:
    return int(value) if value.isdigit() else NUMBERS[value]


def weekly_limit(text: str) -> int | None:
    match = re.search(r"(?:每周|一周)\s*(?:最多|只能(?:投入|出勤|实习)?|不超过|可以(?:投入|出勤|实习)?|可(?:投入|出勤|实习)|能(?:投入|出勤|实习)?)"
                      r"\s*([一二三四五六七1-7])\s*天", text)
    return number(match[1]) if match else None


def explicit_weekly_limit(text: str) -> bool:
    return weekly_limit(text) is not None and not re.search(r"最好|尽量|优先|理想|倾向|希望", text)


def attendance_evidence(constraint: Constraint, job: Job) -> Evidence | None:
    condition = constraint.text + '\n' + constraint.quote
    branching = bool('实习' in condition and re.search(r'正式|全职', condition) and '天' in condition)
    if branching:
        kind = ('实习' if job.job_type == '实习' or re.search(r'实习(?!经验|经历)', job.title)
                else '正式' if job.job_type in ('正式', '全职') else None)
        if kind is None:
            return Evidence(key=constraint.key, verdict='unknown', explanation='用工类型未确认，无法选择正确出勤分支')
        branch = re.search(r'实习(.*?)(?=正式|全职|$)' if kind == '实习'
                           else r'(?:正式|全职)(.*?)(?=实习|$)', constraint.text)
        value = re.search(r'([一二三四五六七1-7])\s*天', branch[1]) if branch else None
        limit = number(value[1]) if value else None
        if limit is None:
            return Evidence(key=constraint.key, verdict='unknown', explanation='该类型出勤规则没有可比较天数')
    else:
        limit = weekly_limit(constraint.text)
    if limit is None:
        # A model can omit the capacity qualifier in its canonical text. The
        # unchanged, validated user quote still distinguishes capacity from exact attendance.
        limit = weekly_limit(constraint.quote)
    if limit is None:
        return None
    source = job.evidence_text()
    ranges = list(re.finditer(r"(?:每周|一周)\s*([一二三四五六七1-7])\s*[-–—~～至到]\s*([一二三四五六七1-7])\s*天", source))
    if ranges:
        bounds = [(number(m[1]), number(m[2])) for m in ranges]
        if any(low > high for low, high in bounds):
            return Evidence(key=constraint.key, verdict="unknown", explanation="出勤区间无效，需核实")
        # A compact card can omit the upper bound stated in the full JD.
        # Do not let that single number certify an ambiguous range.
        if any(low <= limit < high for low, high in bounds):
            return Evidence(key=constraint.key, verdict="unknown", quote=ranges[0][0],
                            explanation="正文出勤区间跨过可投入上限；卡片单值不能证明可按下限出勤")
    matches = list(re.finditer(r"(?:每周|一周)(?:至少|最少|不少于)?([一二三四五六七1-7])天|([1-7])天/周", source))
    if ranges:
        required = {number(m[1] or m[2]) for m in matches}
        required.update(v for pair in bounds for v in pair)
        if min(required) <= limit < max(required):
            return Evidence(key=constraint.key, verdict="unknown", quote=ranges[0][0],
                            explanation="出勤描述包含跨过可投入上限的数值，需确认最低强制出勤")
        if any(re.search(r"优先|可协商|最好|建议", source[m.end():m.end() + 12]) for m in ranges):
            return Evidence(key=constraint.key, verdict="unknown", explanation="出勤区间带协商或优先条件，需核实")
        return Evidence(key=constraint.key, verdict="satisfied" if max(required) <= limit else "violated",
                        quote=ranges[0][0], explanation="合并正文出勤区间与卡片数值后比较投入上限")
    if not matches:
        return None
    if any(re.search(r"优先|可协商|最好|建议", source[m.end():m.end() + 12]) for m in matches):
        return Evidence(key=constraint.key, verdict="unknown", explanation="出勤表述有优先或协商条件，需核实强制最低")
    required = {number(m[1] or m[2]) for m in matches}
    if len(required) != 1:
        return Evidence(key=constraint.key, verdict="unknown", explanation="岗位中存在多个出勤数值，待确认")
    minimum = required.pop()
    return Evidence(key=constraint.key, verdict="satisfied" if minimum <= limit else "violated",
                    quote=matches[0][0], explanation=f"最低出勤{minimum}天与可投入上限{limit}天比较")


def metadata_conflict(constraint: Constraint, job: Job) -> Evidence | None:
    """Only explicit list-field contradictions; missing fields never exclude."""
    if constraint.kind != "hard":
        return None
    for comparator in (location_evidence, salary_evidence):
        evidence = comparator(constraint, job)
        if evidence and evidence.verdict == 'violated':
            return evidence
    if re.search(r'全职|正式', constraint.text) and "实习" not in constraint.text:
        if re.search(r"实习生|实习(?!经验|经历)", job.title) or job.job_type == "实习":
            return Evidence(key=constraint.key, verdict="violated",
                            quote=job.title if "实习" in job.title else job.job_type,
                            explanation="列表明确为实习岗位，与全职硬条件冲突")
    return None


def salary_evidence(constraint: Constraint, job: Job) -> Evidence | None:
    if not re.search(r'salary|薪资|月薪|工资|薪酬', constraint.key + constraint.text, re.I):
        return None
    text = constraint.text
    # A legacy model value may have lost units. Recover only an explicit monthly
    # minimum from the user's quote, never guess units for a bare number.
    if re.fullmatch(r'\s*\d+(?:\.\d+)?\s*', text):
        if not (re.search(r'月薪|每月|月工资', constraint.quote)
                and re.search(r'至少|以上|不低于', constraint.quote)):
            return None
        text = constraint.quote
    digits = dict(zip('零一二三四五六七八九', range(10)))
    def chinese_number(match):
        number = match[0]
        if '十' in number:
            left, right = number.split('十', 1)
            return str((digits.get(left, 1) * 10) + digits.get(right, 0))
        return str(digits[number])
    text = re.sub(r'[一二三四五六七八九]?十[一二三四五六七八九]?|[零一二三四五六七八九]',
                  chinese_number, text)
    threshold = re.search(r'(\d+(?:\.\d+)?)\s*(k|w|千|万|元)', text, re.I)
    if not threshold or re.search(r'以内|以下|不超过|年薪|时薪|日薪|/年|/天|/小时|\d\s*[-–~至到]', text):
        return None
    scales = {'k': 1000, 'w': 10000, '千': 1000, '万': 10000, '元': 1}
    minimum = float(threshold[1]) * scales[threshold[2].lower()]
    salary = re.fullmatch(r'\s*(\d+(?:\.\d+)?)(?:\s*[-–~至]\s*(\d+(?:\.\d+)?))?\s*'
                          r'(k|千|万|元)(?:\s*[·•]\s*\d+薪)?\s*', job.salary, re.I)
    if not salary:
        return Evidence(key=constraint.key, verdict='unknown', explanation='缺少可同口径比较的月薪区间')
    lower = float(salary[1]) * scales[salary[3].lower()]
    upper = float(salary[2] or salary[1]) * scales[salary[3].lower()]
    if upper < lower:
        return Evidence(key=constraint.key, verdict='unknown', explanation='薪资区间无效，需核实')
    verdict = 'satisfied' if lower >= minimum else 'violated' if upper < minimum else 'unknown'
    return Evidence(key=constraint.key, verdict=verdict, quote=job.salary,
                    explanation='按月薪下限核验；区间跨过门槛仍需确认实际报价')


def employment_type_evidence(constraint: Constraint, job: Job, evidence: Evidence) -> Evidence:
    if not re.search(r'全职|正式', constraint.text) or '实习' in constraint.text:
        return evidence
    if job.job_type in ('实习', '兼职') or re.search(r'实习生|实习(?!经验|经历)', job.title):
        return Evidence(key=constraint.key, verdict='violated', quote=job.job_type or job.title,
                        explanation='岗位明确为实习或兼职')
    if evidence.verdict == 'satisfied' and not re.search(r'全职|正式|劳动合同|编制', evidence.quote):
        return Evidence(key=constraint.key, verdict='unknown', explanation='岗位方向和职位名称不能证明正式用工')
    return evidence


def product_direction_evidence(constraint: Constraint, evidence: Evidence) -> Evidence:
    """An AI application title alone cannot certify a requested product role."""
    if (constraint.kind != 'hard' or evidence.verdict != 'satisfied'
            or not re.search(r'产品经理|产品岗位|AI\s*产品|产品实习', constraint.text, re.I)
            or re.search(r'不接受|排除|不要|不考虑', constraint.text)):
        return evidence
    if not re.search(r'产品|需求(?:分析|调研|设计|文档|拆解)|PRD|原型|用户研究', evidence.quote, re.I):
        return Evidence(key=constraint.key, verdict='unknown',
                        explanation='AI应用名称不能证明产品职责，需要产品或需求设计的明确原文依据')
    return evidence


def company_size_evidence(constraint: Constraint, evidence: Evidence) -> Evidence:
    if (evidence.verdict != 'satisfied' or not re.search(r'大公司|大企业|公司规模|企业规模', constraint.text)):
        return evidence
    if not re.search(r'\d[\d,，～~\-]*\s*(?:人|名员工)|员工(?:人数|规模)|大型企业|大型公司', evidence.quote):
        return Evidence(key=constraint.key, verdict='unknown',
                        explanation='公司名称或上市信息不能单独证明员工规模，当前来源缺少明确规模证据')
    return evidence


def employment_background_only(text: str, quote: str) -> bool:
    """A narrow provenance check, not a general natural-language scope parser."""
    return bool(re.search(r'正式|全职', text)
                and re.search(r'(?:正式|全职)(?:工作|任职)(?:经验|经历)', quote)
                and not re.search(r'找|岗位|要求|希望|想|接受|招聘', quote))


def schedule_evidence(constraint: Constraint, job: Job) -> Evidence | None:
    if '双休' not in constraint.text or re.search(r'不要求|不限|无所谓|不需要', constraint.text):
        return None
    source = '\n'.join([job.title, job.detail_metadata, job.description])
    negative = re.search(r'(?<!不)(?<!非)大小周|(?<!不)(?<!非)单休|非双休|不(?:支持|实行|提供)?双休', source)
    positive_source = source[:negative.start()] + ' ' + source[negative.end():] if negative else source
    positive = re.search(r'周末双休|双休|周六[、和及与]周日休息', positive_source)
    if positive and negative:
        return Evidence(key=constraint.key, verdict='unknown', explanation='休息制度表述存在冲突，需核实')
    found = positive or negative
    return Evidence(key=constraint.key, verdict='satisfied' if positive else 'violated' if negative else 'unknown',
                    quote=found[0] if found else '', explanation='只核验当前岗位明确的休息制度')


def employment_evidence(constraint: Constraint, evidence: Evidence) -> Evidence:
    """Company identity or silence cannot prove excluded employment arrangements absent."""
    terms = [t for t in ('外包', '派遣', '驻场') if t in constraint.text]
    if (not terms or constraint.kind != 'hard' or evidence.verdict != 'satisfied'
            or not re.search(r'不|非|拒绝|排除', constraint.text)):
        return evidence
    # Deliberately conservative: require an explicit negative statement covering every part.
    negated = set()
    for match in re.finditer(r'(?:非|不(?:是|属于|涉及|存在|安排)?|无(?:需|须)?)(?:客户)?'
                             r'(?:外包|派遣|驻场)(?:[、，,/或和及\s]*(?:客户)?(?:外包|派遣|驻场))*',
                             evidence.quote):
        negated.update(t for t in terms if t in match[0])
    if set(terms) - negated:
        return Evidence(key=constraint.key, verdict='unknown',
                        explanation='公司名称或普通用工描述不能证明不存在外包、派遣或驻场；需逐项明确否定证据')
    return evidence


def remote_evidence(constraint: Constraint, evidence: Evidence) -> Evidence:
    if (constraint.kind != 'hard' or not re.search(r'远程|线上', constraint.text)
            or re.search(r'不接受远程|不要远程|非远程|不考虑远程', constraint.text)):
        return evidence
    if evidence.verdict == 'unknown':
        return evidence if re.search(r'远程|线上|办公|到岗|坐班', evidence.quote) else Evidence(
            key=constraint.key, verdict='unknown', explanation='岗位未明确说明远程办公政策')
    explicit_conflict = re.search(r'(?:不支持|不接受|不允许|不可|不能|无法)(?:全程)?(?:远程|线上)'
        r'|现场办公|线下办公|坐班|到岗办公|必须到岗|后期线下|必须[^。\n]{0,8}(?:现场|线下)', evidence.quote)
    if explicit_conflict:
        return evidence.model_copy(update={'verdict':'violated'})
    if evidence.verdict == 'violated':
        return Evidence(key=constraint.key, verdict='unknown', explanation='城市和出勤不能证明必须现场办公')
    full = re.search(r'全程|完全|全远程|纯远程', constraint.text)
    supported = re.search(r'全程远程|完全远程|全远程|纯远程|全程线上|无需到岗', evidence.quote) if full else re.search(r'远程|线上', evidence.quote)
    return evidence if supported else Evidence(key=constraint.key, verdict='unknown',
        explanation='引用未明确支持所要求的远程办公范围')


def location_evidence(constraint: Constraint, job: Job, filter_city: str | None = None) -> Evidence | None:
    """Check an explicit job-location requirement, not the user's residence."""
    if constraint.kind != "hard" or not re.search(r"city|location|城市|地点|所在地", constraint.key, re.I):
        return None
    if re.search(r'远程|线上|线下|办公', constraint.text):
        return None  # Location/work-mode branches cannot be collapsed into one city.
    if re.search(r"或|、|/", constraint.text):
        return None  # Multi-city alternatives need semantic comparison, not one filter value.
    expected = filter_city if filter_city and filter_city in constraint.text else None
    if not expected:
        cities = [name for name in ("北京", "上海", "杭州", "深圳", "广州", "南京", "成都", "武汉",
                                   "西安", "苏州", "天津", "重庆") if name in constraint.text]
        if len(cities) != 1:
            return None
        expected = cities[0]
    if not job.city or re.search(r"全国|不限|远程|线上", job.city):
        return Evidence(key=constraint.key, verdict="unknown", explanation="缺少明确岗位所在地")
    actual = job.city.split("·", 1)[0]
    options = [v.strip().removesuffix("市") for v in re.split(r"[/、,，|]", actual)]
    return Evidence(key=constraint.key, verdict="satisfied" if expected.removesuffix("市") in options else "violated",
                    quote=job.city, explanation="比较岗位所在地；支持远程不改变所在地")


def is_output_policy(text: str) -> bool:
    return text.strip().rstrip("。；;") in {
        "不把缺失信息当满足", "不能把缺失信息当满足", "不把未知当满足", "未知不当事实",
        "未知不能当事实", "不要凑数", "不为凑数放宽条件", "不要为了凑数放宽条件",
    }
