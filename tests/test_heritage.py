"""验证传承履历的核心领域规则。"""

import json
import unittest

from heritage import (
    Assessment,
    DomainError,
    Employment,
    HeritageStore,
    Material,
    Person,
    PracticeBatch,
    Relation,
    Reviewer,
    TechniqueVersion,
)


def build_store():
    store = HeritageStore()
    store.add_person(Person(id="m1", name="王师傅", role="master", id_number="110101195801010011"))
    store.add_person(Person(id="a1", name="小李", role="apprentice", id_number="110101200401010022"))
    store.add_person(Person(id="r1", name="评审甲", role="reviewer"))
    store.add_person(Person(id="r2", name="评审乙", role="reviewer"))
    store.add_reviewer(
        Reviewer(id="rev1", person_id="r1", certificate="CERT-1", valid_until="2027-01-01", store_id="s9")
    )
    store.add_reviewer(
        Reviewer(id="rev_expired", person_id="r2", certificate="CERT-2", valid_until="2020-01-01")
    )
    store.add_reviewer(
        Reviewer(id="rev_master", person_id="m1", certificate="CERT-3", valid_until="2027-01-01")
    )
    store.add_technique_version(
        TechniqueVersion(
            id="tv1",
            technique="烧麦",
            version="v1",
            steps=[
                {"id": "st1", "name": "擀皮", "demo": "走槌擀出荷叶边"},
                {"id": "st2", "name": "包馅", "demo": "提褶收口留石榴嘴"},
            ],
            standards={"st1": "皮边薄如纸、直径九厘米", "st2": "十八道褶、收口不散"},
            effective_from="2025-01-01",
        )
    )
    store.add_material(Material(id="mat1", name="羊后腿肉", lot_no="L1", responsible="门店A"))
    store.add_material(Material(id="mat2", name="秘制香料", lot_no="L2", responsible="品牌总部", secret=True))
    store.add_relation(Relation(master_id="m1", apprentice_id="a1", store_id="s1", start="2026-01-01"))
    return store


def make_assessment(**overrides):
    data = {
        "id": "as1",
        "apprentice_id": "a1",
        "step_id": "st1",
        "technique_version_id": "tv1",
        "store_id": "s1",
        "reviewer_id": "rev1",
        "decision": "pass",
        "level": "independent",
        "comment": "擀皮均匀，荷叶边成型稳定",
        "work_batch_ids": [],
        "assessed_on": "2026-06-01",
    }
    data.update(overrides)
    return Assessment(**data)


class MaterialVisibilityTest(unittest.TestCase):
    def setUp(self):
        self.store = build_store()

    def test_secret_material_hidden_for_school(self):
        view = {m["id"]: m for m in self.store.visible_materials("school_admin")}
        self.assertEqual(view["mat2"], {"id": "mat2", "name": "保密配料", "secret": True})
        self.assertNotIn("lot_no", view["mat2"])
        self.assertEqual(view["mat1"]["name"], "羊后腿肉")

    def test_secret_material_visible_for_brand_and_master(self):
        for role in ("brand_admin", "master"):
            view = {m["id"]: m for m in self.store.visible_materials(role)}
            self.assertEqual(view["mat2"]["name"], "秘制香料")
            self.assertEqual(view["mat2"]["responsible"], "品牌总部")


class AssessmentSigningTest(unittest.TestCase):
    def setUp(self):
        self.store = build_store()

    def test_expired_reviewer_rejected(self):
        with self.assertRaisesRegex(DomainError, "资质已失效"):
            self.store.sign_assessment(make_assessment(reviewer_id="rev_expired"))

    def test_master_reviewer_has_conflict_of_interest(self):
        with self.assertRaisesRegex(DomainError, "利益冲突"):
            self.store.sign_assessment(make_assessment(reviewer_id="rev_master"))

    def test_same_store_reviewer_has_conflict_of_interest(self):
        with self.assertRaisesRegex(DomainError, "利益冲突"):
            self.store.sign_assessment(make_assessment(store_id="s9"))

    def test_reexam_appends_without_overwriting(self):
        first = make_assessment(
            id="as1", decision="fail", level="", comment="褶数不足", assessed_on="2026-05-01"
        )
        second = make_assessment(
            id="as2", decision="pass", level="supervised", comment="补考达标", assessed_on="2026-06-01"
        )
        self.store.sign_assessment(first)
        self.store.sign_assessment(second)
        history = self.store.assessment_history("a1", "st1")
        self.assertEqual([a.id for a in history], ["as1", "as2"])
        self.assertEqual(history[0].decision, "fail")
        self.assertEqual(history[0].comment, "褶数不足")
        self.assertEqual(self.store.latest_assessment("a1", "st1").id, "as2")

    def test_fail_assessment_must_not_carry_level(self):
        with self.assertRaises(DomainError):
            self.store.sign_assessment(make_assessment(decision="fail", level="independent"))

    def test_work_batches_must_match_apprentice_and_step(self):
        self.store.record_batch(
            PracticeBatch(
                id="b1", apprentice_id="a1", step_id="st2", store_id="s1", master_id="m1",
                date="2026-05-01", start="08:00", end="09:00",
            )
        )
        with self.assertRaisesRegex(DomainError, "不匹配"):
            self.store.sign_assessment(make_assessment(work_batch_ids=["b1"]))


class PracticeHoursTest(unittest.TestCase):
    def setUp(self):
        self.store = build_store()

    def add_batch(self, batch_id, store_id, start, end, date="2026-06-10"):
        self.store.record_batch(
            PracticeBatch(
                id=batch_id, apprentice_id="a1", step_id="st1", store_id=store_id,
                master_id="m1", date=date, start=start, end=end,
            )
        )

    def test_cross_store_overlap_counts_once(self):
        self.add_batch("b1", "s1", "08:00", "10:00")
        self.add_batch("b2", "s2", "09:00", "11:00")
        self.assertEqual(self.store.effective_hours("a1", "2026-06-10"), 3.0)

    def test_disjoint_batches_accumulate(self):
        self.add_batch("b1", "s1", "08:00", "10:00")
        self.add_batch("b2", "s2", "14:00", "16:30")
        self.assertEqual(self.store.effective_hours("a1"), 4.5)

    def test_invalid_time_range_rejected(self):
        with self.assertRaises(DomainError):
            self.add_batch("b1", "s1", "10:00", "09:00")


class ClearanceAndTraceTest(unittest.TestCase):
    def setUp(self):
        self.store = build_store()
        self.store.record_batch(
            PracticeBatch(
                id="b1", apprentice_id="a1", step_id="st1", store_id="s1", master_id="m1",
                date="2026-05-20", start="08:00", end="10:00",
                material_ids=["mat1", "mat2"], output="烧麦皮四十张",
            )
        )
        self.store.sign_assessment(
            make_assessment(id="as1", step_id="st1", level="independent", work_batch_ids=["b1"])
        )
        self.store.sign_assessment(
            make_assessment(id="as2", step_id="st2", level="supervised", comment="收口偶有散开")
        )

    def test_clearance_lists_independent_and_supervised_steps(self):
        clearance = self.store.clearance("a1")
        self.assertEqual(clearance["independent"], ["st1"])
        self.assertEqual(clearance["supervised"], ["st2"])
        self.assertEqual(clearance["steps"]["st1"]["status"], "independent")
        self.assertEqual(clearance["steps"]["st2"]["status"], "supervised")

    def test_trace_recovers_works_comment_and_standards_at_the_time(self):
        self.store.add_technique_version(
            TechniqueVersion(
                id="tv2", technique="烧麦", version="v2",
                steps=[
                    {"id": "st1", "name": "擀皮"},
                    {"id": "st2", "name": "包馅"},
                ],
                standards={"st1": "新标准：直径十厘米", "st2": "新标准：二十道褶"},
                effective_from="2026-07-01",
            )
        )
        trace = self.store.trace("as1")
        self.assertEqual(trace["comment"], "擀皮均匀，荷叶边成型稳定")
        self.assertEqual([w.id for w in trace["works"]], ["b1"])
        self.assertEqual(trace["works"][0].output, "烧麦皮四十张")
        self.assertEqual(trace["standards"]["st1"], "皮边薄如纸、直径九厘米")
        self.assertEqual(trace["technique_version"].id, "tv1")


class SchoolSummaryTest(unittest.TestCase):
    def test_summary_is_aggregate_only(self):
        store = build_store()
        store.sign_assessment(make_assessment(id="as1"))
        store.add_employment(
            Employment(apprentice_id="a1", store_id="s1", position="面点工", started_on="2026-08-01")
        )
        summary = store.school_summary()
        self.assertEqual(
            summary,
            {
                "apprentices": 1,
                "employed": 1,
                "assessments_total": 1,
                "independent_steps": 1,
                "supervised_steps": 0,
            },
        )
        payload = json.dumps(summary, ensure_ascii=False)
        for leaked in ("小李", "110101200401010022", "秘制香料", "品牌总部"):
            self.assertNotIn(leaked, payload)


if __name__ == "__main__":
    unittest.main()
