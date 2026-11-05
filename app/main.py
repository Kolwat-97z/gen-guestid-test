"""Account check report: record UID / nickname / level / ban status and send it to Discord."""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

STATIC_DIR = Path(__file__).parent / "static"
DB_PATH = Path(os.getenv("DB_PATH", "/data/checks.db"))
DEFAULT_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
ALLOWED_WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
)

STATUS_LABELS = {
    "normal": "ปกติ",
    "temporary": "ถูกระงับชั่วคราว",
    "banned": "ถูกแบน",
}
STATUS_COLORS = {"normal": 0x2ECC71, "temporary": 0xF1C40F, "banned": 0xE74C3C}

app = FastAPI(title="Account Check Report")


def level_tier(level: int) -> str:
    if level >= 22:
        return "22+"
    if level >= 20:
        return "20-21"
    if level >= 8:
        return "8-19"
    return "ต่ำกว่า 8"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT NOT NULL,
                nickname TEXT NOT NULL,
                level INTEGER NOT NULL,
                status TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                checked_at TEXT NOT NULL
            )
            """
        )


def row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["tier"] = level_tier(data["level"])
    data["status_label"] = STATUS_LABELS[data["status"]]
    return data


class CheckIn(BaseModel):
    uid: str = Field(min_length=6, max_length=20)
    nickname: str = Field(min_length=1, max_length=32)
    level: int = Field(ge=1, le=100)
    status: str = "normal"
    note: str = Field(default="", max_length=500)

    @field_validator("uid")
    @classmethod
    def uid_is_numeric(cls, value: str) -> str:
        value = value.strip()
        if not value.isdigit():
            raise ValueError("UID ต้องเป็นตัวเลขเท่านั้น")
        return value

    @field_validator("nickname")
    @classmethod
    def nickname_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("กรุณากรอกชื่อในเกม")
        return value

    @field_validator("status")
    @classmethod
    def status_known(cls, value: str) -> str:
        if value not in STATUS_LABELS:
            raise ValueError("สถานะบัญชีไม่ถูกต้อง")
        return value


class WebhookIn(BaseModel):
    webhook_url: str = ""


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/config")
def config() -> dict:
    return {"has_default_webhook": bool(DEFAULT_WEBHOOK)}


@app.get("/api/checks")
def list_checks() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM checks ORDER BY id DESC LIMIT 25"
        ).fetchall()
    return [row_to_dict(row) for row in rows]


@app.post("/api/checks")
def create_check(payload: CheckIn) -> dict:
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO checks (uid, nickname, level, status, note, checked_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                payload.uid,
                payload.nickname,
                payload.level,
                payload.status,
                payload.note.strip(),
                checked_at,
            ),
        )
        row = conn.execute(
            "SELECT * FROM checks WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return row_to_dict(row)


def build_embed(record: dict) -> dict:
    fields = [
        {"name": "UID", "value": record["uid"], "inline": True},
        {"name": "ชื่อในเกม", "value": record["nickname"], "inline": True},
        {"name": "เลเวล", "value": str(record["level"]), "inline": True},
        {"name": "ช่วงเลเวล", "value": record["tier"], "inline": True},
        {"name": "สถานะบัญชี", "value": record["status_label"], "inline": True},
    ]
    if record["note"]:
        fields.append({"name": "หมายเหตุ", "value": record["note"][:1000], "inline": False})
    return {
        "username": "Account Check",
        "embeds": [
            {
                "title": f"ผลตรวจบัญชี UID {record['uid']}",
                "color": STATUS_COLORS[record["status"]],
                "fields": fields,
                "footer": {"text": "ข้อมูลที่กรอกจากผู้ใช้ กรุณายืนยันกับเกมอีกครั้ง"},
                "timestamp": record["checked_at"],
            }
        ],
    }


@app.post("/api/checks/{check_id}/webhook")
async def send_webhook(check_id: int, payload: WebhookIn) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="ไม่พบรายการที่ต้องการส่ง")

    webhook_url = (payload.webhook_url or DEFAULT_WEBHOOK).strip()
    if not webhook_url.startswith(ALLOWED_WEBHOOK_PREFIXES):
        raise HTTPException(
            status_code=400,
            detail="Webhook ต้องเป็นลิงก์ Discord (https://discord.com/api/webhooks/...)",
        )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                webhook_url, json=build_embed(row_to_dict(row))
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"ส่งไม่สำเร็จ: {exc}") from exc

    if response.status_code not in (200, 204):
        raise HTTPException(
            status_code=502,
            detail=f"Discord ตอบกลับ {response.status_code}",
        )
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
