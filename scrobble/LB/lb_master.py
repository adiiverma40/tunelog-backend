# lb_master.py
import sqlite3
import time
from typing import Dict, List

from rich.console import Console

from core.crypto import decrypt_token
from core.db import get_db_connection_usr
from Workers.worker_queue import LB_queue, lbWork

console = Console()


def load_lb_users() -> List[Dict[str, str]]:
    usr_conn = get_db_connection_usr()
    cursor = usr_conn.cursor()
    cursor.execute(
        "SELECT username, LB_token, LB_username FROM user "
        "WHERE LB_token IS NOT NULL AND LB_token != '' "
        "AND LB_username IS NOT NULL AND LB_username != ''"
    )
    rows = cursor.fetchall()
    usr_conn.close()

    resolved = []
    for row in rows:
        try:
            decrypted = decrypt_token(row["LB_token"])
            resolved.append(
                {
                    "db_username": row["username"],
                    "decrypted_token": decrypted,
                    "lb_username": row["LB_username"],
                }
            )
        except Exception as e:
            console.print(f"  [red]✗ Decrypt failed for '{row['username']}': {e}[/red]")
    return resolved


def resolve_lb_username(decrypted_token: str) -> str | None:
    try:
        r = LB_queue.addWork(
            work=lbWork(
                method="GET", endpoint="/1/validate-token", token=decrypted_token
            )
        )
        if r.get("status_code") == 200 and r.get("status") == "success":
            data = r.get("data", {})
            return data.get("user_name") if data.get("valid") else None
    except Exception:
        return None


def execute_with_retry(cursor, conn, sql, data, retries=5, delay=2):
    for attempt in range(retries):
        try:
            cursor.executemany(sql, data)
            conn.commit()
            return True
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                time.sleep(delay)
                continue
            conn.rollback()
            raise
    conn.rollback()
    return False
