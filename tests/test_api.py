"""验证传承履历 HTTP 接口的契约。"""

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from heritage import HeritageStore
from service import Handler


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Handler.store = HeritageStore()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(f"{self.base_url}{path}", data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            body = error.read().decode("utf-8")
            error.close()
            try:
                return error.code, json.loads(body)
            except json.JSONDecodeError:
                return error.code, {}

    def post(self, path, payload):
        return self.request("POST", path, payload)

    def get(self, path):
        return self.request("GET", path)

    def test_full_apprenticeship_flow(self):
        status, _ = self.post("/persons", {"id": "m1", "name": "王师傅", "role": "master"})
        self.assertEqual(status, 201)
        status, _ = self.post(
            "/persons", {"id": "a1", "name": "小李", "role": "apprentice", "id_number": "110101200401010022"}
        )
        self.assertEqual(status, 201)
        status, _ = self.post("/persons", {"id": "r1", "name": "评审甲", "role": "reviewer"})
        self.assertEqual(status, 201)
        status, _ = self.post(
            "/reviewers",
            {"id": "rev1", "person_id": "r1", "certificate": "CERT-1", "valid_until": "2027-01-01"},
        )
        self.assertEqual(status, 201)
        status, _ = self.post(
            "/technique-versions",
            {
                "id": "tv1",
                "technique": "烧麦",
                "version": "v1",
                "steps": [{"id": "st1", "name": "擀皮", "demo": "走槌擀出荷叶边"}],
                "standards": {"st1": "皮边薄如纸"},
                "effective_from": "2025-01-01",
            }
        )
        self.assertEqual(status, 201)
        status, _ = self.post(
            "/materials", {"id": "mat1", "name": "秘制香料", "lot_no": "L2", "responsible": "品牌总部", "secret": True}
        )
        self.assertEqual(status, 201)
        status, _ = self.post(
            "/relations", {"master_id": "m1", "apprentice_id": "a1", "store_id": "s1", "start": "2026-01-01"}
        )
        self.assertEqual(status, 201)

        # 跨店轮转的两段重叠练习，学时合并为 3 小时而非 4 小时。
        for batch_id, store_id, start, end in (
            ("b1", "s1", "08:00", "10:00"),
            ("b2", "s2", "09:00", "11:00"),
        ):
            status, _ = self.post(
                "/batches",
                {
                    "id": batch_id, "apprentice_id": "a1", "step_id": "st1", "store_id": store_id,
                    "master_id": "m1", "date": "2026-06-10", "start": start, "end": end,
                    "material_ids": ["mat1"], "output": "烧麦皮若干",
                },
            )
            self.assertEqual(status, 201)
        status, body = self.get("/apprentices/a1/hours?date=2026-06-10")
        self.assertEqual(status, 200)
        self.assertEqual(body["hours"], 3.0)

        # 资质过期的评审人签署被拒。
        status, _ = self.post("/persons", {"id": "r2", "name": "评审乙", "role": "reviewer"})
        self.assertEqual(status, 201)
        status, _ = self.post(
            "/reviewers",
            {"id": "rev_expired", "person_id": "r2", "certificate": "CERT-2", "valid_until": "2020-01-01"},
        )
        self.assertEqual(status, 201)
        status, body = self.post(
            "/assessments",
            {
                "id": "as0", "apprentice_id": "a1", "step_id": "st1", "technique_version_id": "tv1",
                "store_id": "s1", "reviewer_id": "rev_expired", "decision": "pass",
                "level": "independent", "comment": "", "work_batch_ids": [], "assessed_on": "2026-06-20",
            },
        )
        self.assertEqual(status, 422)
        self.assertIn("资质已失效", body["error"])

        # 合格评审人签署，门店可核对独立工序。
        status, _ = self.post(
            "/assessments",
            {
                "id": "as1", "apprentice_id": "a1", "step_id": "st1", "technique_version_id": "tv1",
                "store_id": "s1", "reviewer_id": "rev1", "decision": "pass",
                "level": "independent", "comment": "擀皮均匀", "work_batch_ids": ["b1", "b2"],
                "assessed_on": "2026-06-20",
            },
        )
        self.assertEqual(status, 201)
        status, body = self.get("/apprentices/a1/clearance")
        self.assertEqual(status, 200)
        self.assertEqual(body["independent"], ["st1"])

        # 资格可追溯到作品、评语与当时标准。
        status, body = self.get("/assessments/as1/trace")
        self.assertEqual(status, 200)
        self.assertEqual(body["comment"], "擀皮均匀")
        self.assertEqual([w["id"] for w in body["works"]], ["b1", "b2"])
        self.assertEqual(body["standards"], {"st1": "皮边薄如纸"})

        # 秘方原料对学校角色隐藏，对品牌方可见。
        status, body = self.get("/materials?role=school_admin")
        self.assertEqual(status, 200)
        self.assertEqual(body["materials"], [{"id": "mat1", "name": "保密配料", "secret": True}])
        status, body = self.get("/materials?role=brand_admin")
        self.assertEqual(body["materials"][0]["name"], "秘制香料")

        # 学校汇总只有聚合数字。
        status, _ = self.post(
            "/employments",
            {"apprentice_id": "a1", "store_id": "s1", "position": "面点工", "started_on": "2026-08-01"},
        )
        self.assertEqual(status, 201)
        status, body = self.get("/summary/school")
        self.assertEqual(status, 200)
        self.assertEqual(body["apprentices"], 1)
        self.assertEqual(body["employed"], 1)
        self.assertNotIn("小李", json.dumps(body, ensure_ascii=False))

    def test_unknown_assessment_trace_is_404(self):
        status, _ = self.get("/assessments/nope/trace")
        self.assertEqual(status, 404)

    def test_bad_payload_is_400(self):
        status, _ = self.post("/persons", {"id": "x"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
