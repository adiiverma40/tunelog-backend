import time
from datetime import datetime
from typing import Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from core.crypto import decrypt_token
from core.db import (
    get_db_connection_lib,
    get_db_connection_Musicbrainz,
    get_db_connection_usr,
)
from scrobble.LB.lb_master import resolve_lb_username
from Workers.worker_queue import LB_queue, MB_queue, MBWork, lbWork

console = Console()
MAX_COUNT = 1000


def fetch_cf_recordings(
    lb_username: str, decrypted_token: str
) -> tuple[list, Optional[int]]:
    url = f"/1/cf/recommendation/user/{lb_username}/recording"
    params = {"count": MAX_COUNT, "offset": 0}

    try:
        r = LB_queue.addWork(
            work=lbWork(
                method="GET", endpoint=url, params=params, token=decrypted_token
            )
        )

        status_code = r.get("status_code")

        if status_code == 200 and r.get("status") == "success":
            payload = r.get("data", {}).get("payload", {})
            mbids = payload.get("mbids", [])
            total = payload.get("total_mbid_count", len(mbids))
            cf_last_updated = payload.get("last_updated", int(time.time()))

            console.print(
                f"    [cyan]↳ Total CF tracks available: {total} | Fetched: {len(mbids)}[/cyan]"
            )
            return mbids, cf_last_updated

        elif status_code == 404:
            console.print(
                f"    [yellow]⚠ No CF recommendations found for '{lb_username}' (404 — model may not have run yet)[/yellow]"
            )
            return [], None

        else:
            console.print(
                f"    [red]✗ CF fetch returned HTTP {status_code}: {r.get('error_msg')}[/red]"
            )
            return [], None

    except Exception as e:
        console.print(f"    [red]✗ CF fetch request failed: {e}[/red]")
        return [], None


def save_cf_to_db(db_username: str, mbids: list[dict], cf_last_updated: int) -> int:
    conn = get_db_connection_lib()
    cursor = conn.cursor()

    row = cursor.execute(
        "SELECT cf_last_updated FROM LB_CF WHERE username = ? LIMIT 1", (db_username,)
    ).fetchone()

    if row and row["cf_last_updated"] == cf_last_updated:
        console.print(
            f"  [yellow]⚠ CF data for '{db_username}' hasn't changed "
            f"(cf_last_updated={cf_last_updated}). Skipping insert.[/yellow]"
        )
        conn.close()
        return 0

    cursor.execute("DELETE FROM LB_CF WHERE username = ?", (db_username,))
    console.print(
        f"  [dim]↳ CF update detected, wiped old rows for '{db_username}'[/dim]"
    )

    fetched_at = datetime.utcnow().isoformat()
    rows = [
        (
            item.get("recording_mbid"),
            db_username,
            item.get("score", 0.0),
            cf_last_updated,
            fetched_at,
            item.get("latest_listened_at"),
        )
        for item in mbids
        if item.get("recording_mbid")
    ]

    cursor.executemany(
        """
        INSERT INTO LB_CF
            (recording_mbid, username, score, cf_last_updated, fetched_at, latest_listened_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()
    return len(rows)


def fetch_top_similar_user(lb_username: str, decrypted_token: str) -> str | None:
    url = f"/1/user/{lb_username}/similar-users"

    try:
        r = LB_queue.addWork(
            work=lbWork(method="GET", endpoint=url, token=decrypted_token)
        )

        if r.get("status") == "success":
            payload = r.get("data", {}).get("payload", [])

            if not payload:
                console.print(
                    f"  [yellow]⚠ No similar users found for '{lb_username}'[/yellow]"
                )
                return None

            top = payload[0]
            top_username = top.get("user_name")
            top_similarity = top.get("similarity", 0.0)

            console.print(
                f"  [green]✓ Top similar user: [bold]{top_username}[/bold] "
                f"[dim](similarity: {top_similarity:.2%})[/dim][/green]"
            )
            return top_username

        elif r.get("status_code") == 404:
            console.print(
                f"  [yellow]⚠ No similar users data for '{lb_username}' (404)[/yellow]"
            )
            return None
        else:
            console.print(
                f"  [red]✗ similar-users returned HTTP {r.get('status_code')}: "
                f"{r.get('error_msg', 'Unknown error')}[/red]"
            )
            return None

    except Exception as e:
        console.print(f"  [red]✗ similar-users request failed: {e}[/red]")
        return None


def FetchCF():
    console.print(
        Panel.fit(
            "[bold magenta]ListenBrainz CF Recommendation Fetcher[/bold magenta]",
            subtitle="TuneLog · multi-user",
            box=box.DOUBLE_EDGE,
        )
    )
    inserted = 0

    usr_conn = get_db_connection_usr()
    cursor = usr_conn.cursor()
    cursor.execute(
        "SELECT username, LB_token, LB_username FROM user WHERE LB_token IS NOT NULL AND LB_token != ''"
    )
    users = cursor.fetchall()
    usr_conn.close()

    if not users:
        console.print(
            "[yellow]⚠ No users with LB_token found in the database. Exiting.[/yellow]"
        )
        return

    console.print(
        f"[bold green]✓ Found {len(users)} user(s) with LB token[/bold green]\n"
    )

    summary = Table(title="Fetch Summary", box=box.SIMPLE_HEAVY, show_lines=True)
    summary.add_column("DB User", style="cyan", no_wrap=True)
    summary.add_column("LB User", style="magenta", no_wrap=True)
    summary.add_column("Similar User", style="yellow", no_wrap=True)
    summary.add_column("Own CF Saved", style="green", justify="right")
    summary.add_column("Similar CF Saved", style="bright_yellow", justify="right")
    summary.add_column("Status", style="bold")

    for user in users:
        db_username = user["username"]
        raw_token = user["LB_token"]
        stored_lb_un = user["LB_username"]

        console.rule(f"[bold blue]User: {db_username}[/bold blue]")

        console.print("  [dim]→ Decrypting token...[/dim]")
        try:
            decrypted = decrypt_token(raw_token)
        except Exception as e:
            console.print(f"  [red]✗ Token decryption failed: {e}[/red]")
            summary.add_row(
                db_username, "—", "—", "0", "0", "[red]Decrypt failed[/red]"
            )
            continue

        console.print("  [dim]→ Validating token + resolving LB username...[/dim]")
        lb_username = resolve_lb_username(decrypted)

        if not lb_username:
            if stored_lb_un:
                console.print(
                    f"  [yellow]⚠ Falling back to stored LB_username: '{stored_lb_un}'[/yellow]"
                )
                lb_username = stored_lb_un
            else:
                console.print(
                    f"  [red]✗ Could not determine LB username for '{db_username}'. Skipping.[/red]"
                )
                summary.add_row(
                    db_username, "—", "—", "0", "0", "[red]No LB username[/red]"
                )
                continue

        console.print(
            f"  [green]✓ LB username resolved: [bold]{lb_username}[/bold][/green]"
        )

        console.print(
            f"  [dim]→ Fetching own CF recommendations for '{lb_username}'...[/dim]"
        )
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(f"  Fetching CF for {lb_username}...", total=None)
            mbids, cf_last_updated = fetch_cf_recordings(lb_username, decrypted)
            progress.update(task, completed=True)

        own_saved = 0
        if not mbids:
            console.print(f"  [yellow]⚠ No own CF data for '{lb_username}'[/yellow]")
        else:
            console.print(
                f"  [dim]→ Saving {len(mbids)} own CF tracks (db_user='{db_username}')...[/dim]"
            )
            own_saved = save_cf_to_db(db_username, mbids, cf_last_updated)
            console.print(
                f"  [bold green]✓ Saved {own_saved} own CF tracks for '{db_username}'[/bold green]"
            )

        console.print(
            f"  [dim]→ Fetching top similar user for '{lb_username}'...[/dim]"
        )
        similar_username = fetch_top_similar_user(lb_username, decrypted)
        similar_saved = 0

        if not similar_username:
            console.print(
                f"  [yellow]⚠ No similar user found for '{lb_username}', skipping similar CF.[/yellow]"
            )
            summary.add_row(
                db_username,
                lb_username,
                "—",
                str(own_saved),
                "0",
                "[green]✓ Own only[/green]"
                if own_saved
                else "[yellow]No data[/yellow]",
            )
            inserted = own_saved
            continue

        console.print(
            f"  [dim]→ Fetching CF for similar user '{similar_username}'...[/dim]"
        )
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(
                f"  Fetching CF for {similar_username}...", total=None
            )
            sim_mbids, sim_cf_last_updated = fetch_cf_recordings(
                similar_username, decrypted
            )
            progress.update(task, completed=True)

        if not sim_mbids:
            console.print(
                f"  [yellow]⚠ No CF data for similar user '{similar_username}'[/yellow]"
            )
        else:
            sim_db_key = f"{db_username}__sim__{similar_username}"
            console.print(
                f"  [dim]→ Saving {len(sim_mbids)} similar-user CF tracks "
                f"(key='{sim_db_key}')...[/dim]"
            )
            similar_saved = save_cf_to_db(sim_db_key, sim_mbids, sim_cf_last_updated)
            console.print(
                f"  [bold bright_yellow]✓ Saved {similar_saved} similar-user CF tracks "
                f"for '{similar_username}'[/bold bright_yellow]"
            )

        inserted = own_saved + similar_saved
        summary.add_row(
            db_username,
            lb_username,
            similar_username,
            str(own_saved),
            str(similar_saved),
            "[green]✓ OK[/green]",
        )

    console.print()
    console.print(summary)
    console.print(
        Panel.fit("[bold green]CF fetch complete.[/bold green]", box=box.ROUNDED)
    )
    return inserted


def fillMusicBrainzDB():
    lib_conn = get_db_connection_lib()
    lib_cursor = lib_conn.cursor()
    lib_cursor.execute(
        "SELECT DISTINCT recording_mbid FROM LB_CF WHERE recording_mbid IS NOT NULL"
    )
    rows = lib_cursor.fetchall()
    lib_conn.close()

    if not rows:
        console.print("[yellow]⚠ LB_CF is empty — nothing to seed.[/yellow]")
        return 0

    mbids = [row["recording_mbid"] for row in rows]
    console.print(
        Panel.fit(
            f"[bold cyan]Seeding hydration_cache[/bold cyan]\n"
            f"Found [bold]{len(mbids)}[/bold] distinct mbids in LB_CF",
            box=box.ROUNDED,
        )
    )

    mb_conn = get_db_connection_Musicbrainz()
    mb_cursor = mb_conn.cursor()

    mb_cursor.executemany(
        """
        INSERT OR IGNORE INTO hydration_cache (recording_mbid, fetch_status)
        VALUES (?, 'PENDING')
        """,
        [(mbid,) for mbid in mbids],
    )
    inserted = mb_conn.total_changes
    mb_conn.commit()
    mb_conn.close()

    skipped = len(mbids) - inserted
    console.print(f"[bold green]✓ Inserted : {inserted}[/bold green]")
    console.print(f"[dim]  Skipped (already existed): {skipped}[/dim]")

    return inserted


def parse_recording(data: dict) -> dict:
    title = data.get("title")
    duration_ms = data.get("length")

    artist = None
    artist_mbid = None
    credits = data.get("artist-credit", [])
    if credits:
        first = credits[0]
        if isinstance(first, dict):
            a = first.get("artist", {})
            artist = a.get("name")
            artist_mbid = a.get("id")

    album = None
    release_mbid = None
    release_group_mbid = None
    releases = data.get("releases", [])
    if releases:
        rel = releases[0]
        album = rel.get("title")
        release_mbid = rel.get("id")
        rg = rel.get("release-group", {})
        release_group_mbid = rg.get("id") if rg else None

    return {
        "title": title,
        "artist": artist,
        "artist_mbid": artist_mbid,
        "album": album,
        "release_mbid": release_mbid,
        "release_group_mbid": release_group_mbid,
        "duration_ms": duration_ms,
    }


def update_row(conn, mbid: str, parsed: dict | None):
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    if parsed:
        conn.execute(
            """
            UPDATE hydration_cache SET
                title               = ?,
                artist              = ?,
                artist_mbid         = ?,
                album               = ?,
                release_mbid        = ?,
                release_group_mbid  = ?,
                duration_ms         = ?,
                fetch_status        = 'DONE',
                last_synced         = ?
            WHERE recording_mbid = ?
            """,
            (
                parsed["title"],
                parsed["artist"],
                parsed["artist_mbid"],
                parsed["album"],
                parsed["release_mbid"],
                parsed["release_group_mbid"],
                parsed["duration_ms"],
                now,
                mbid,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE hydration_cache SET
                fetch_status = 'FAILED',
                last_synced  = ?
            WHERE recording_mbid = ?
            """,
            (now, mbid),
        )


def handle_mb_success(raw_data: dict, mbid: str):
    console.print(f"  [green]✓[/green] [dim]{mbid[:8]}…[/dim] [white]Fetching…[/white]")
    parsed = parse_recording(raw_data) if raw_data else None
    conn = get_db_connection_Musicbrainz()

    update_row(conn, mbid, parsed)
    conn.commit()
    conn.close()

    if parsed:
        artist = parsed.get("artist") or "Unknown"
        title = parsed.get("title") or "Unknown"
        console.print(
            f"  [green]✓[/green] [dim]{mbid[:8]}…[/dim] [white]{artist} — {title}[/white]"
        )
    else:
        console.print(f"  [red]✗[/red] [dim]{mbid[:8]}…[/dim] [red]Parse FAILED[/red]")


def handle_mb_error(error_msg: str, mbid: str):
    conn = get_db_connection_Musicbrainz()
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    conn.execute(
        "UPDATE hydration_cache SET fetch_status = 'FAILED', last_synced = ? WHERE recording_mbid = ?",
        (now, mbid),
    )
    conn.commit()
    conn.close()

    console.print(
        f"  [red]✗[/red] [dim]{mbid[:8]}…[/dim] [red]API Error: {error_msg}[/red]"
    )


def fetchPendingSongs(limit: int | None = None):
    conn = get_db_connection_Musicbrainz()
    cursor = conn.cursor()

    query = "SELECT recording_mbid FROM hydration_cache WHERE fetch_status = 'PENDING'"
    if limit:
        query += f" LIMIT {limit}"
    cursor.execute(query)
    pending = [row["recording_mbid"] for row in cursor.fetchall()]
    conn.close()

    total = len(pending)
    if not total:
        console.print("[yellow]⚠ No PENDING rows in hydration_cache.[/yellow]")
        return

    console.print(
        Panel.fit(
            f"[bold cyan]MusicBrainz Hydration[/bold cyan]\n"
            f"[white]{total} tasks dispatched to background worker[/white]",
            box=box.DOUBLE_EDGE,
        )
    )

    params = {"inc": "artists releases release-groups", "fmt": "json"}

    for mbid in pending:
        url = f"/recording/{mbid}"
        MB_queue.addBackgroundTask(
            priority=4,
            work=MBWork(
                method="GET",
                endpoint=url,
                params=params,
                on_success=lambda data, m_id=mbid: handle_mb_success(data, m_id),
                on_error=lambda err, m_id=mbid: handle_mb_error(err, m_id),
            ),
        )


def retryFailedSongs(max_retries: int = 3, limit: int | None = None):
    conn = get_db_connection_Musicbrainz()
    cursor = conn.cursor()

    query = "SELECT recording_mbid FROM hydration_cache WHERE fetch_status = 'FAILED'"
    if limit:
        query += f" LIMIT {limit}"
    cursor.execute(query)
    failed_rows = [row["recording_mbid"] for row in cursor.fetchall()]

    total = len(failed_rows)
    if not total:
        console.print("[yellow]⚠ No FAILED rows in hydration_cache.[/yellow]")
        conn.close()
        return

    console.print(
        Panel.fit(
            f"[bold yellow]MusicBrainz — Retry Failed[/bold yellow]\n"
            f"[white]{total} FAILED rows to retry[/white]",
            box=box.DOUBLE_EDGE,
        )
    )

    cursor.executemany(
        "UPDATE hydration_cache SET fetch_status = 'PENDING' WHERE recording_mbid = ?",
        [(mbid,) for mbid in failed_rows],
    )
    conn.commit()
    conn.close()

    params = {"inc": "artists releases release-groups", "fmt": "json"}

    for index, mbid in enumerate(failed_rows, start=1):
        url = f"/recording/{mbid}"
        MB_queue.addBackgroundTask(
            priority=4,
            work=MBWork(
                method="GET",
                endpoint=url,
                params=params,
                on_success=lambda data, m_id=mbid: handle_mb_success(data, m_id),
                on_error=lambda err, m_id=mbid: handle_mb_error(err, m_id),
            ),
        )
