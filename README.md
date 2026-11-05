# gen-guestid-test

Guest account level checker — a Termux-friendly CLI that verifies `UID:PASSWORD`
accounts and sorts them into level files. Source: `level-ck/app.py`.

## Run

```bash
cd level-ck
pip install -r requirements.txt
python app.py
```

Accounts go in `level-ck/accounts.txt`, one `UID:PASSWORD` per line. Results are
written to `level-ck/Results/` (`Valid_Accounts.txt`, the per-level files and
`Failed_Accounts.txt`).

## Discord webhook

Every run posts a summary embed plus the valid accounts grouped by level to a
Discord webhook. The URL is a secret and is never stored in the repository:

```bash
cp level-ck/.env.example level-ck/.env   # then paste your webhook URL
```

`level-ck/.env` is git-ignored. `DISCORD_WEBHOOK_URL` in the environment takes
precedence over the file. If neither is set, the run still completes normally
and only the notification is skipped.
