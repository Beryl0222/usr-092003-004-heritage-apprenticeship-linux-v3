"""传承履历业务规则测试：资质、冲突、补考留存、学时去重、脱敏与追溯。"""

import json
import unittest

from heritage import HeritageStore, RuleViolation, NotFound


CRAFT = "shaomai"
V1 = "v1"
S1, S2, S3 = "pi", "bao", "zheng"  # 擀皮 / 包制 / 蒸制
STUDENT = "stu-li"
WANG = "m-wang"      # 本店师傅（同时持评审资质，用于验证回避规则）
ZHAO = "m-zhao"      # 轮转店师傅
SUN = "a-sun"        # 独立评审
LI = "a-li"          # 与本店同店的评审
ZHOU = "a-zhou"      # 仅持过期/不覆盖版本资质的评审


def build_world():
    store = HeritageStore()
    store.register_store("shop-a", "东城门市部")
    store.register_store("shop-b", "西城门市部")
    store.register_person(STUDENT, "小李", ["apprentice"])
    store.register_person(WANG, "王师傅", ["master", "assessor"],
                          home_store_id="shop-a")
    store.register_person(ZHAO, "赵师傅", ["master"], home_store_id="shop-b")
    store.register_person(SUN, "孙评审", ["assessor"])
    store.register_person(LI, "李评审", ["assessor"], home_store_id="shop-a")
    store.register_person(ZHOU, "周评审", ["assessor"])
    store.register_craft(CRAFT, "烧麦")
    store.add_craft_version(
        CRAFT, V1, "2025-01-01", WANG,
        [
            {"step_id": S1, "name": "擀皮", "key": True, "required_hours": 10},
            {"step_id": S2, "name": "包制", "key": True, "required_hours": 20},
            {"step_id": S3, "name": "蒸制", "key": False, "required_hours": 6},
        ],
    )
    # 两段师徒关系长期有效，模拟跨店轮转。
    store.add_mentorship(STUDENT, WANG, CRAFT, "shop-a", "2026-03-01")
    store.add_mentorship(STUDENT, ZHAO, CRAFT, "shop-b", "2026-06-01")
    store.add_credential("cred-sun", SUN, CRAFT, "2026-01-01", "2026-12-31",
                         qualified_versions=[V1])
    return store


class CredentialRuleTest(unittest.TestCase):
    def setUp(self):
        self.store = build_world()

    def test_non_assessor_cannot_hold_credential(self):
        with self.assertRaises(RuleViolation):
            self.store.add_credential(
                "bad", STUDENT, CRAFT, "2026-01-01", "2026-12-31")

    def test_expired_or_uncovered_credential_rejected(self):
        self.store.add_practice(
            "p1", STUDENT, CRAFT, S1, "shop-a",
            [{"date": "2026-03-02", "hours": 6, "mentor_id": WANG},
             {"date": "2026-03-03", "hours": 6, "mentor_id": WANG}],
            works=[{"work_id": "w1", "name": "皮胚一组",
                    "produced_at": "2026-03-03"}],
        )
        self.store.add_credential("cred-exp", ZHOU, CRAFT,
                                  "2025-01-01", "2025-12-31")
        with self.assertRaises(RuleViolation):
            self.store.sign_assessment(
                "a-exp", STUDENT, CRAFT, S1, V1, ["p1"], ["w1"], "fail",
                "评语", ZHOU, "2026-03-10")

        # 有效时段但不覆盖考核采用的标准版本。
        self.store.add_credential("cred-v2", ZHOU, CRAFT,
                                  "2026-01-01", "2026-12-31",
                                  qualified_versions=["v9"])
        with self.assertRaises(RuleViolation):
            self.store.sign_assessment(
                "a-v2", STUDENT, CRAFT, S1, V1, ["p1"], ["w1"], "fail",
                "评语", ZHOU, "2026-03-10")

    def test_mentor_conflict_must_recuse(self):
        self.store.add_practice(
            "p1", STUDENT, CRAFT, S1, "shop-a",
            [{"date": "2026-03-02", "hours": 12, "mentor_id": WANG}])
        self.store.add_credential("cred-wang", WANG, CRAFT,
                                  "2026-01-01", "2026-12-31")
        with self.assertRaisesRegex(RuleViolation, "利益冲突"):
            self.store.sign_assessment(
                "a-wang", STUDENT, CRAFT, S1, V1, ["p1"], [], "pass",
                "师傅自评", WANG, "2026-03-10")

    def test_same_store_conflict_must_recuse(self):
        self.store.add_practice(
            "p1", STUDENT, CRAFT, S1, "shop-a",
            [{"date": "2026-03-02", "hours": 12, "mentor_id": WANG}])
        self.store.add_credential("cred-li", LI, CRAFT,
                                  "2026-01-01", "2026-12-31")
        with self.assertRaisesRegex(RuleViolation, "同一门店"):
            self.store.sign_assessment(
                "a-li", STUDENT, CRAFT, S1, V1, ["p1"], [], "pass",
                "本店评审", LI, "2026-03-10")

    def test_evidence_from_other_store_allows_assessment(self):
        # 评审在本店挂职，证据全部来自轮转店，则不构成同店冲突。
        self.store.add_practice(
            "pb", STUDENT, CRAFT, S1, "shop-b",
            [{"date": "2026-06-02", "hours": 12, "mentor_id": ZHAO}])
        self.store.add_credential("cred-li-b", LI, CRAFT,
                                  "2026-01-01", "2026-12-31")
        outcome = self.store.sign_assessment(
            "a-b", STUDENT, CRAFT, S1, V1, ["pb"], [], "pass",
            "跨店证据可评", LI, "2026-06-10")
        self.assertEqual(outcome["qualification"]["level"], "independent")


class PracticeDedupTest(unittest.TestCase):
    def setUp(self):
        self.store = build_world()
        self.store.add_practice(
            "p1", STUDENT, CRAFT, S1, "shop-a",
            [{"date": "2026-03-02", "hours": 6, "mentor_id": WANG}])
        self.store.add_practice(
            "p2", STUDENT, CRAFT, S1, "shop-a",
            [{"date": "2026-03-03", "hours": 6, "mentor_id": WANG}])

    def test_same_day_other_store_not_double_counted(self):
        with self.assertRaisesRegex(RuleViolation, "重复计算"):
            self.store.add_practice(
                "p-dup", STUDENT, CRAFT, S1, "shop-b",
                [{"date": "2026-03-03", "hours": 8, "mentor_id": ZHAO}])
        hours = self.store.practice_hours(STUDENT, CRAFT, S1)
        self.assertEqual(hours["total_hours"], 12.0)
        self.assertEqual(sum(hours["by_store"].values()), 12.0)

    def test_duplicate_batch_submit_is_idempotent(self):
        again = self.store.add_practice(
            "p1", STUDENT, CRAFT, S1, "shop-a",
            [{"date": "2026-03-02", "hours": 6, "mentor_id": WANG}])
        self.assertTrue(again["deduped"])
        self.assertEqual(self.store.practice_hours(STUDENT)["total_hours"], 12.0)

    def test_duplicate_date_inside_one_batch_rejected(self):
        with self.assertRaises(RuleViolation):
            self.store.add_practice(
                "p-x", STUDENT, CRAFT, S2, "shop-b",
                [{"date": "2026-06-02", "hours": 4, "mentor_id": ZHAO},
                 {"date": "2026-06-02", "hours": 4, "mentor_id": ZHAO}])

    def test_session_after_mentorship_ended_rejected(self):
        self.store.register_person("stu-tmp", "临时工", ["apprentice"])
        self.store.add_mentorship("stu-tmp", WANG, CRAFT, "shop-a",
                                  "2026-03-01", "2026-03-31")
        with self.assertRaisesRegex(RuleViolation, "师徒关系"):
            self.store.add_practice(
                "p-end", "stu-tmp", CRAFT, S1, "shop-a",
                [{"date": "2026-04-02", "hours": 4, "mentor_id": WANG}])


class AssessmentAndTraceTest(unittest.TestCase):
    def setUp(self):
        self.store = build_world()
        self.store.add_practice(
            "p1", STUDENT, CRAFT, S1, "shop-a",
            [{"date": "2026-03-02", "hours": 6, "mentor_id": WANG},
             {"date": "2026-03-03", "hours": 6, "mentor_id": WANG}],
            works=[{"work_id": "w1", "name": "首考皮胚",
                    "produced_at": "2026-03-03"}])

    def _assess(self, attempt_id, result, comments, day, works=None):
        return self.store.sign_assessment(
            attempt_id, STUDENT, CRAFT, S1, V1, ["p1"],
            works or ["w1"], result, comments, SUN, day)

    def test_insufficient_hours_blocks_signoff(self):
        with self.assertRaisesRegex(RuleViolation, "学时不足"):
            self.store.sign_assessment(
                "a-zheng", STUDENT, CRAFT, S3, V1, [], [], "pass",
                "无练习不可签", SUN, "2026-03-10")

    def test_retake_appends_without_overwriting_and_is_traceable(self):
        first = self._assess("a1", "fail", "皮厚薄不均", "2026-03-10")
        self.assertIsNone(first["qualification"])

        second = self._assess("a2", "pass", "补考：均匀达标", "2026-04-02",
                              works=["w1"])
        qual_id = second["qualification"]["qualification_id"]
        self.assertEqual(second["qualification"]["level"], "independent")

        # 旧的失败评价仍然保留，没有被补考结论覆盖。
        trace = self.store.trace_qualification(qual_id)
        results = [h["result"] for h in trace["assessment_history"]]
        self.assertEqual(results, ["fail", "pass"])
        comments = [h["comments"] for h in trace["assessment_history"]]
        self.assertIn("皮厚薄不均", comments)
        self.assertIn("补考：均匀达标", comments)
        source = [h for h in trace["assessment_history"] if h["is_source"]]
        self.assertEqual(len(source), 1)
        self.assertEqual(source[0]["attempt_id"], "a2")
        self.assertEqual(trace["standard_at_issue"]["version_no"], V1)
        self.assertEqual(trace["standard_at_issue"]["step"]["required_hours"], 10)
        self.assertEqual(trace["assessment_history"][0]["works"][0]["work_id"], "w1")

    def test_conditional_pass_means_supervised_then_superseded(self):
        self.store.add_practice(
            "p2", STUDENT, CRAFT, S2, "shop-b",
            [{"date": f"2026-06-0{i}", "hours": 4, "mentor_id": ZHAO}
             for i in range(2, 7)])
        conditional = self.store.sign_assessment(
            "a3", STUDENT, CRAFT, S2, V1, ["p2"], [], "conditional_pass",
            "褶数尚可，需监督", SUN, "2026-06-20")
        old_qual = conditional["qualification"]
        self.assertEqual(old_qual["level"], "supervised")

        report = self.store.readiness_report(STUDENT, CRAFT)
        status = {s["step_id"]: s["status"] for s in report["steps"]}
        self.assertEqual(status[S1], "not_started")  # 本用例没有 S1 考核
        self.assertEqual(status[S2], "supervised")
        self.assertEqual(status[S3], "not_started")

        # 再次考核通过：产生新资格，旧资格停用但仍可追溯。
        renewed = self.store.sign_assessment(
            "a4", STUDENT, CRAFT, S2, V1, ["p2"], [], "pass",
            "独立操作达标", SUN, "2026-07-10")
        new_qual = renewed["qualification"]
        self.assertNotEqual(old_qual["qualification_id"],
                            new_qual["qualification_id"])
        self.assertFalse(self.store.trace_qualification(
            old_qual["qualification_id"])["qualification"]["active"])
        self.assertEqual(
            self.store.trace_qualification(old_qual["qualification_id"])
            ["qualification"]["superseded_by_attempt"], "a4")

        report = self.store.readiness_report(STUDENT, CRAFT)
        status = {s["step_id"]: s["status"] for s in report["steps"]}
        self.assertEqual(status[S2], "independent")

    def test_later_failure_deactivates_granted_qualification(self):
        self._assess("a1", "pass", "首次通过", "2026-03-10")
        self.assertEqual(
            self.store.readiness_report(STUDENT, CRAFT)
            ["steps"][0]["status"], "independent")

        outcome = self._assess("a2", "fail", "复评退步，暂停独立操作",
                               "2026-05-02")
        self.assertIsNone(outcome["qualification"])
        step = next(s for s in self.store.readiness_report(STUDENT, CRAFT)
                    ["steps"] if s["step_id"] == S1)
        self.assertEqual(step["status"], "not_ready")
        self.assertEqual(step["latest_result"], "fail")

    def test_missing_entities_raise_not_found(self):
        with self.assertRaises(NotFound):
            self.store.trace_qualification("QUAL-9999")


class SecretRedactionTest(unittest.TestCase):
    def setUp(self):
        self.store = build_world()
        self.store.add_demonstration(
            "d1", CRAFT, S2, V1, WANG, "2026-02-01",
            ingredients=[
                {"name": "面粉", "amount": "500g", "secret": False},
                {"name": "秘制酱油水", "amount": "30ml", "secret": True},
            ])

    def test_secret_hidden_from_apprentice_visible_to_master(self):
        student_view = self.store.get_demonstration("d1", "apprentice")
        secret = next(i for i in student_view["ingredients"] if i["secret"])
        self.assertTrue(secret["hidden"])
        self.assertIsNone(secret["amount"])
        self.assertNotIn("秘制酱油水", json.dumps(student_view, ensure_ascii=False))
        self.assertTrue(student_view["secret_redacted"])

        master_view = self.store.get_demonstration("d1", "master")
        secret = next(i for i in master_view["ingredients"] if i["secret"])
        self.assertFalse(secret.get("hidden"))
        self.assertEqual(secret["amount"], "30ml")
        self.assertFalse(master_view["secret_redacted"])

        admin_view = self.store.get_demonstration("d1", "brand_admin")
        self.assertFalse(admin_view["secret_redacted"])


class EmploymentSummaryTest(unittest.TestCase):
    def setUp(self):
        self.store = build_world()
        # 烧麦专业 4 名毕业生：小李去 shop-b，其余三人去 shop-a。
        self.store.record_graduation(STUDENT, CRAFT, "2026-07-01", 2026)
        self.store.record_employment(STUDENT, "shop-b", "2026-07-05", "学徒工")
        for idx in range(2, 5):
            pid = f"stu-{idx}"
            self.store.register_person(pid, f"学生{idx}", ["apprentice"])
            self.store.record_graduation(pid, CRAFT, "2026-07-01", 2026)
            self.store.record_employment(pid, "shop-a", "2026-07-05",
                                         "门店技师")
        # 小样本专业：仅 1 人毕业，整体隐去。
        self.store.register_craft("pastry", "酥饼")
        self.store.register_person("stu-p", "酥饼学生", ["apprentice"])
        self.store.record_graduation("stu-p", "pastry", "2026-07-01", 2026)

    def test_summary_is_aggregated_and_privacy_safe(self):
        report = self.store.employment_summary(2026)
        shaomai = next(c for c in report["crafts"] if c["craft_id"] == CRAFT)
        self.assertEqual(shaomai["graduates"], 4)
        self.assertEqual(shaomai["employed"], 4)
        self.assertEqual(shaomai["employment_rate"], 1.0)
        # 单店 1 人可能识别到个人，该店计数隐去；3 人门店保留。
        self.assertIsNone(shaomai["by_store"]["shop-b"])
        self.assertEqual(shaomai["by_store"]["shop-a"], 3)

        pastry = next(c for c in report["crafts"] if c["craft_id"] == "pastry")
        self.assertIsNone(pastry["graduates"])
        self.assertEqual(pastry["suppressed_reason"], "cohort_smaller_than_3")

        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("小李", serialized)
        self.assertNotIn("秘制", serialized)
        self.assertFalse(report["contains_personal_data"])
        self.assertFalse(report["contains_commercial_formula"])


if __name__ == "__main__":
    unittest.main()
