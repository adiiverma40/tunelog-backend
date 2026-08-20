# history_sync.py
import datetime
import time
from collections import defaultdict
from typing import Dict, List, Optional

from rich import box
from rich.console import Console
from rich.panel import Panel

from core.db import get_db_connection
from navidrome.state import tune_config
from Workers.worker_queue import LB_queue, lbWork

from .lb_master import execute_with_retry, load_lb_users
from .local_matching import (
    fallback_stage_1,
    fallback_stage_2,
    fallback_stage_3,
    getSongsFromDb,
)

console = Console()
listenBrainzConf = tune_config.get("listenbrainz", {})
behaviour = tune_config.get("behavioral_scoring", {})


def deep_history_sync(
    pagination: int = 20, lb_user: Dict[str, str] = None
) -> List[dict]:

    if lb_user:
        lb_username = lb_user["lb_username"]
        decrypted_token = lb_user["decrypted_token"]
    else:
        fresh_lb_conf = tune_config.get("listenbrainz", {})
        lb_username = fresh_lb_conf.get("username")
        decrypted_token = None

    if not lb_username:
        console.print(
            "[bold red]deep_history_sync aborted: No LB username available.[/bold red]"
        )
        return []

    console.print(
        Panel.fit(
            f"[bold cyan]Deep History Sync[/bold cyan]\n"
            f"LB user: [magenta]{lb_username}[/magenta]",
            box=box.ROUNDED,
        )
    )

    all_listens = []
    ceiling_ts = None
    endpoint = f"1/user/{lb_username}/listens"

    while True:
        params = {"count": pagination}
        if ceiling_ts is not None:
            params["max_ts"] = ceiling_ts

        work = lbWork(
            method="GET",
            endpoint=endpoint,
            params=params,
            username=lb_username,
            token=decrypted_token,
        )

        try:
            result = LB_queue.addWork(work=work)
        except Exception as e:
            console.print(
                f"[bold red]Deep Sync queue error for '{lb_username}':[/bold red] {e}"
            )
            break

        if result.get("status") != "success":
            console.print(
                f"[bold red]Deep Sync API Error for '{lb_username}':[/bold red] "
                f"{result.get('error_msg')}"
            )
            break

        listens = result.get("data", {}).get("payload", {}).get("listens", [])

        if not listens:
            console.print("[yellow]No more history found.[/yellow]")
            break

        all_listens.extend(listens)
        console.print(
            f"[bold green]  ↳ Fetched {len(all_listens)} total for '{lb_username}'...[/bold green]"
        )

        ceiling_ts = listens[-1]["listened_at"] - 1
        time.sleep(0.5)

        if len(listens) < pagination:
            console.print(
                f"[bold green]✓ All history fetched for '{lb_username}'[/bold green]"
            )
            return all_listens

    return all_listens


def getListenBrainzResponse(lb_user: Dict[str, str]) -> List[dict]:

    lb_username = lb_user["lb_username"]
    decrypted_token = lb_user["decrypted_token"]

    console.print(
        f"[bold yellow]Getting listens for LB user '{lb_username}' "
        f"(DB: '{lb_user['db_username']}')[/bold yellow]"
    )

    last_synced_ts = listenBrainzConf.get("last_synced")

    if not last_synced_ts:
        console.print(
            "[bold red]No last_synced found — running deep history sync[/bold red]"
        )
        return deep_history_sync(100, lb_user)

    since = int(last_synced_ts)
    console.print(
        f"[blue]Syncing since: {datetime.datetime.fromtimestamp(since)}[/blue]"
    )

    endpoint = f"1/user/{lb_username}/listens"
    params = {"min_ts": since, "count": 100}

    work = lbWork(
        method="GET",
        endpoint=endpoint,
        params=params,
        username=lb_username,
        token=decrypted_token,
    )

    try:
        result = LB_queue.addWork(work=work)
    except Exception as e:
        console.print(
            f"[bold red]Error queuing work for '{lb_username}': {e}[/bold red]"
        )
        return []

    if result.get("status") != "success":
        console.print(
            f"[bold red]Error fetching listens for '{lb_username}': "
            f"{result.get('error_msg')}[/bold red]"
        )
        return []

    listens = result.get("data", {}).get("payload", {}).get("listens", [])

    if listens:
        console.print(
            f"[green]✓ Fetched {len(listens)} new tracks for '{lb_username}'.[/green]"
        )
        return listens
    else:
        console.print(
            f"[white]No new tracks for '{lb_username}' since last sync.[/white]"
        )
        return []


def batchSave(matched_records, unmatched_records=None):
    if not matched_records and not unmatched_records:
        console.print("[yellow]No records to save.[/yellow]")
        return

    allowed_users = listenBrainzConf.get("for_users", [])
    if not allowed_users:
        console.print(
            "[bold red]ABORT: No users defined in config ('for_users' is empty).[/bold red]"
        )
        return

    console.print(
        f"[bold green]Preparing {len(matched_records)} tracks to save "
        f"for users: {', '.join(allowed_users)}...[/bold green]"
    )
    default_signal = str(listenBrainzConf.get("treat_data_as", "complete")).lower()
    default_signal = "positive" if default_signal == "complete" else default_signal
    repeat_window_seconds = behaviour.get("repeat_time_window_min", 30) * 60
    dedup_window_seconds = 30 * 60

    percent_map = {"skip": 15.0, "partial": 55.0, "positive": 100.0}
    base_percent = percent_map.get(default_signal, 100.0)

    conn = get_db_connection()
    cursor = conn.cursor()

    matched_records.sort(key=lambda x: x["listen"]["listened_at"])
    song_ids = list({r["song"]["songId"] for r in matched_records})
    chunk_size = 900

    existing_history = defaultdict(list)
    console.print("[cyan]Querying database for recent history...[/cyan]")
    for i in range(0, len(song_ids), chunk_size):
        chunk = song_ids[i : i + chunk_size]
        placeholders = ",".join(["?"] * len(chunk))
        query = f"""
            SELECT song_id, timestamp
            FROM (
                SELECT song_id, timestamp,
                       ROW_NUMBER() OVER (PARTITION BY song_id ORDER BY timestamp DESC) as rn
                FROM listens
                WHERE song_id IN ({placeholders})
            )
            WHERE rn <= 10
        """
        cursor.execute(query, chunk)
        for row in cursor.fetchall():
            dt_obj = datetime.datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S")
            existing_history[row[0]].append(int(dt_obj.timestamp()))

    insert_data = []
    lb_log_data = []
    duplicates_ignored = 0

    console.print("[cyan]Processing and Deduplicating records...[/cyan]")

    for record in matched_records:
        listen = record["listen"]
        song = record["song"]

        song_id = song["songId"]
        listened_at = listen["listened_at"]
        title = song.get("title", "Unknown")
        artist = song.get("artist", "Unknown")
        album = song.get("album", "")
        human_time = datetime.datetime.utcfromtimestamp(listened_at).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        console.print(f"[dim]Analyzing: '{title}' by {artist} ({human_time})[/dim]")

        history = existing_history[song_id]
        is_duplicate = any(
            abs(listened_at - ts) <= dedup_window_seconds for ts in history
        )

        if is_duplicate:
            duplicates_ignored += 1
            console.print("[bold yellow] ↳ ⚠ Duplicate Ignored[/bold yellow]")
            lb_log_data.append(
                (
                    song_id,
                    title,
                    artist,
                    album,
                    default_signal,
                    "duplicate",
                    None,
                    human_time,
                )
            )
            continue

        current_signal = default_signal
        current_percent = base_percent

        past_plays_before = [ts for ts in history if ts < listened_at]
        if past_plays_before:
            last_played_ts = max(past_plays_before)
            time_diff = listened_at - last_played_ts
            if time_diff <= repeat_window_seconds:
                current_signal = "repeat"
                current_percent = 100.0
                console.print(
                    f"[bold blue] ↳ ↻ Flagged as Repeat (Gap: {int(time_diff / 60)}m)[/bold blue]"
                )

        existing_history[song_id].append(listened_at)
        dt_string = datetime.datetime.utcfromtimestamp(listened_at).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        metadata = listen.get("track_metadata", {}).get("additional_info", {})
        duration_ms = metadata.get("duration_ms", 0)
        duration_sec = int(duration_ms / 1000) if duration_ms else 0

        for username in allowed_users:
            insert_data.append(
                (
                    song_id,
                    title,
                    artist,
                    album,
                    song.get("genre", ""),
                    duration_sec,
                    1,
                    current_percent,
                    current_signal,
                    dt_string,
                    username,
                )
            )

        lb_log_data.append(
            (song_id, title, artist, album, current_signal, "matched", None, dt_string)
        )
        console.print(
            f"[bold green] ↳ ✔ Queued for insertion ({current_signal})[/bold green]"
        )

    if unmatched_records:
        console.print(
            f"[bold red]Logging {len(unmatched_records)} unmatched tracks "
            f"to listenbrainz table...[/bold red]"
        )
        for listen in unmatched_records:
            metadata = listen.get("track_metadata", {})
            raw_title = metadata.get("track_name", "")
            raw_artist = metadata.get("artist_name", "")
            raw_album = metadata.get("release_name", "")
            listened_at = listen.get("listened_at", 0)
            dt_string = datetime.datetime.utcfromtimestamp(listened_at).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if raw_title and raw_artist:
                lb_log_data.append(
                    (
                        None,
                        raw_title,
                        raw_artist,
                        raw_album,
                        None,
                        "unmatched",
                        None,
                        dt_string,
                    )
                )
            else:
                fallback_label = raw_title or raw_artist or "unknown"
                lb_log_data.append(
                    (
                        None,
                        fallback_label,
                        None,
                        None,
                        None,
                        "unmatched",
                        None,
                        dt_string,
                    )
                )

    console.print("[cyan]Attempting to write to database...[/cyan]")

    if insert_data:
        ok = execute_with_retry(
            cursor,
            conn,
            """
            INSERT INTO listens
                (song_id, title, artist, album, genre, duration, played,
                 percent_played, signal, timestamp, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            insert_data,
        )
        if ok:
            unique_tracks = len(insert_data) // len(allowed_users)
            console.print(
                f"[bold green]✔ Successfully saved {unique_tracks} unique tracks "
                f"({len(insert_data)} total plays)![/bold green]"
            )
        else:
            console.print("[bold red]✖ Database Save Failed after retries.[/bold red]")
    else:
        console.print(
            f"[bold yellow]Total Duplicates Ignored: {duplicates_ignored}[/bold yellow]"
        )
        console.print(
            "[bold red]No new unique tracks to save to the database.[/bold red]"
        )

    if lb_log_data:
        ok = execute_with_retry(
            cursor,
            conn,
            """
            INSERT INTO listenbrainz
                (song_id, title, artist, album, signal, tag, comment, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            lb_log_data,
        )
        if ok:
            console.print(
                f"[bold cyan]✔ Logged {len(lb_log_data)} entries to listenbrainz table.[/bold cyan]"
            )
        else:
            console.print(
                "[bold red]✖ ListenBrainz log write failed after retries.[/bold red]"
            )

    conn.close()


def run_history_sync() -> Optional[int]:
    lb_users = load_lb_users()
    if not lb_users:
        return None

    songs_list = getSongsFromDb()
    exact_match_dict = {
        f"{str(s['artist']).lower().strip()} - {str(s['title']).lower().strip()}": s
        for s in songs_list
    }

    global_newest_ts = None

    for lb_user in lb_users:
        console.rule(
            f"[bold blue]Processing: {lb_user['db_username']} "
            f"→ LB: {lb_user['lb_username']}[/bold blue]"
        )

        response_songs = getListenBrainzResponse(lb_user)
        if not response_songs:
            continue

        newest_ts = response_songs[0].get("listened_at")
        if newest_ts and (global_newest_ts is None or newest_ts > global_newest_ts):
            global_newest_ts = newest_ts

        unmatched_listens, matched_records = [], []

        for listen in response_songs:
            metadata = listen.get("track_metadata", {})
            lookup = f"{str(metadata.get('artist_name', '')).lower().strip()} - {str(metadata.get('track_name', '')).lower().strip()}"
            if lookup in exact_match_dict:
                matched_records.append(
                    {"listen": listen, "song": exact_match_dict[lookup]}
                )
            else:
                unmatched_listens.append(listen)

        final_garbage = []
        if unmatched_listens:
            console.print(
                f"[bold yellow]Sending {len(unmatched_listens)} to Fallback Pipeline...[/bold yellow]"
            )
            remaining_1, artist_index = fallback_stage_1(
                unmatched_listens, songs_list, matched_records
            )
            remaining_2 = fallback_stage_2(remaining_1, artist_index, matched_records)
            if remaining_2:
                final_garbage = fallback_stage_3(
                    remaining_2, songs_list, matched_records
                )
                console.print(
                    f"[bold red]True Misses (Ignored): {len(final_garbage)}[/bold red]"
                )

        batchSave(matched_records, unmatched_records=final_garbage)

    return global_newest_ts


def fuzzyMatchingSong() -> Optional[int]:

    console.print(
        Panel.fit(
            "[bold magenta]ListenBrainz Sync — Multi-User[/bold magenta]",
            box=box.DOUBLE_EDGE,
        )
    )

    lb_users = load_lb_users()

    if not lb_users:
        console.print("[bold red]No valid LB users found. Aborting.[/bold red]")

        return None

    songs_list = getSongsFromDb()

    exact_match_dict = {}

    for s in songs_list:
        artist = str(s["artist"]).lower().strip() if s["artist"] else ""

        title = str(s["title"]).lower().strip() if s["title"] else ""

        exact_match_dict[f"{artist} - {title}"] = s

    global_newest_ts = None

    for lb_user in lb_users:
        console.rule(
            f"[bold blue]Processing: {lb_user['db_username']} "
            f"→ LB: {lb_user['lb_username']}[/bold blue]"
        )

        response_songs = getListenBrainzResponse(lb_user)

        if not response_songs:
            console.print(
                f"[yellow]No listens returned for '{lb_user['lb_username']}'. Skipping.[/yellow]"
            )

            continue

        newest_ts = response_songs[0].get("listened_at")

        if newest_ts and (global_newest_ts is None or newest_ts > global_newest_ts):
            global_newest_ts = newest_ts

        unmatched_listens = []

        matched_records = []

        console.print(f"[cyan]Processing {len(response_songs)} listens...[/cyan]")

        for listen in response_songs:
            metadata = listen.get("track_metadata", {})

            lb_title = str(metadata.get("track_name", "")).lower().strip()

            lb_artist = str(metadata.get("artist_name", "")).lower().strip()

            matched_song = exact_match_dict.get(f"{lb_artist} - {lb_title}")

            if matched_song:
                matched_records.append({"listen": listen, "song": matched_song})

            else:
                unmatched_listens.append(listen)

        console.print(
            f"[bold green]Direct Matches: {len(matched_records)}/{len(response_songs)}[/bold green]"
        )

        final_garbage = []

        if unmatched_listens:
            console.print(
                f"[bold yellow]Sending {len(unmatched_listens)} to Fallback Pipeline...[/bold yellow]"
            )

            remaining_1, artist_index = fallback_stage_1(
                unmatched_listens, songs_list, matched_records
            )

            remaining_2 = fallback_stage_2(remaining_1, artist_index, matched_records)

            if remaining_2:
                final_garbage = fallback_stage_3(
                    remaining_2, songs_list, matched_records
                )

                console.print(
                    f"[bold red]True Misses (Ignored): {len(final_garbage)}[/bold red]"
                )

        batchSave(matched_records, unmatched_records=final_garbage)

        # songScoringCorn()

    return global_newest_ts
