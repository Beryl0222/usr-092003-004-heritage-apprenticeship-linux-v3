"""老字号传承履历的领域模型与核心规则。

品牌与学校共同维护学徒的传承履历，把技艺版本、分步示范、练习批次、
原料责任、师徒关系与现场考核串联起来，并保证：

* 只有资质有效且不存在利益冲突的评审人才能签署阶段能力；
* 补考只追加新结论，历史评价不被覆盖；
* 跨店轮转的学时按时间区间合并，不重复计算；
* 涉及秘方的原料按角色隐藏；
* 资格可追溯到作品、评语与当时采用的技艺标准；
* 学校汇总只看聚合数据，不接触商业配方与个人敏感信息。
"""

from dataclasses import dataclass, field
from datetime import date


class DomainError(Exception):
    """业务规则校验失败。"""


ROLE_APPRENTICE = "apprentice"
ROLE_MASTER = "master"

# 涉及秘方的原料仅对品牌方与师傅可见，其余角色一律隐藏。
SECRET_VISIBLE_ROLES = {"brand_admin", "master"}

DECISIONS = ("pass", "fail")
STAGE_LEVELS = ("independent", "supervised")


def _to_date(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _to_minutes(value):
    """把 "HH:MM" 或分钟数统一成当天分钟数，便于区间合并。"""
    if isinstance(value, (int, float)):
        return int(value)
    hours, _, minutes = str(value).partition(":")
    return int(hours) * 60 + int(minutes)


def _merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


@dataclass
class Person:
    id: str
    name: str
    role: str
    id_number: str = ""  # 个人敏感信息，不出现在学校汇总中
    phone: str = ""


@dataclass
class Reviewer:
    id: str
    person_id: str
    certificate: str
    valid_until: str  # 评审资质有效期（ISO 日期）
    store_id: str = ""  # 评审人所属门店，用于利益冲突判定


@dataclass
class TechniqueVersion:
    id: str
    technique: str
    version: str
    steps: list  # 分步示范：[{"id": ..., "name": ..., "demo": ...}]
    standards: dict  # 每道工序当时采用的考核标准：step_id -> 标准描述
    effective_from: str


@dataclass
class Material:
    id: str
    name: str
    lot_no: str
    responsible: str  # 原料责任方
    secret: bool = False  # 是否涉及秘方


@dataclass
class Relation:
    master_id: str
    apprentice_id: str
    store_id: str
    start: str
    end: str = ""


@dataclass
class PracticeBatch:
    id: str
    apprentice_id: str
    step_id: str
    store_id: str
    master_id: str
    date: str  # ISO 日期
    start: str  # "HH:MM"
    end: str
    material_ids: list = field(default_factory=list)
    output: str = ""  # 练习作品说明


@dataclass
class Assessment:
    id: str
    apprentice_id: str
    step_id: str
    technique_version_id: str
    store_id: str
    reviewer_id: str
    decision: str  # pass / fail
    level: str  # pass 时为 independent / supervised，fail 时为空
    comment: str  # 现场评语
    work_batch_ids: list  # 所依据的作品（练习批次）
    assessed_on: str


@dataclass
class Employment:
    apprentice_id: str
    store_id: str
    position: str
    started_on: str


class HeritageStore:
    """传承履历的数据与规则入口。考核记录只增不改。"""

    def __init__(self):
        self.persons = {}
        self.reviewers = {}
        self.technique_versions = {}
        self.materials = {}
        self.relations = []
        self.batches = {}
        self.assessments = []
        self.employments = []

    # ---- 基础档案 ----

    def add_person(self, person):
        if person.id in self.persons:
            raise DomainError(f"人员已存在：{person.id}")
        self.persons[person.id] = person
        return person

    def add_reviewer(self, reviewer):
        if reviewer.person_id not in self.persons:
            raise DomainError(f"评审人对应的人员不存在：{reviewer.person_id}")
        self.reviewers[reviewer.id] = reviewer
        return reviewer

    def add_technique_version(self, version):
        if version.id in self.technique_versions:
            raise DomainError(f"技艺版本已存在：{version.id}")
        step_ids = [step["id"] for step in version.steps]
        if not step_ids:
            raise DomainError("技艺版本至少包含一道工序")
        missing = [sid for sid in step_ids if sid not in version.standards]
        if missing:
            raise DomainError(f"工序缺少考核标准：{', '.join(missing)}")
        self.technique_versions[version.id] = version
        return version

    def add_material(self, material):
        self.materials[material.id] = material
        return material

    def add_relation(self, relation):
        master = self.persons.get(relation.master_id)
        apprentice = self.persons.get(relation.apprentice_id)
        if master is None or master.role != ROLE_MASTER:
            raise DomainError("师徒关系中的师傅不存在或角色不符")
        if apprentice is None or apprentice.role != ROLE_APPRENTICE:
            raise DomainError("师徒关系中的学徒不存在或角色不符")
        self.relations.append(relation)
        return relation

    def add_employment(self, employment):
        if employment.apprentice_id not in self.persons:
            raise DomainError(f"学徒不存在：{employment.apprentice_id}")
        self.employments.append(employment)
        return employment

    # ---- 练习批次与学时 ----

    def record_batch(self, batch):
        apprentice = self.persons.get(batch.apprentice_id)
        if apprentice is None or apprentice.role != ROLE_APPRENTICE:
            raise DomainError("练习批次的学徒不存在或角色不符")
        if batch.master_id not in self.persons:
            raise DomainError(f"带教师傅不存在：{batch.master_id}")
        if not any(batch.step_id in {s["id"] for s in v.steps} for v in self.technique_versions.values()):
            raise DomainError(f"工序不属于任何技艺版本：{batch.step_id}")
        for material_id in batch.material_ids:
            if material_id not in self.materials:
                raise DomainError(f"原料不存在：{material_id}")
        if _to_minutes(batch.end) <= _to_minutes(batch.start):
            raise DomainError("练习批次的结束时间必须晚于开始时间")
        if batch.id in self.batches:
            raise DomainError(f"练习批次已存在：{batch.id}")
        self.batches[batch.id] = batch
        return batch

    def effective_hours(self, apprentice_id, on_date=None):
        """跨店轮转按天合并时间区间，重叠时段只计一次。"""
        per_day = {}
        for batch in self.batches.values():
            if batch.apprentice_id != apprentice_id:
                continue
            if on_date and batch.date != on_date:
                continue
            per_day.setdefault(batch.date, []).append(
                (_to_minutes(batch.start), _to_minutes(batch.end))
            )
        total_minutes = 0
        for intervals in per_day.values():
            for start, end in _merge_intervals(intervals):
                total_minutes += end - start
        return total_minutes / 60

    # ---- 现场考核（只增不改） ----

    def sign_assessment(self, assessment):
        reviewer = self.reviewers.get(assessment.reviewer_id)
        if reviewer is None:
            raise DomainError(f"评审人不存在：{assessment.reviewer_id}")
        if _to_date(reviewer.valid_until) < _to_date(assessment.assessed_on):
            raise DomainError("评审人资质已失效，不能签署阶段能力")
        for relation in self.relations:
            if (
                relation.master_id == reviewer.person_id
                and relation.apprentice_id == assessment.apprentice_id
            ):
                raise DomainError("评审人与学徒存在师徒关系，利益冲突")
        if reviewer.store_id and reviewer.store_id == assessment.store_id:
            raise DomainError("评审人隶属考核门店，利益冲突")
        version = self.technique_versions.get(assessment.technique_version_id)
        if version is None:
            raise DomainError(f"技艺版本不存在：{assessment.technique_version_id}")
        if assessment.step_id not in {s["id"] for s in version.steps}:
            raise DomainError("考核工序不属于所采用的技艺版本")
        if assessment.decision not in DECISIONS:
            raise DomainError(f"考核结论无效：{assessment.decision}")
        if assessment.decision == "pass" and assessment.level not in STAGE_LEVELS:
            raise DomainError("通过考核必须给出 independent 或 supervised 的阶段能力")
        if assessment.decision == "fail" and assessment.level:
            raise DomainError("未通过的考核不应给出阶段能力")
        for batch_id in assessment.work_batch_ids:
            batch = self.batches.get(batch_id)
            if (
                batch is None
                or batch.apprentice_id != assessment.apprentice_id
                or batch.step_id != assessment.step_id
            ):
                raise DomainError(f"作品批次与本次考核不匹配：{batch_id}")
        if any(a.id == assessment.id for a in self.assessments):
            raise DomainError(f"考核记录已存在：{assessment.id}")
        # 补考只追加新记录，历史评价永不覆盖。
        self.assessments.append(assessment)
        return assessment

    def assessment_history(self, apprentice_id, step_id):
        return [
            a
            for a in self.assessments
            if a.apprentice_id == apprentice_id and a.step_id == step_id
        ]

    def latest_assessment(self, apprentice_id, step_id):
        history = list(enumerate(self.assessment_history(apprentice_id, step_id)))
        if not history:
            return None
        return max(history, key=lambda pair: (_to_date(pair[1].assessed_on), pair[0]))[1]

    # ---- 上岗核对与追溯 ----

    def clearance(self, apprentice_id, technique_version_id=None):
        """门店核对：每道工序可独立操作、仍需监督或尚未达标。"""
        if technique_version_id is None:
            version = None
            if self.technique_versions:
                version = max(
                    self.technique_versions.values(),
                    key=lambda v: _to_date(v.effective_from),
                )
        else:
            version = self.technique_versions.get(technique_version_id)
            if version is None:
                raise KeyError(technique_version_id)
        steps = {}
        if version is not None:
            for step in version.steps:
                latest = self.latest_assessment(apprentice_id, step["id"])
                if latest is None:
                    status = "not_assessed"
                elif latest.decision == "fail":
                    status = "not_qualified"
                else:
                    status = latest.level
                steps[step["id"]] = {
                    "name": step["name"],
                    "status": status,
                    "assessment_id": latest.id if latest else None,
                }
        return {
            "apprentice_id": apprentice_id,
            "technique_version_id": version.id if version else None,
            "steps": steps,
            "independent": [sid for sid, info in steps.items() if info["status"] == "independent"],
            "supervised": [sid for sid, info in steps.items() if info["status"] == "supervised"],
        }

    def trace(self, assessment_id):
        """从一项资格追溯到作品、评语与当时采用的技艺标准。"""
        assessment = next((a for a in self.assessments if a.id == assessment_id), None)
        if assessment is None:
            raise KeyError(assessment_id)
        version = self.technique_versions[assessment.technique_version_id]
        works = [self.batches[batch_id] for batch_id in assessment.work_batch_ids]
        return {
            "assessment": assessment,
            "works": works,
            "comment": assessment.comment,
            "standards": dict(version.standards),
            "technique_version": version,
        }

    # ---- 视图：按角色隐藏秘方，汇总不含敏感信息 ----

    def visible_materials(self, role):
        view = []
        for material in self.materials.values():
            if material.secret and role not in SECRET_VISIBLE_ROLES:
                view.append({"id": material.id, "name": "保密配料", "secret": True})
            else:
                view.append(
                    {
                        "id": material.id,
                        "name": material.name,
                        "lot_no": material.lot_no,
                        "responsible": material.responsible,
                        "secret": material.secret,
                    }
                )
        return view

    def school_summary(self):
        """学校汇总就业与培养成效：只有聚合数字，不含配方与个人信息。"""
        apprentices = [p for p in self.persons.values() if p.role == ROLE_APPRENTICE]
        apprentice_ids = {p.id for p in apprentices}
        employed = {e.apprentice_id for e in self.employments} & apprentice_ids
        independent = supervised = 0
        for person in apprentices:
            for info in self.clearance(person.id)["steps"].values():
                if info["status"] == "independent":
                    independent += 1
                elif info["status"] == "supervised":
                    supervised += 1
        return {
            "apprentices": len(apprentices),
            "employed": len(employed),
            "assessments_total": len(self.assessments),
            "independent_steps": independent,
            "supervised_steps": supervised,
        }
