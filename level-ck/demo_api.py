"""เซิร์ฟเวอร์ตัวอย่างสำหรับทดสอบหน้าเว็บ (ไม่ใช่ข้อมูลจริง)

ให้บริการสอง endpoint ที่หน้าเว็บเรียกใช้ คือ /token และ /info
ข้อมูลที่ตอบกลับเป็นค่าตัวอย่างที่คำนวณจาก UID เท่านั้น ไม่ได้เชื่อมต่อกับเกม
หรือบริการใด ๆ ใช้เพื่อทดสอบหน้าจอ/การแยกหมวดเลเวล โดยไม่ต้องส่งบัญชีจริงออกไป

รัน:  python demo_api.py            (ฟังที่ 127.0.0.1:8899)
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


class DemoHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # ไม่บันทึก query string เพราะมีรหัสผ่านอยู่ในนั้น
        print("demo-api: %s %s" % (self.command, urlparse(self.path).path), flush=True)

    def send_json(self, body, code=200):
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        uid = (query.get("uid") or [""])[0].strip()

        if url.path == "/token":
            password = (query.get("password") or [""])[0]
            if uid.isdigit() and password:
                self.send_json({"status": "success", "real_uid": uid})
            else:
                self.send_json({"status": "error", "message": "ต้องมี uid และรหัสผ่าน"})
            return

        if url.path == "/info":
            if not uid.isdigit():
                self.send_json({"error": "uid ไม่ถูกต้อง"}, 400)
                return
            self.send_json(
                {
                    "basicInfo": {
                        "nickname": "DEMO-%s" % uid[-4:],
                        "level": int(uid) % 31,
                        "region": "DEMO",
                    }
                }
            )
            return

        self.send_json({"error": "ไม่พบ endpoint นี้"}, 404)


if __name__ == "__main__":
    host = os.environ.get("DEMO_HOST", "127.0.0.1")
    port = int(os.environ.get("DEMO_PORT", "8899"))
    print("demo-api: listening on %s:%d" % (host, port), flush=True)
    HTTPServer((host, port), DemoHandler).serve_forever()
