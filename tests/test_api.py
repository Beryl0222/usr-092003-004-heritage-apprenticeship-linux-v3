"""验证传承履历 HTTP 接口的端到端行为。"""

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import service
from service import Handler
from heritage import HeritageStore


def post(base_url, path, payload):
    request = Request(
        f"{base_url}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    return urlopen(request, timeout=2)


def get(base_url, path, headers=None):
    request = Request(f"{base_url}{path}", headers=headers or {})
    return urlopen(request, timeout=2)


class ApiFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = HeritageStore()
        Handler.store = cls.store
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        Handler.store = service.STORE

    def test_full_flow_over_http(self):
        b = self.base_url
        json.load(post(b, "/stores", {"store_id": "shop-a", "name": "一店"}))
        json.load(post(b, "/stores", {"store_id": "shop-b", "name": "二店"}))
        json.load(post(b, "/people", {"person_id": "stu", "name": "小陈",
                                      "roles": ["apprentice"]}))
        json.load(post(b, "/people", {"person_id": "m1", "name": "马师傅",
                                      "roles": ["master"]}))
        json.load(post(b, "/people", {"person_id": "r1", "name": "评审",
                                      "roles": ["assessor"]}))
        json.load(post(b, "/crafts", {"craft_id": "c1", "name": "烧麦"}))
        json.load(post(b, "/craft-versions", {
            "craft_id": "c1", "version_no": "v1",
            "effective_date": "2025-01-01", "creator": "m1",
            "steps": [{"step_id": "s1", "name": "包制", "key": True,
                       "required_hours": 4}]}))
        self.store.add_mentorship("stu", "m1", "c1", "shop-a", "2026-01-01")
        self.store.add_credential("cr1", "r1", "c1", "2026-01-01",
                                  "2026-12-31")

        json.load(post(b, "/practices", {
            "practice_id": "p1", "apprentice_id": "stu", "craft_id": "c1",
            "step_id": "s1", "store_id": "shop-a",
            "sessions": [{"date": "2026-01-02", "hours": 6,
                          "mentor_id": "m1"}],
            "works": [{"work_id": "w1", "name": "首件作品"}]}))

        json.load(post(b, "/assessments", {
            "attempt_id": "a1", "apprentice_id": "stu", "craft_id": "c1",
            "step_id": "s1", "standard_version": "v1",
            "practice_ids": ["p1"], "work_ids": ["w1"], "result": "pass",
            "comments": "达标", "assessor_id": "r1",
            "assessed_on": "2026-01-10"}))

        with get(b, "/apprentices/stu/readiness?craft_id=c1") as response:
            report = json.load(response)
        self.assertEqual(report["independent_steps"], ["s1"])

        # 秘方示范按 X-Viewer-Role 脱敏。
        self.store.add_demonstration(
            "d1", "c1", "s1", "v1", "m1", "2026-01-01",
            ingredients=[{"name": "秘料", "amount": "10g", "secret": True}])
        with get(b, "/demonstrations/d1",
                 {"X-Viewer-Role": "apprentice"}) as response:
            hidden = json.load(response)
        self.assertIsNone(hidden["ingredients"][0]["amount"])
        with get(b, "/demonstrations/d1",
                 {"X-Viewer-Role": "master"}) as response:
            shown = json.load(response)
        self.assertEqual(shown["ingredients"][0]["amount"], "10g")

        with self.assertRaises(HTTPError) as error:
            get(b, "/apprentices/nobody/readiness?craft_id=c1")
        self.assertEqual(error.exception.code, 404)
        error.exception.close()


if __name__ == "__main__":
    unittest.main()
