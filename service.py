"""老字号传承履历的服务入口。"""

import argparse
import dataclasses
import json
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

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

SERVICE_ID = "heritage-apprenticeship"
SERVICE_NAME = "老字号传承履历"


def health_payload():
    """返回稳定的服务身份信息。"""
    return {"status": "ok", "service": SERVICE_ID, "name": SERVICE_NAME}


def _jsonable(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(val) for key, val in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


class Handler(BaseHTTPRequestHandler):
    """传承履历接口：档案登记、练习批次、现场考核、上岗核对与追溯。"""

    store = HeritageStore()

    def do_GET(self):
        path, query = self._split()
        try:
            if path == "/health":
                return self._send(200, health_payload())
            if path == "/materials":
                role = query.get("role", [""])[0]
                return self._send(200, {"materials": self.store.visible_materials(role)})
            if path == "/summary/school":
                return self._send(200, self.store.school_summary())
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "apprentices" and parts[2] == "clearance":
                version_id = query.get("technique_version_id", [None])[0]
                return self._send(200, self.store.clearance(parts[1], version_id))
            if len(parts) == 3 and parts[0] == "apprentices" and parts[2] == "hours":
                return self._send(
                    200,
                    {
                        "apprentice_id": parts[1],
                        "hours": self.store.effective_hours(
                            parts[1], query.get("date", [None])[0]
                        ),
                    },
                )
            if len(parts) == 3 and parts[0] == "assessments" and parts[2] == "trace":
                return self._send(200, self.store.trace(parts[1]))
            self.send_error(404)
        except DomainError as exc:
            self._send(422, {"error": str(exc)})
        except KeyError:
            self._send(404, {"error": "资源不存在"})

    def do_POST(self):
        path, _ = self._split()
        try:
            payload = self._read_json()
            if path == "/persons":
                return self._send(201, self.store.add_person(Person(**payload)))
            if path == "/reviewers":
                return self._send(201, self.store.add_reviewer(Reviewer(**payload)))
            if path == "/technique-versions":
                return self._send(201, self.store.add_technique_version(TechniqueVersion(**payload)))
            if path == "/materials":
                return self._send(201, self.store.add_material(Material(**payload)))
            if path == "/relations":
                return self._send(201, self.store.add_relation(Relation(**payload)))
            if path == "/batches":
                return self._send(201, self.store.record_batch(PracticeBatch(**payload)))
            if path == "/assessments":
                return self._send(201, self.store.sign_assessment(Assessment(**payload)))
            if path == "/employments":
                return self._send(201, self.store.add_employment(Employment(**payload)))
            self.send_error(404)
        except DomainError as exc:
            self._send(422, {"error": str(exc)})
        except KeyError:
            self._send(404, {"error": "资源不存在"})
        except (TypeError, ValueError) as exc:
            self._send(400, {"error": f"请求格式错误：{exc}"})

    def _split(self):
        parsed = urlparse(self.path)
        return parsed.path, parse_qs(parsed.query)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _send(self, status, payload):
        body = json.dumps(_jsonable(payload), ensure_ascii=False).encode("utf-8")
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
        assert Handler.store is not None
        print("基础检查通过")
        return
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
