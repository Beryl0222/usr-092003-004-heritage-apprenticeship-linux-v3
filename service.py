"""老字号传承履历的 HTTP 服务入口。

在原有健康检查之上开放传承履历接口；业务规则集中在 heritage.HeritageStore，
HTTP 层只负责 JSON 编解码、身份角色透传与错误码映射。
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from heritage import HeritageStore, LedgerError, NotFound, RuleViolation

SERVICE_ID = "heritage-apprenticeship"
SERVICE_NAME = "老字号传承履历"


def health_payload():
    """返回稳定的服务身份信息。"""
    return {"status": "ok", "service": SERVICE_ID, "name": SERVICE_NAME}


def build_store():
    """构建一个共享的履历存储（单进程内存态，供本地联调与测试）。"""
    return HeritageStore()


STORE = build_store()


class Handler(BaseHTTPRequestHandler):
    """提供健康检查与传承履历接口。"""

    store = STORE

    # ------------------------------------------------------------------ GET

    def do_GET(self):
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        one = lambda key: query.get(key, [None])[0]

        if path == "/health":
            self._write_json(200, health_payload())
            return

        if path.startswith("/demonstrations/"):
            demo_id = path.rsplit("/", 1)[-1]
            self._run(lambda: self.store.get_demonstration(
                demo_id, viewer_role=self.headers.get("X-Viewer-Role")))
            return

        if path.startswith("/apprentices/") and path.endswith("/readiness"):
            apprentice_id = path.split("/")[2]
            craft_id = one("craft_id")
            if not craft_id:
                self._write_json(400, {"error": "缺少 craft_id 查询参数"})
                return
            self._run(lambda: self.store.readiness_report(
                apprentice_id, craft_id, one("on_date")))
            return

        if path.startswith("/apprentices/") and path.endswith("/hours"):
            apprentice_id = path.split("/")[2]
            self._run(lambda: self.store.practice_hours(
                apprentice_id, one("craft_id"), one("step_id")))
            return

        if path.startswith("/qualifications/") and path.endswith("/trace"):
            qualification_id = path.split("/")[2]
            self._run(lambda: self.store.trace_qualification(qualification_id))
            return

        if path == "/school/employment-summary":
            year = one("graduation_year")
            self._run(lambda: self.store.employment_summary(
                int(year) if year else None))
            return

        self.send_error(404)

    # ------------------------------------------------------------------ POST

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._write_json(400, {"error": "请求体不是合法 JSON"})
            return
        if not isinstance(payload, dict):
            self._write_json(400, {"error": "请求体必须是 JSON 对象"})
            return

        actions = {
            "/people": lambda: self.store.register_person(
                payload["person_id"], payload["name"], payload["roles"],
                payload.get("home_store_id")),
            "/stores": lambda: self.store.register_store(
                payload["store_id"], payload["name"]),
            "/crafts": lambda: self.store.register_craft(
                payload["craft_id"], payload["name"]),
            "/craft-versions": lambda: self.store.add_craft_version(
                payload["craft_id"], payload["version_no"],
                payload["effective_date"], payload["creator"],
                payload["steps"]),
            "/credentials": lambda: self.store.add_credential(
                payload["credential_id"], payload["assessor_id"],
                payload["craft_id"], payload["valid_from"],
                payload["valid_until"], payload.get("qualified_versions"),
                payload.get("status", "active")),
            "/mentorships": lambda: self.store.add_mentorship(
                payload["apprentice_id"], payload["mentor_id"],
                payload["craft_id"], payload["store_id"],
                payload["start_date"], payload.get("end_date")),
            "/demonstrations": lambda: self.store.add_demonstration(
                payload["demonstration_id"], payload["craft_id"],
                payload["step_id"], payload["version_no"],
                payload["demonstrated_by"], payload["recorded_at"],
                payload.get("video_ref"), payload.get("ingredients")),
            "/practices": lambda: self.store.add_practice(
                payload["practice_id"], payload["apprentice_id"],
                payload["craft_id"], payload["step_id"], payload["store_id"],
                payload["sessions"], payload.get("works")),
            "/assessments": lambda: self.store.sign_assessment(
                payload["attempt_id"], payload["apprentice_id"],
                payload["craft_id"], payload["step_id"],
                payload["standard_version"], payload["practice_ids"],
                payload.get("work_ids", []), payload["result"],
                payload["comments"], payload["assessor_id"],
                payload["assessed_on"]),
            "/graduations": lambda: self.store.record_graduation(
                payload["apprentice_id"], payload["craft_id"],
                payload["graduation_date"], payload["graduation_year"]),
            "/employments": lambda: self.store.record_employment(
                payload["apprentice_id"], payload["store_id"],
                payload["hired_date"], payload["position"]),
        }
        if path not in actions:
            self.send_error(404)
            return
        self._run(actions[path])

    # ------------------------------------------------------------------ 工具

    def _run(self, action):
        try:
            self._write_json(200, action())
        except KeyError as missing:
            self._write_json(400, {"error": f"缺少必填字段：{missing.args[0]}"})
        except RuleViolation as error:
            self._write_json(400, {"error": str(error)})
        except NotFound as error:
            self._write_json(404, {"error": str(error)})
        except LedgerError as error:
            self._write_json(400, {"error": str(error)})

    def _write_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def main():
    parser = argparse.ArgumentParser(description=SERVICE_NAME)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        assert health_payload()["service"] == SERVICE_ID
        assert isinstance(build_store(), HeritageStore)
        print("基础检查通过")
        return
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
