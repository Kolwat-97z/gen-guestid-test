import os
import sys
import json
import time
import requests
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

# Discord webhook-এ রেজাল্ট পাঠানোর জন্য (level-ck/discord_notify.py)
from discord_notify import send_report

# Termux UI-এর জন্য Rich Console সেটআপ
console = Console()

# ফোল্ডার ও ফাইল কনফিগারেশন
INPUT_FILE = "accounts.txt"
OUTPUT_FOLDER = "Results"

SUCCESS_FILE = f"{OUTPUT_FOLDER}/Valid_Accounts.txt"
DETAILS_FILE = f"{OUTPUT_FOLDER}/Details_Accounts.txt"
FAILED_FILE = f"{OUTPUT_FOLDER}/Failed_Accounts.txt"

# লেভেল ভিত্তিক আলাদা ফাইলসমূহ
LEVEL_22_PLUS_FILE = f"{OUTPUT_FOLDER}/22-lvl_Accounts.txt"      # লেভেল ২২ বা তার বেশি
LEVEL_20_21_FILE   = f"{OUTPUT_FOLDER}/20-21-lvl_Accounts.txt"   # লেভেল ২০ থেকে ২১
LEVEL_8_19_FILE    = f"{OUTPUT_FOLDER}/8-19-lvl_Accounts.txt"    # লেভেল ৮ থেকে ১৯
BELOW_8_FILE       = f"{OUTPUT_FOLDER}/Below-8_Accounts.txt"     # লেভেল ৮ এর নিচে

ALL_RESULT_FILES = [
    SUCCESS_FILE, DETAILS_FILE, FAILED_FILE,
    LEVEL_22_PLUS_FILE, LEVEL_20_21_FILE, LEVEL_8_19_FILE, BELOW_8_FILE
]

THREAD_LIMIT = 4

# ফাইল রাইট থ্রেড-সেফ করার জন্য লক এবং ট্র্যাকার
FILE_LOCK = threading.Lock()
EXISTING_UIDS_PER_FILE = {fp: set() for fp in ALL_RESULT_FILES}
HEADER_WRITTEN_IN_SESSION = set()

# API এন্ডপয়েন্টস
JWT_GEN_API = "http://node54.lunes.host:3069/"
INFO_API = "https://ayan-ob55-info.vercel.app/info"

def setup_folders():
    """ফোল্ডার ও ফাইল তৈরি করা"""
    if not os.path.exists(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)
    if not os.path.exists(INPUT_FILE):
        with open(INPUT_FILE, "w", encoding="utf-8") as f:
            f.write("# Enter accounts as UID:PASSWORD\n")
            f.write("6180106694:1_DEV_w8w1Ia3tpo6b\n")
        console.print(f"[bold yellow][!] '{INPUT_FILE}' file banano hoyeche. Data add kore abr run korun.[/bold yellow]")
        sys.exit()

def load_existing_uids_from_files():
    """ফাইলগুলোতে আগে থেকে থাকা সব UID মেমোরিতে লোড করা (ডুপ্লিকেট আটকানোর জন্য)"""
    for file_path in ALL_RESULT_FILES:
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        
                        # UID বের করার লজিক (UID:Password অথবা UID: 123456 ইত্যাদি)
                        potential_uid = ""
                        if ":" in line:
                            first_part = line.split(":", 1)[0].strip()
                            clean = first_part.replace("UID", "").replace("uid", "").strip()
                            if clean.isdigit():
                                potential_uid = clean
                        elif "UID" in line:
                            parts = line.split("|")[0]
                            clean = parts.replace("UID:", "").replace("uid:", "").strip()
                            if clean.isdigit():
                                potential_uid = clean

                        if potential_uid:
                            EXISTING_UIDS_PER_FILE[file_path].add(potential_uid)
            except Exception:
                pass

def get_account_id(account_line):
    """নতুন API (status: success, real_uid) থেকে ডাটা সংগ্রহ"""
    headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36"}
    line = account_line.strip()
    
    if ":" not in line:
        if line.isdigit():
            return {"account_id": line, "original_uid": line, "password": "", "error": None}
        return {"account_id": None, "original_uid": None, "password": None, "error": "Invalid format (UID:PASS chai)"}

    try:
        uid, password = line.split(":", 1)
        uid = uid.strip()
        password = password.strip()
    except ValueError:
        return {"account_id": None, "original_uid": None, "password": None, "error": "Parsing failed"}

    # আপনার API এন্ডপয়েন্ট ও প্যারামিটার
    api_url = "https://ayan-jwt-token-gen.vercel.app/token"
    params = {"uid": uid, "password": password}

    try:
        response = requests.get(api_url, params=params, headers=headers, timeout=15)
        if response.status_code == 200:
            res_json = response.json()
            
            # 🔥 নতুন JSON রেসপন্স অনুযায়ী চেক (status: "success" এবং real_uid / account_id)
            if res_json.get("status") == "success" or res_json.get("success") is True:
                # real_uid বা account_id অথবা uid খুঁজল
                acc_id = res_json.get("real_uid") or res_json.get("account_id") or res_json.get("uid")
                if acc_id:
                    return {"account_id": str(acc_id), "original_uid": uid, "password": password, "error": None}
                return {"account_id": None, "original_uid": None, "password": None, "error": "Real UID missing in response"}
            else:
                err_msg = res_json.get("message") or res_json.get("error") or "Login Rejected"
                return {"account_id": None, "original_uid": None, "password": None, "error": err_msg}
        return {"account_id": None, "original_uid": None, "password": None, "error": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"account_id": None, "original_uid": None, "password": None, "error": f"Net Error: {str(e)}"}

def get_account_info(account_id):
    """API 2 থেকে ডাটা সংগ্রহ"""
    params = {"uid": account_id}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        response = requests.get(INFO_API, params=params, headers=headers, timeout=12)
        if response.status_code == 200:
            res_json = response.json()
            if any(k in res_json for k in ("BasicInformation", "basicInfo", "AccountInfo")):
                return {"data": res_json, "error": None}
            return {"data": None, "error": "Invalid Data format"}
        return {"data": None, "error": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"data": None, "error": f"Net Error: {str(e)}"}

def save_to_file_safely(file_path, uid_key, content_line):
    """
    ফাইলে সেভ করার ফাংশন:
    ১. অলরেডি ফাইলে থাকলে ডুপ্লিকেট সেভ করবে না।
    ২. নতুন অ্যাকাউন্ট হলে সেশনের শুরুতে # --- New Accounts... হেডার দিবে।
    """
    with FILE_LOCK:
        # ফাইল যদি আগে স্ক্যান না হয়ে থাকে
        if file_path not in EXISTING_UIDS_PER_FILE:
            EXISTING_UIDS_PER_FILE[file_path] = set()

        # যদি এই ফাইলে অ্যাকাউন্টটি আগেই থাকে, তাহলে ডুপ্লিকেট করবে না
        if uid_key in EXISTING_UIDS_PER_FILE[file_path]:
            return False

        # নতুন অ্যাকাউন্ট পাওয়ার পর সেশনে প্রথমবার হেডার লেখা
        if file_path not in HEADER_WRITTEN_IN_SESSION:
            HEADER_WRITTEN_IN_SESSION.add(file_path)
            now_time = datetime.now().strftime("%d-%b-%Y %I:%M %p")
            header = f"\n# --- New Accounts Added on {now_time} ---\n"
            if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
                header = f"# --- New Accounts Added on {now_time} ---\n"
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(header)

        # নতুন ডাটা অ্যাপেন্ড করা
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(content_line + "\n")

        EXISTING_UIDS_PER_FILE[file_path].add(uid_key)
        return True

def run_diagnostics(test_account):
    """প্রথম অ্যাকাউন্টের ওপর টেস্ট রান"""
    console.print("[bold yellow][⚡] SYSTEM DIAGNOSTIC CHALANO HOCHCHE...[/bold yellow]")
    test_account = test_account.strip()
    console.print(f"[cyan][*] Loaded Account:[/cyan] '{test_account}'")
    
    jwt_res = get_account_id(test_account)
    account_id = jwt_res["account_id"]
    if not account_id:
        console.print(f"[red][-] API 1 Fail hoyeche: {jwt_res['error']}[/red]")
        return False
    console.print(f"[green][+] API 1 Success! Get ID: {account_id}[/green]")

    info_res = get_account_info(account_id)
    if not info_res["data"]:
        console.print(f"[red][-] API 2 Fail hoyeche: {info_res['error']}[/red]")
        return False
        
    console.print("[bold green][👑] DIAGNOSTIC PASSED! System full active ও ready.[/bold green]\n")
    return True

def process_account(account_data):
    """প্রতিটি অ্যাকাউন্টের প্রসেসিং লজিক"""
    account_data = account_data.strip()
    if not account_data or account_data.startswith("#"):
        return None

    # ১. এপিআই ১ কল
    jwt_res = get_account_id(account_data)
    account_id = jwt_res["account_id"]
    original_uid = jwt_res["original_uid"]
    password = jwt_res["password"]
    
    if not account_id:
        failed_uid = original_uid if original_uid else account_data
        save_to_file_safely(FAILED_FILE, failed_uid, f"API1_ERR | Input: {account_data} | Details: {jwt_res['error']}")
        return {"status": "failed", "data": account_data, "reason": jwt_res["error"]}

    # ২. এপিআই ২ কল
    info_res = get_account_info(account_id)
    info_data = info_res["data"]
    
    if not info_data:
        save_to_file_safely(FAILED_FILE, account_id, f"API2_ERR | ID: {account_id} | Details: {info_res['error']}")
        return {"status": "failed", "data": account_data, "reason": info_res["error"]}

    # ডাটা পার্সিং
    if "BasicInformation" in info_data:
        basic_info = info_data.get("BasicInformation", {})
        nickname = basic_info.get("Name", "Unknown")
        level = basic_info.get("Level", 0)
        region = basic_info.get("Region", "Unknown")
    elif "basicInfo" in info_data:
        basic_info = info_data.get("basicInfo", {})
        nickname = basic_info.get("nickname", "Unknown")
        level = basic_info.get("level", 0)
        region = basic_info.get("region", "Unknown")
    elif "AccountInfo" in info_data:
        basic_info = info_data.get("AccountInfo", {})
        nickname = basic_info.get("AccountName", "Unknown")
        level = basic_info.get("AccountLevel", 0)
        region = basic_info.get("AccountRegion", "Unknown")
    else:
        nickname = "Unknown"
        level = 0
        region = "Unknown"

    try:
        lvl_val = int(level)
    except (ValueError, TypeError):
        lvl_val = 0

    # ইউনিক কী
    uid_key = original_uid if original_uid else account_id
    credentials_line = f"{original_uid}:{password}" if original_uid and password else f"{uid_key}"

    # ১. ভ্যালিড অ্যাকাউন্ট ফাইল
    save_to_file_safely(SUCCESS_FILE, uid_key, f"UID: {account_id} | Name: {nickname} | Level: {lvl_val} | Region: {region}")

    # ২. ডিটেইলস ফাইল
    save_to_file_safely(DETAILS_FILE, uid_key, f"{credentials_line} | Level: {lvl_val} | Name: {nickname}")

    # ৩. লেভেল ভিত্তিক ক্যাটাগরি ফিল্টারিং
    cat_tag = ""
    if lvl_val >= 22:
        cat_tag = "22+ Lvl"
        save_to_file_safely(LEVEL_22_PLUS_FILE, uid_key, credentials_line)
    elif 20 <= lvl_val <= 21:
        cat_tag = "20-21 Lvl"
        save_to_file_safely(LEVEL_20_21_FILE, uid_key, credentials_line)
    elif 8 <= lvl_val <= 19:
        cat_tag = "8-19 Lvl"
        save_to_file_safely(LEVEL_8_19_FILE, uid_key, credentials_line)
    else:
        cat_tag = "< 8 Lvl"
        save_to_file_safely(BELOW_8_FILE, uid_key, credentials_line)

    return {
        "status": "success",
        "account_id": account_id,
        "nickname": nickname,
        "level": lvl_val,
        "region": region,
        "category": cat_tag
    }

def main():
    os.system("clear")
    
    banner = f"""[bold magenta]
     █████╗ ██╗   ██╗ █████╗ ███╗   ██╗
    ██╔══██╗╚██╗ ██╔╝██╔══██╗████╗  ██║
    ███████║ ╚████╔╝ ███████║██╔██╗ ██║
    ██╔══██║  ╚██╔╝  ██╔══██║██║╚████║
    ██║  ██║   ██║   ██║  ██║██║ ╚███║
    ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚══╝[/bold magenta]
    
 [bold cyan]┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓[/bold cyan]
 [bold cyan]┃[/bold cyan] [bold yellow]✨ VIP MULTI-TIER LEVEL FILTER v4.0 ✨[/bold yellow]             [bold cyan]┃[/bold cyan]
 [bold cyan]┃[/bold cyan] [bold white]Developer: AYAN CODEX                               [/bold white] [bold cyan]┃[/bold cyan]
 [bold cyan]┃[/bold cyan] [bold white]Logic    : 22+ | 20-21 | 8-19 | <8 Levels            [/bold white] [bold cyan]┃[/bold cyan]
 [bold cyan]┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛[/bold cyan]
    """
    console.print(banner)

    setup_folders()
    load_existing_uids_from_files()

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        raw_lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    # accounts.txt থেকে ডুপ্লিকেট অ্যাকাউন্ট ফিল্টার করে সিঙ্গেল করা
    seen_uids_in_input = set()
    accounts_to_check = []

    for line in raw_lines:
        uid_part = line.split(":", 1)[0].strip() if ":" in line else line.strip()
        if uid_part not in seen_uids_in_input:
            seen_uids_in_input.add(uid_part)
            accounts_to_check.append(line)

    duplicate_removed = len(raw_lines) - len(accounts_to_check)
    if duplicate_removed > 0:
        console.print(f"[bold yellow][*] Input file theke {duplicate_removed} ti duplicate account remove kora hoyeche.[/bold yellow]")

    total_accounts = len(accounts_to_check)
    if total_accounts == 0:
        console.print(f"[red][!] '{INPUT_FILE}' file faka! Account add korun.[/red]")
        return

    # ডায়াগনস্টিক চেক
    diagnostic_success = run_diagnostics(accounts_to_check[0])
    if not diagnostic_success:
        console.print("[yellow][!] Diagnostic fail hoyeche. Server problem hote pare.[/yellow]\n")
        choice = input("Force check chalate chan? (y/n): ")
        if choice.lower() != 'y':
            sys.exit()

    console.print(f"[yellow][*] Total {total_accounts} ti unique account verify shuru hochche...[/yellow]\n")

    # লাইভ টেবিল ভিউ
    table = Table(title="Audited Live Accounts Info", border_style="cyan")
    table.add_column("Account ID", style="cyan", no_wrap=True)
    table.add_column("Nickname", style="magenta")
    table.add_column("Level", style="green", justify="center")
    table.add_column("Region", style="yellow", justify="center")
    table.add_column("Category", style="bold white", justify="center")

    count_22_plus = 0
    count_20_21 = 0
    count_8_19 = 0
    count_below_8 = 0
    failed_count = 0

    # Discord রিপোর্টের জন্য রেজাল্ট জমা করা
    valid_accounts = []

    with Progress(
        SpinnerColumn(spinner_name="dots12"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30, complete_style="magenta"),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[bold cyan]Scanning accounts...[/bold cyan]", total=total_accounts)

        with ThreadPoolExecutor(max_workers=THREAD_LIMIT) as executor:
            futures = {executor.submit(process_account, acc): acc for acc in accounts_to_check}

            for future in as_completed(futures):
                result = future.result()
                if result:
                    if result["status"] == "success":
                        lvl = result["level"]
                        cat = result["category"]

                        if lvl >= 22:
                            count_22_plus += 1
                            cat_badge = "[bold green]22+ Lvl[/bold green]"
                        elif 20 <= lvl <= 21:
                            count_20_21 += 1
                            cat_badge = "[bold cyan]20-21 Lvl[/bold cyan]"
                        elif 8 <= lvl <= 19:
                            count_8_19 += 1
                            cat_badge = "[bold yellow]8-19 Lvl[/bold yellow]"
                        else:
                            count_below_8 += 1
                            cat_badge = "[dim white]< 8 Lvl[/dim white]"

                        valid_accounts.append(result)

                        table.add_row(
                            result["account_id"],
                            result["nickname"],
                            str(result["level"]),
                            result["region"],
                            cat_badge
                        )
                    else:
                        failed_count += 1
                        table.add_row("N/A", "N/A", "N/A", "N/A", "[red]FAILED[/red]")
                
                progress.update(task, advance=1)

    console.print("\n")
    console.print(table)

    # সামারি ড্যাশবোর্ড
    summary_text = (
        f"[bold green]✅ 22+ Level Accounts      : {count_22_plus}[/bold green] -> [cyan]{LEVEL_22_PLUS_FILE}[/cyan]\n"
        f"[bold cyan]⚡ 20-21 Level Accounts    : {count_20_21}[/bold cyan] -> [cyan]{LEVEL_20_21_FILE}[/cyan]\n"
        f"[bold yellow]⭐ 8-19 Level Accounts     : {count_8_19}[/bold yellow] -> [cyan]{LEVEL_8_19_FILE}[/cyan]\n"
        f"[dim white]🔻 Below 8 Level Accounts  : {count_below_8}[/dim white] -> [cyan]{BELOW_8_FILE}[/cyan]\n"
        f"[bold red]❌ Failed/Dead Accounts    : {failed_count}[/bold red] -> [cyan]{FAILED_FILE}[/cyan]\n\n"
        f"[bold white]📌 Full Info Log           : [cyan]{SUCCESS_FILE}[/cyan][/bold white]\n"
        f"[bold white]📌 Details (Pass) Log      : [cyan]{DETAILS_FILE}[/cyan][/bold white]\n\n"
        f"[bold green][✓] Note: রেজাল্ট ফাইলগুলোতে কোনো ডুপ্লিকেট এন্ট্রি হবে না এবং নতুন অ্যাকাউন্টগুলোর উপরে তারিখ সহ হেডার বসেছে।[/bold green]"
    )
    console.print(Panel(summary_text, title="📊 CHECK SUMMARY REPORT 📊", border_style="purple"))

    # Discord webhook-এ সামারি ও ভ্যালিড অ্যাকাউন্ট পাঠানো
    discord_ok, discord_error = send_report(
        accounts=valid_accounts,
        counts={
            "22+ Lvl": count_22_plus,
            "20-21 Lvl": count_20_21,
            "8-19 Lvl": count_8_19,
            "< 8 Lvl": count_below_8,
        },
        total=total_accounts,
        failed=failed_count,
    )
    if discord_ok:
        console.print("[bold green][✓] Discord webhook-এ রেজাল্ট পাঠানো হয়েছে।[/bold green]")
    else:
        console.print(f"[bold yellow][!] Discord webhook পাঠানো যায়নি: {discord_error}[/bold yellow]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print(f"\n[bold red][!] Program stop kora hoyeche![/bold red]")
        sys.exit()