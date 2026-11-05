"""เว็บยูไอในเครื่องสำหรับ Guest Level Checker

กรอกบัญชีในหน้าเว็บแทนการใส่ใน accounts.txt:
- เซิร์ฟเวอร์ตรวจในหน่วยความจำ ไม่เขียนไฟล์ใด ๆ ลงดิสก์
- รหัสผ่านไม่ถูกส่งกลับใน response (เบราว์เซอร์เก็บเองและสร้างไฟล์ดาวน์โหลดเอง)

รัน:  python webapp.py     (ฟังที่ 0.0.0.0:$PORT ค่าเริ่มต้น 3000)
"""

import os
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, render_template, request

import app as checker

MAX_ACCOUNTS = int(os.environ.get("MAX_ACCOUNTS", "200"))

web = Flask(__name__)
web.json.ensure_ascii = False


def parse_accounts(raw):
    """ล้างบรรทัด UID:Password และตัดบัญชีซ้ำ (ยึด UID เป็นหลัก)"""
    entries, seen = [], set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            uid = line.split(":", 1)[0].strip()
        else:
            uid = line
        if not uid or uid in seen:
            continue
        seen.add(uid)
        entries.append((uid, line))
    return entries


def check_account(entry):
    """ตรวจบัญชีหนึ่งรายการ โดยไม่คืนรหัสผ่านกลับไป"""
    uid, line = entry
    try:
        jwt_res = checker.get_account_id(line)
        account_id = jwt_res["account_id"]
        if not account_id:
            return {"uid": uid, "status": "failed", "error": jwt_res["error"]}

        info_res = checker.get_account_info(account_id)
        if not info_res["data"]:
            return {
                "uid": uid,
                "status": "failed",
                "account_id": account_id,
                "error": info_res["error"],
            }

        parsed = checker.parse_basic_info(info_res["data"])
        return {
            "uid": uid,
            "status": "success",
            "account_id": account_id,
            "nickname": parsed["nickname"],
            "level": parsed["level"],
            "region": parsed["region"],
            "category": checker.category_from_level(parsed["level"]),
        }
    except Exception as exc:  # เครือข่าย/รูปแบบข้อมูลผิดพลาด ไม่ทำให้ทั้งชุดล้ม
        return {"uid": uid, "status": "failed", "error": "Checker error: %s" % exc}


def summarize(results):
    counts = {"22+ Lvl": 0, "20-21 Lvl": 0, "8-19 Lvl": 0, "< 8 Lvl": 0, "failed": 0}
    for item in results:
        if item["status"] == "success":
            counts[item["category"]] += 1
        else:
            counts["failed"] += 1
    return counts


@web.get("/")
def index():
    return render_template(
        "index.html",
        max_accounts=MAX_ACCOUNTS,
        account_api=checker.JWT_GEN_API,
        info_api=checker.INFO_API,
        demo_mode=os.environ.get("DEMO_MODE") == "1",
    )


@web.post("/api/check")
def check():
    payload = request.get_json(silent=True) or {}
    raw = payload.get("accounts", "")
    if not isinstance(raw, str):
        return jsonify({"error": "รูปแบบข้อมูลไม่ถูกต้อง"}), 400

    entries = parse_accounts(raw)
    if not entries:
        return jsonify({"error": "ยังไม่มีบัญชีให้ตรวจ กรอกบรรทัดละหนึ่งบัญชีในรูปแบบ UID:รหัสผ่าน"}), 400
    if len(entries) > MAX_ACCOUNTS:
        return jsonify({"error": "ตรวจได้สูงสุด %d บัญชีต่อครั้ง" % MAX_ACCOUNTS}), 400

    with ThreadPoolExecutor(max_workers=checker.THREAD_LIMIT) as pool:
        results = list(pool.map(check_account, entries))

    return jsonify(
        {
            "total": len(results),
            "counts": summarize(results),
            "results": results,
        }
    )


@web.after_request
def no_store(response):
    """ผลตรวจมีข้อมูลบัญชี จึงไม่ให้เบราว์เซอร์หรือพร็อกซีแคช"""
    response.headers["Cache-Control"] = "no-store"
    return response


if __name__ == "__main__":
    web.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "3000")),
        threaded=True,
    )
