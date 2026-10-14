from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from schedule_agent import ScheduleService


ROOT = Path(__file__).resolve().parent
SERVICE = ScheduleService(ROOT)


class Handler(BaseHTTPRequestHandler):
    server_version = "ScheduleAgent/1.0"

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/runs":
                return self._json({"runs": SERVICE.store.list_runs()})
            if path == "/api/template":
                return self._file(SERVICE.template_path, "市政道路进度计划标准模板.xlsx")
            if path == "/api/sample-file":
                return self._file(SERVICE.sample_path, "市政道路进度计划示例.xlsx")
            if path.startswith("/api/runs/") and "/files/" in path:
                parts = path.strip("/").split("/")
                run_id, kind = parts[2], parts[4]
                return self._file(SERVICE.artifact_path(run_id, kind))
            if path.startswith("/api/runs/"):
                run_id = path.strip("/").split("/")[2]
                return self._json(SERVICE.get_result(run_id))
            return self._static(path)
        except Exception as exc:
            return self._error(exc)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/analyze":
                content = self._body()
                filename = unquote(self.headers.get("X-Filename", "input.xlsx"))
                actor = unquote(self.headers.get("X-Actor", "计划工程师"))
                return self._json(SERVICE.analyze(content, filename, actor), status=201)
            if path == "/api/sample":
                return self._json(SERVICE.analyze_sample(), status=201)
            if path.startswith("/api/runs/") and path.endswith("/transition"):
                run_id = path.strip("/").split("/")[2]
                body = self._json_body()
                return self._json(SERVICE.transition(run_id, body.get("action", ""), body.get("actor", ""), body.get("comment", "")))
            if path.startswith("/api/runs/") and path.endswith("/confirm-import"):
                run_id = path.strip("/").split("/")[2]
                body = self._json_body()
                return self._json(SERVICE.confirm_import(run_id, body.get("actor", ""), bool(body.get("acknowledged"))))
            if path.startswith("/api/runs/") and path.endswith("/archive"):
                run_id = path.strip("/").split("/")[2]
                body = self._json_body()
                return self._json(SERVICE.archive_plan(run_id, body.get("actor", "计划工程师")))
            if path.startswith("/api/runs/") and path.endswith("/chat"):
                run_id = path.strip("/").split("/")[2]
                body = self._json_body()
                return self._json({"answer": SERVICE.chat(run_id, body.get("message", ""))})
            return self._json({"error": "接口不存在"}, status=404)
        except Exception as exc:
            return self._error(exc)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 25 * 1024 * 1024:
            raise ValueError("文件为空或超过 25 MB")
        return self.rfile.read(length)

    def _json_body(self) -> dict:
        raw = self._body()
        return json.loads(raw.decode("utf-8"))

    def _json(self, value, status=200):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _file(self, path: Path, download_name: str | None = None):
        payload = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        if download_name:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(download_name)}")
        self.end_headers()
        self.wfile.write(payload)

    def _static(self, path: str):
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        candidate = (ROOT / "static" / relative).resolve()
        static_root = (ROOT / "static").resolve()
        if static_root not in candidate.parents and candidate != static_root:
            return self._json({"error": "路径无效"}, status=400)
        if not candidate.is_file():
            return self._json({"error": "页面不存在"}, status=404)
        return self._file(candidate)

    def _error(self, exc: Exception):
        status = 404 if "找不到" in str(exc) or "不存在" in str(exc) else 400
        return self._json({"error": str(exc)}, status=status)

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")


def main():
    parser = argparse.ArgumentParser(description="市政道路工程进度计划 Agent")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Schedule Agent running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
