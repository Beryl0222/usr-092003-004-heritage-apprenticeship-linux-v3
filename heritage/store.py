"""传承履历的内存数据存储与业务规则。

所有写操作集中在 HeritageStore 中完成，规则包括：
* 评审人资质（在考核当日有效、覆盖该标准版本）与利益冲突校验；
* 考核结论（含补考）只追加、不覆盖，资格可追溯到当时作品、评语与标准；
* 练习学时按（学徒、工序、日期）去重，跨店轮转不重复计算；
* 秘方材料按角色脱敏；
* 学校端仅输出聚合数据，小样本队列与个人敏感信息一律隐藏。
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import date


class LedgerError(Exception):
    """履历服务错误的基类。"""


class RuleViolation(LedgerError):
    """业务规则不满足，映射为 HTTP 400。"""


class NotFound(LedgerError):
    """引用的对象不存在，映射为 HTTP 404。"""


RESULT_PASS = "pass"
RESULT_CONDITIONAL = "conditional_pass"
RESULT_FAIL = "fail"

# 可查看秘方完整配料（含用量、手法）的角色。
SECRET_VIEWER_ROLES = {"brand_admin", "master"}

# 就业汇总中小样本队列的最小人数；低于该值不输出任何计数。
MIN_COHORT_SIZE = 3


def _today():
    return date.today().isoformat()


class HeritageStore:
    """线程安全的传承履历存储。"""

    def __init__(self):
        self._lock = threading.RLock()
        self.people = {}
        self.credentials = {}
        self.stores = {}
        self.crafts = {}
        self.craft_versions = defaultdict(dict)
        self.demonstrations = {}
        self.mentorships = []
        self.practices = {}
        self.attempts = []
        self.qualifications = []
        self.graduations = []
        self.employments = []

    # ------------------------------------------------------------------ 基础档案

    def register_person(self, person_id, name, roles, home_store_id=None):
        with self._lock:
            if person_id in self.people:
                raise RuleViolation(f"人员已存在：{person_id}")
            roles = set(roles)
            if home_store_id and home_store_id not in self.stores:
                raise NotFound(f"门店不存在：{home_store_id}")
            person = {
                "person_id": person_id,
                "name": name,
                "roles": sorted(roles),
                "home_store_id": home_store_id,
            }
            self.people[person_id] = person
            return dict(person)

    def add_credential(self, credential_id, assessor_id, craft_id,
                       valid_from, valid_until, qualified_versions=None,
                       status="active"):
        """登记评审资质。qualified_versions 为空表示覆盖该技艺全部版本。"""
        with self._lock:
            if assessor_id not in self.people:
                raise NotFound(f"评审人不存在：{assessor_id}")
            if "assessor" not in self.people[assessor_id]["roles"]:
                raise RuleViolation("该人员不具备评审人角色")
            if credential_id in self.credentials:
                raise RuleViolation(f"资质编号已存在：{credential_id}")
            cred = {
                "credential_id": credential_id,
                "assessor_id": assessor_id,
                "craft_id": craft_id,
                "valid_from": valid_from,
                "valid_until": valid_until,
                "qualified_versions": list(qualified_versions or []),
                "status": status,
            }
            self.credentials[credential_id] = cred
            return dict(cred)

    def register_store(self, store_id, name):
        with self._lock:
            if store_id in self.stores:
                raise RuleViolation(f"门店已存在：{store_id}")
            store = {"store_id": store_id, "name": name}
            self.stores[store_id] = store
            return dict(store)

    # ------------------------------------------------------------ 技艺版本与示范

    def register_craft(self, craft_id, name):
        with self._lock:
            if craft_id in self.crafts:
                raise RuleViolation(f"技艺已存在：{craft_id}")
            craft = {"craft_id": craft_id, "name": name}
            self.crafts[craft_id] = craft
            return dict(craft)

    def add_craft_version(self, craft_id, version_no, effective_date, creator, steps):
        """发布技艺标准版本。steps: [{step_id,name,key,required_hours}]。"""
        with self._lock:
            if craft_id not in self.crafts:
                raise NotFound(f"技艺不存在：{craft_id}")
            versions = self.craft_versions[craft_id]
            if version_no in versions:
                raise RuleViolation(f"技艺版本已存在：{craft_id}@{version_no}")
            step_ids = [s["step_id"] for s in steps]
            if len(step_ids) != len(set(step_ids)):
                raise RuleViolation("同一版本内工序编号不得重复")
            snapshot = {
                "craft_id": craft_id,
                "version_no": version_no,
                "effective_date": effective_date,
                "creator": creator,
                "steps": [dict(s) for s in steps],
            }
            versions[version_no] = snapshot
            return dict(snapshot)

    def current_version(self, craft_id, on_date=None):
        with self._lock:
            versions = self.craft_versions.get(craft_id)
            if not versions:
                raise NotFound(f"技艺没有任何标准版本：{craft_id}")
            on_date = on_date or _today()
            effective = [v for v in versions.values()
                         if v["effective_date"] <= on_date]
            if not effective:
                raise RuleViolation(f"技艺 {craft_id} 在 {on_date} 尚无生效标准")
            return max(effective, key=lambda v: v["effective_date"])

    def add_demonstration(self, demonstration_id, craft_id, step_id, version_no,
                          demonstrated_by, recorded_at, video_ref=None,
                          ingredients=None):
        """登记分步示范。ingredients: [{name,amount,secret}]，secret 材料按角色隐藏。"""
        with self._lock:
            if demonstration_id in self.demonstrations:
                raise RuleViolation(f"示范已存在：{demonstration_id}")
            version = self._require_version(craft_id, version_no)
            if not any(s["step_id"] == step_id for s in version["steps"]):
                raise RuleViolation("示范工序不属于该技艺版本")
            if demonstrated_by not in self.people:
                raise NotFound(f"示范师傅不存在：{demonstrated_by}")
            demo = {
                "demonstration_id": demonstration_id,
                "craft_id": craft_id,
                "step_id": step_id,
                "version_no": version_no,
                "demonstrated_by": demonstrated_by,
                "recorded_at": recorded_at,
                "video_ref": video_ref,
                "ingredients": [dict(i) for i in (ingredients or [])],
            }
            self.demonstrations[demonstration_id] = demo
            return self._redact_demo(dict(demo), None)

    def get_demonstration(self, demonstration_id, viewer_role=None):
        with self._lock:
            demo = self.demonstrations.get(demonstration_id)
            if not demo:
                raise NotFound(f"示范不存在：{demonstration_id}")
            return self._redact_demo(dict(demo), viewer_role)

    @staticmethod
    def _redact_demo(demo, viewer_role):
        """秘方材料对无权角色隐藏用量与操作，仅保留占位以维持工序结构。"""
        if viewer_role in SECRET_VIEWER_ROLES:
            demo["secret_redacted"] = False
            return demo
        safe = []
        for item in demo["ingredients"]:
            if item.get("secret"):
                safe.append({"name": "秘方材料", "amount": None,
                             "secret": True, "hidden": True})
            else:
                rewritten = dict(item)
                rewritten["hidden"] = False
                safe.append(rewritten)
        demo["ingredients"] = safe
        demo["secret_redacted"] = True
        return demo

    # ------------------------------------------------------------------ 师徒关系

    def add_mentorship(self, apprentice_id, mentor_id, craft_id, store_id,
                       start_date, end_date=None):
        with self._lock:
            apprentice = self._require_person(apprentice_id)
            mentor = self._require_person(mentor_id)
            if "master" not in mentor["roles"]:
                raise RuleViolation("带教方必须是在册传承人（师傅）")
            if craft_id not in self.crafts:
                raise NotFound(f"技艺不存在：{craft_id}")
            if store_id not in self.stores:
                raise NotFound(f"门店不存在：{store_id}")
            relation = {
                "apprentice_id": apprentice_id,
                "mentor_id": mentor_id,
                "craft_id": craft_id,
                "store_id": store_id,
                "start_date": start_date,
                "end_date": end_date,
            }
            self.mentorships.append(relation)
            return dict(relation)

    def _active_mentorship(self, apprentice_id, mentor_id, craft_id, on_date):
        for rel in self.mentorships:
            if (rel["apprentice_id"] == apprentice_id
                    and rel["mentor_id"] == mentor_id
                    and rel["craft_id"] == craft_id
                    and rel["start_date"] <= on_date
                    and (rel["end_date"] is None or rel["end_date"] >= on_date)):
                return rel
        return None

    # ---------------------------------------------------------------- 练习批次与学时

    def add_practice(self, practice_id, apprentice_id, craft_id, step_id,
                     store_id, sessions, works=None):
        """登记一个练习批次。

        同一 practice_id 重复提交按幂等处理，不重复累计学时；
        不同批次若在同一日期为同一学徒、同一工序记学时，即使跨门店也拒绝，
        从源头杜绝轮转重复计算。
        sessions: [{date,hours,mentor_id}]；works: [{work_id,name,produced_at}]
        """
        with self._lock:
            if practice_id in self.practices:
                existing = self.practices[practice_id]
                if (existing["apprentice_id"] == apprentice_id
                        and existing["craft_id"] == craft_id
                        and existing["step_id"] == step_id
                        and existing["store_id"] == store_id):
                    result = dict(existing)
                    result["deduped"] = True
                    return result
                raise RuleViolation("练习批次编号已用于其他练习记录")

            self._require_person(apprentice_id)
            if craft_id not in self.crafts:
                raise NotFound(f"技艺不存在：{craft_id}")
            if not any(step_id in [s["step_id"] for s in v["steps"]]
                       for v in self.craft_versions[craft_id].values()):
                raise RuleViolation(f"工序不属于该技艺：{step_id}")
            if store_id not in self.stores:
                raise NotFound(f"门店不存在：{store_id}")

            norm_sessions = []
            batch_dates = set()
            for sess in sessions:
                on_date, hours, mentor_id = sess["date"], sess["hours"], sess["mentor_id"]
                # 先做学时去重：无论轮转门店与带教师傅如何，同学徒同工序同日只计一次。
                if on_date in batch_dates:
                    raise RuleViolation(
                        f"批次 {practice_id} 在 {on_date} 为同一工序重复登记学时")
                batch_dates.add(on_date)
                for other in self.practices.values():
                    if (other["apprentice_id"] == apprentice_id
                            and other["step_id"] == step_id
                            and any(s["date"] == on_date for s in other["sessions"])):
                        raise RuleViolation(
                            f"{apprentice_id} 在 {on_date} 的 {step_id} 学时已由"
                            f"批次 {other['practice_id']}（门店 {other['store_id']}）"
                            "登记，跨店轮转不得重复计算")
                relation = self._active_mentorship(apprentice_id, mentor_id,
                                                   craft_id, on_date)
                if relation is None:
                    raise RuleViolation(
                        f"{on_date} 不存在有效的师徒关系：{apprentice_id}-{mentor_id}")
                if relation["store_id"] != store_id:
                    raise RuleViolation(
                        f"{on_date} 的带教师傅隶属其他门店，门店轮转记录不一致")
                norm_sessions.append({
                    "date": on_date,
                    "hours": float(hours),
                    "mentor_id": mentor_id,
                })

            work_rows = []
            for work in works or []:
                if any(w["work_id"] == work["work_id"]
                       for p in self.practices.values() for w in p["works"]):
                    raise RuleViolation(f"作品编号已存在：{work['work_id']}")
                work_rows.append({
                    "work_id": work["work_id"],
                    "name": work["name"],
                    "produced_at": work.get("produced_at"),
                    "apprentice_id": apprentice_id,
                    "craft_id": craft_id,
                    "step_id": step_id,
                    "store_id": store_id,
                })

            record = {
                "practice_id": practice_id,
                "apprentice_id": apprentice_id,
                "craft_id": craft_id,
                "step_id": step_id,
                "store_id": store_id,
                "sessions": norm_sessions,
                "works": work_rows,
            }
            self.practices[practice_id] = record
            result = dict(record)
            result["deduped"] = False
            return result

    def practice_hours(self, apprentice_id, craft_id=None, step_id=None):
        """汇总学时时每个日期的练习只计一次（批次内、批次间均去重）。"""
        with self._lock:
            seen = set()
            per_step = defaultdict(float)
            per_store = defaultdict(float)
            total = 0.0
            for record in self.practices.values():
                if record["apprentice_id"] != apprentice_id:
                    continue
                if craft_id and record["craft_id"] != craft_id:
                    continue
                if step_id and record["step_id"] != step_id:
                    continue
                for sess in record["sessions"]:
                    key = (record["craft_id"], record["step_id"], sess["date"])
                    if key in seen:
                        continue
                    seen.add(key)
                    total += sess["hours"]
                    per_step[(record["craft_id"], record["step_id"])] += sess["hours"]
                    per_store[record["store_id"]] += sess["hours"]
            return {
                "apprentice_id": apprentice_id,
                "total_hours": round(total, 2),
                "by_step": {f"{craft}::{step}": round(h, 2)
                            for (craft, step), h in sorted(per_step.items())},
                "by_store": {store: round(h, 2)
                             for store, h in sorted(per_store.items())},
            }

    # ------------------------------------------------------------------ 现场考核

    def sign_assessment(self, attempt_id, apprentice_id, craft_id, step_id,
                        standard_version, practice_ids, work_ids, result,
                        comments, assessor_id, assessed_on):
        """签署一次现场考核。资质失效或存在利益冲突一律拒绝。"""
        with self._lock:
            if attempt_id in {a["attempt_id"] for a in self.attempts}:
                raise RuleViolation(f"考核记录编号已存在：{attempt_id}")
            if result not in {RESULT_PASS, RESULT_CONDITIONAL, RESULT_FAIL}:
                raise RuleViolation(f"未知考核结论：{result}")
            self._require_person(apprentice_id)
            assessor = self._require_person(assessor_id)
            if "assessor" not in assessor["roles"]:
                raise RuleViolation("签署人不具备评审人角色")
            version = self._require_version(craft_id, standard_version)
            step_snapshot = next((s for s in version["steps"]
                                  if s["step_id"] == step_id), None)
            if step_snapshot is None:
                raise RuleViolation("考核工序不属于所采用的标准版本")

            self._assert_credential_valid(assessor_id, craft_id,
                                          standard_version, assessed_on)
            self._assert_no_conflict(apprentice_id, craft_id, assessor_id,
                                     practice_ids)

            evidence = []
            evidence_stores = set()
            work_seen = set()
            for pid in practice_ids:
                record = self.practices.get(pid)
                if record is None:
                    raise NotFound(f"练习批次不存在：{pid}")
                if record["apprentice_id"] != apprentice_id:
                    raise RuleViolation(f"练习批次不属于该学徒：{pid}")
                if record["craft_id"] != craft_id or record["step_id"] != step_id:
                    raise RuleViolation(f"练习批次与考核工序不符：{pid}")
                evidence.append(pid)
                evidence_stores.add(record["store_id"])
            for wid in work_ids or []:
                owners = [w for p in self.practices.values() for w in p["works"]
                          if w["work_id"] == wid]
                if not owners:
                    raise NotFound(f"考核作品不存在：{wid}")
                work = owners[0]
                if work["apprentice_id"] != apprentice_id:
                    raise RuleViolation(f"作品不属于该学徒：{wid}")
                if work["craft_id"] != craft_id or work["step_id"] != step_id:
                    raise RuleViolation(f"作品与考核工序不符：{wid}")
                work_seen.add(wid)

            # 关键工序必须有足够练习学时才能签署阶段能力。
            hours = self.practice_hours(apprentice_id, craft_id, step_id)
            if hours["total_hours"] < float(step_snapshot["required_hours"]):
                raise RuleViolation(
                    f"练习学时不足：{hours['total_hours']} < "
                    f"{step_snapshot['required_hours']}，不得签署 {step_id}")

            attempt = {
                "attempt_id": attempt_id,
                "apprentice_id": apprentice_id,
                "craft_id": craft_id,
                "step_id": step_id,
                "standard_version": standard_version,
                "standard_snapshot": {
                    "version_no": version["version_no"],
                    "effective_date": version["effective_date"],
                    "step": dict(step_snapshot),
                },
                "practice_ids": evidence,
                "evidence_store_ids": sorted(evidence_stores),
                "work_ids": sorted(work_seen),
                "result": result,
                "comments": comments,
                "assessor_id": assessor_id,
                "assessed_on": assessed_on,
                "sequence": len(self.attempts) + 1,
            }
            self.attempts.append(attempt)

            qualification = None
            if result in {RESULT_PASS, RESULT_CONDITIONAL}:
                # 旧资格不删除、不覆盖；新结论产生新的资格版本。
                for old in self.qualifications:
                    if (old["apprentice_id"] == apprentice_id
                            and old["step_id"] == step_id
                            and old["active"]):
                        old["active"] = False
                        old["superseded_by_attempt"] = attempt_id
                level = ("independent" if result == RESULT_PASS
                         else "supervised")
                qualification = {
                    "qualification_id": f"QUAL-{len(self.qualifications) + 1:04d}",
                    "apprentice_id": apprentice_id,
                    "craft_id": craft_id,
                    "step_id": step_id,
                    "level": level,
                    "standard_version": standard_version,
                    "source_attempt_id": attempt_id,
                    "granted_on": assessed_on,
                    "active": True,
                    "superseded_by_attempt": None,
                }
                self.qualifications.append(qualification)
            else:
                # 再考核失败：停用既有资格，上岗核对不再放行；历史资格仍可追溯。
                for old in self.qualifications:
                    if (old["apprentice_id"] == apprentice_id
                            and old["step_id"] == step_id
                            and old["active"]):
                        old["active"] = False
                        old["revoked_by_attempt"] = attempt_id

            return {"attempt": dict(attempt),
                    "qualification": dict(qualification) if qualification else None}

    def _assert_credential_valid(self, assessor_id, craft_id, version_no, on_date):
        candidates = [c for c in self.credentials.values()
                      if c["assessor_id"] == assessor_id
                      and c["craft_id"] == craft_id
                      and c["status"] == "active"
                      and c["valid_from"] <= on_date <= c["valid_until"]
                      and (not c["qualified_versions"]
                           or version_no in c["qualified_versions"])]
        if not candidates:
            raise RuleViolation(
                f"评审人 {assessor_id} 在 {on_date} 对 {craft_id}@{version_no} "
                "无有效资质（已过期、被停用或未覆盖该标准版本）")

    def _assert_no_conflict(self, apprentice_id, craft_id, assessor_id,
                            practice_ids):
        for rel in self.mentorships:
            if (rel["apprentice_id"] == apprentice_id
                    and rel["craft_id"] == craft_id
                    and rel["mentor_id"] == assessor_id):
                raise RuleViolation(
                    f"评审人 {assessor_id} 曾带教该学徒，存在师徒利益冲突，须回避")
        assessor = self.people[assessor_id]
        home = assessor.get("home_store_id")
        evidence_stores = {self.practices[pid]["store_id"] for pid in practice_ids}
        if home and home in evidence_stores:
            raise RuleViolation(
                f"评审人隶属门店 {home}，考核证据来自同一门店，存在利益冲突，须回避")

    # ------------------------------------------------------------ 毕业上岗与追溯

    def readiness_report(self, apprentice_id, craft_id, on_date=None):
        """门店核对：每个关键工序可否独立操作、仍需监督还是尚未达标。"""
        with self._lock:
            self._require_person(apprentice_id)
            version = self.current_version(craft_id, on_date)
            attempts_by_step = defaultdict(list)
            for attempt in self.attempts:
                if (attempt["apprentice_id"] == apprentice_id
                        and attempt["craft_id"] == craft_id):
                    attempts_by_step[attempt["step_id"]].append(attempt)
            quals = {(q["step_id"], q["level"]): q
                     for q in self.qualifications
                     if q["apprentice_id"] == apprentice_id
                     and q["craft_id"] == craft_id and q["active"]}

            steps = []
            for step in version["steps"]:
                history = sorted(attempts_by_step.get(step["step_id"], []),
                                 key=lambda a: a["sequence"])
                hours = self.practice_hours(
                    apprentice_id, craft_id, step["step_id"])["total_hours"]
                latest = history[-1] if history else None
                independent = quals.get((step["step_id"], "independent"))
                supervised = quals.get((step["step_id"], "supervised"))
                if latest and latest["result"] == RESULT_PASS and independent:
                    status = "independent"
                    qual_id = independent["qualification_id"]
                elif latest and latest["result"] == RESULT_CONDITIONAL and supervised:
                    status = "supervised"
                    qual_id = supervised["qualification_id"]
                elif history:
                    status = "not_ready"
                    qual_id = None
                else:
                    status = "not_started"
                    qual_id = None
                steps.append({
                    "step_id": step["step_id"],
                    "name": step["name"],
                    "key": step.get("key", False),
                    "required_hours": step["required_hours"],
                    "practiced_hours": hours,
                    "assessment_count": len(history),
                    "latest_result": latest["result"] if latest else None,
                    "status": status,
                    "qualification_id": qual_id,
                })
            return {
                "apprentice_id": apprentice_id,
                "craft_id": craft_id,
                "standard_version": version["version_no"],
                "independent_steps": [s["step_id"] for s in steps
                                      if s["status"] == "independent"],
                "supervised_steps": [s["step_id"] for s in steps
                                     if s["status"] == "supervised"],
                "steps": steps,
            }

    def trace_qualification(self, qualification_id):
        """从一项资格追溯作品、全部评语（含历次补考）与当时采用的标准。"""
        with self._lock:
            qual = next((q for q in self.qualifications
                         if q["qualification_id"] == qualification_id), None)
            if qual is None:
                raise NotFound(f"资格不存在：{qualification_id}")
            history = []
            for attempt in self.attempts:
                if (attempt["apprentice_id"] == qual["apprentice_id"]
                        and attempt["craft_id"] == qual["craft_id"]
                        and attempt["step_id"] == qual["step_id"]):
                    works = [w for p in self.practices.values()
                             for w in p["works"]
                             if w["work_id"] in attempt["work_ids"]]
                    history.append({
                        "attempt_id": attempt["attempt_id"],
                        "sequence": attempt["sequence"],
                        "result": attempt["result"],
                        "comments": attempt["comments"],
                        "assessor_id": attempt["assessor_id"],
                        "assessed_on": attempt["assessed_on"],
                        "standard_version": attempt["standard_version"],
                        "is_source": attempt["attempt_id"] == qual["source_attempt_id"],
                        "practice_ids": attempt["practice_ids"],
                        "works": [{k: w[k] for k in
                                   ("work_id", "name", "produced_at", "store_id")}
                                  for w in works],
                    })
            history.sort(key=lambda h: h["sequence"])
            return {
                "qualification": dict(qual),
                "standard_at_issue": next(
                    a["standard_snapshot"] for a in self.attempts
                    if a["attempt_id"] == qual["source_attempt_id"]),
                "assessment_history": history,
            }

    def record_graduation(self, apprentice_id, craft_id, graduation_date,
                          graduation_year):
        with self._lock:
            self._require_person(apprentice_id)
            event = {
                "apprentice_id": apprentice_id,
                "craft_id": craft_id,
                "graduation_date": graduation_date,
                "graduation_year": graduation_year,
            }
            self.graduations.append(event)
            return dict(event)

    def record_employment(self, apprentice_id, store_id, hired_date, position):
        with self._lock:
            if store_id not in self.stores:
                raise NotFound(f"就业门店不存在：{store_id}")
            event = {
                "apprentice_id": apprentice_id,
                "store_id": store_id,
                "hired_date": hired_date,
                "position": position,
            }
            self.employments.append(event)
            return dict(event)

    def employment_summary(self, graduation_year=None):
        """学校汇总就业与培养成效：只出聚合数，小样本隐去，不含配方与个人信息。"""
        with self._lock:
            graduates = [g for g in self.graduations
                         if graduation_year is None
                         or g["graduation_year"] == graduation_year]
            employed_ids = {e["apprentice_id"] for e in self.employments}
            store_of = {e["apprentice_id"]: e["store_id"]
                        for e in self.employments}

            grouped = defaultdict(list)
            for grad in graduates:
                grouped[grad["craft_id"]].append(grad["apprentice_id"])

            crafts_report = []
            for craft_id, members in sorted(grouped.items()):
                unique_members = sorted(set(members))
                employed = [m for m in unique_members if m in employed_ids]
                base = {
                    "craft_id": craft_id,
                    "graduates": None,
                    "employed": None,
                    "employment_rate": None,
                    "by_store": None,
                }
                if len(unique_members) < MIN_COHORT_SIZE:
                    base["suppressed_reason"] = "cohort_smaller_than_3"
                    crafts_report.append(base)
                    continue
                store_counts = defaultdict(int)
                for member in employed:
                    store_counts[store_of[member]] += 1
                by_store = {}
                for store_id, count in sorted(store_counts.items()):
                    # 单店人数过少可能反向识别到个人，整体隐去该店计数。
                    by_store[store_id] = (count if count >= MIN_COHORT_SIZE
                                          else None)
                base.update({
                    "graduates": len(unique_members),
                    "employed": len(employed),
                    "employment_rate": round(len(employed)
                                             / len(unique_members), 4),
                    "by_store": by_store,
                })
                crafts_report.append(base)
            return {
                "graduation_year": graduation_year,
                "minimum_cohort_size": MIN_COHORT_SIZE,
                "contains_personal_data": False,
                "contains_commercial_formula": False,
                "crafts": crafts_report,
            }

    # ------------------------------------------------------------------ 内部工具

    def _require_person(self, person_id):
        person = self.people.get(person_id)
        if person is None:
            raise NotFound(f"人员不存在：{person_id}")
        return person

    def _require_version(self, craft_id, version_no):
        if craft_id not in self.crafts:
            raise NotFound(f"技艺不存在：{craft_id}")
        version = self.craft_versions[craft_id].get(version_no)
        if version is None:
            raise NotFound(f"技艺版本不存在：{craft_id}@{version_no}")
        return version
