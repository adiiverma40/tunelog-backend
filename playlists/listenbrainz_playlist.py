from typing import Dict, List, Tuple

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from core.db import DB_PATH_LOG, DB_PATH_MB, get_db_connection_lib
from navidrome.state import automation_config

from .base_playlist import analyze_user_ratios

lb_cf_config = automation_config.get("cf_playlist_config", {})

_console = Console()

_SIGNAL_META = {
    "cf_heard": ("Heard", "bright_cyan"),
    "cf_unheard": ("Unheard", "bright_magenta"),
    "cf_unheard_backfill": ("Unheard Backfill", "magenta"),
    "cf_blend_fallback": ("Blend Fallback", "yellow"),
}


def _log_cf_table(
    song_ids: list,
    song_signals: dict,
    candidates_by_id: dict,
    playlist_name: str,
    new_heard_score: float,
    new_unheard_score: float,
):
    table = Table(
        title=f"[bold orange1]LB CF — {playlist_name}[/]",
        box=box.SIMPLE_HEAD,
        show_lines=False,
        header_style="bold dim",
        title_justify="left",
        min_width=72,
    )
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Title", min_width=28)
    table.add_column("Type", width=18)
    table.add_column("Score", style="dim", width=10, justify="right")

    signal_counts: dict = {}

    for i, sid in enumerate(song_ids, 1):
        signal = song_signals.get(sid, "unknown")
        label, colour = _SIGNAL_META.get(signal, (signal, "white"))
        signal_counts[label] = signal_counts.get(label, 0) + 1

        song = candidates_by_id.get(sid)
        title = (song["title"] if song else sid) or sid
        score = song["score"] if song else None

        table.add_row(
            str(i),
            Text(title[:40] + ("…" if len(title) > 40 else ""), style="white"),
            Text(f"● {label}", style=colour),
            Text(f"{score:.4f}" if score is not None else "—", style="dim"),
        )

    _console.print(table)

    summary = "  ".join(
        f"[{_SIGNAL_META.get(k.lower().replace(' ', '_'), ('', 'white'))[1]}]{k}[/] [dim]{v}[/]"
        for k, v in signal_counts.items()
    )
    _console.print(
        f"  [dim]Total[/] [bold white]{len(song_ids)}[/]  |  {summary}\n"
        f"  [dim]Heard cursor  →[/] [bright_cyan]{new_heard_score:.4f}[/]"
        f"   [dim]Unheard cursor →[/] [bright_magenta]{new_unheard_score:.4f}[/]\n"
    )


def filter_pool_by_genre(
    pool: list, target_size: int, user_id: str, history_dict: dict, alias_to_cat: dict
) -> list:
    cat_counts, _ = analyze_user_ratios(user_id, history_dict, alias_to_cat)
    total_listens = sum(cat_counts.values())

    if total_listens == 0:
        return pool[:target_size]

    target_counts = {
        cat: max(1, round((count / total_listens) * target_size))
        for cat, count in cat_counts.items()
    }

    selected = []
    remaining_pool = []

    for song in pool:
        raw_genres = song.get("genre", "")
        clean_genres = (
            [g.strip().lower() for g in raw_genres.split(",") if g.strip()]
            if raw_genres
            else []
        )
        mapped_cats = {alias_to_cat.get(g, g) for g in clean_genres} or {"unknown"}

        matches = [c for c in mapped_cats if target_counts.get(c, 0) > 0]
        if matches:
            selected.append(song)
            for m in matches:
                target_counts[m] -= 1
        else:
            remaining_pool.append(song)

        if len(selected) >= target_size:
            break

    if len(selected) < target_size:
        selected.extend(remaining_pool[: target_size - len(selected)])

    return selected


def fetch_cf_batch(
    cursor,
    user_id: str = "default",
) -> list:
    skip_song_if_less_score: bool = lb_cf_config.get("skip_song_if_less_score", True)
    skip_timeout_song: bool = lb_cf_config.get("skip_timeout_song", True)
    try:
        cursor.execute(f"ATTACH DATABASE '{DB_PATH_LOG}' AS log_db")
    except Exception as e:
        pass

    query = """
        SELECT
            lb.recording_mbid,
            hc.nvid          AS song_id,
            lb.score,
            lb.latest_listened_at,
            lib.genre,
            lib.title
        FROM LB_CF lb
        JOIN mb_db.hydration_cache hc ON lb.recording_mbid = hc.recording_mbid
        JOIN library lib              ON hc.nvid = lib.song_id
        WHERE hc.nvid IS NOT NULL
    """

    if skip_timeout_song:
        query += f" AND hc.nvid NOT IN (SELECT song_id FROM log_db.timeout WHERE user_id = '{user_id}')"

    if skip_song_if_less_score:
        query += (
            " AND hc.nvid NOT IN (SELECT song_id FROM log_db.listens WHERE score < 0)"
        )
    query += """
        ORDER BY lb.score DESC
        LIMIT 500
    """
    cursor.execute(query)
    results = [dict(row) for row in cursor.fetchall()]
    try:
        cursor.execute("DETACH DATABASE log_db")
    except Exception:
        pass

    return results


# Why did i make it that cf config is entered during calling the function?
#


def build_LB_CF_playlist(
    user_id: str,
    cf_config: dict,
    history_dict: dict,
    alias_to_cat: dict,
    standard_scores: dict,
) -> Tuple[List[str], Dict[str, str], float, float]:

    if not cf_config :
        cf_config = lb_cf_config

    size = cf_config.get("size", 50)
    target_heard = cf_config.get("heard", 25)
    target_unheard = cf_config.get("unheard", 25)

    unheard_genre_inj = cf_config.get("unheard_genre_injection", True)
    heard_genre_inj = cf_config.get("heard_genre_injection", False)
    backfill_unheard = cf_config.get("backfill_unheard_song", True)
    use_blend = cf_config.get("use_blend", True)
    fallback_score = cf_config.get("fallbackScore", True)
    playlist_name = cf_config.get("Name", "Listenbrainz Playlist")

    heard_last_score = cf_config.get("heard_last_score", 0.0)
    unheard_last_score = cf_config.get("unheard_last_score", 0.0)

    conn = get_db_connection_lib()
    cursor = conn.cursor()

    try:
        cursor.execute(f"ATTACH DATABASE '{DB_PATH_MB}' AS mb_db")
    except Exception as e:
        _console.print(f"[red][LB_CF] Failed to attach musicbrainz database: {e}[/]")
        return [], {}, heard_last_score, unheard_last_score

    raw_candidates = fetch_cf_batch(cursor, user_id)

    all_heard_candidates = [c for c in raw_candidates if c["latest_listened_at"]]
    all_unheard_candidates = [c for c in raw_candidates if not c["latest_listened_at"]]

    _console.print(
        f"[dim][LB_CF] Candidate pool —[/] "
        f"[bright_cyan]heard: {len(all_heard_candidates)}[/]  "
        f"[bright_magenta]unheard: {len(all_unheard_candidates)}[/]"
    )

    def apply_score_cursor(pool: list, last_score: float) -> list:
        if last_score <= 0:
            return pool
        return [c for c in pool if c["score"] < last_score]

    heard_candidates = apply_score_cursor(all_heard_candidates, heard_last_score)
    unheard_candidates = apply_score_cursor(all_unheard_candidates, unheard_last_score)

    _console.print(
        f"[dim][LB_CF] After cursor —[/] "
        f"[bright_cyan]heard: {len(heard_candidates)}[/] [dim](cursor {heard_last_score:.4f})[/]  "
        f"[bright_magenta]unheard: {len(unheard_candidates)}[/] [dim](cursor {unheard_last_score:.4f})[/]"
    )

    heard_wrapped = False
    unheard_wrapped = False

    if len(heard_candidates) < target_heard and fallback_score:
        _console.print(
            "[yellow][LB_CF] Heard bucket ran dry — wrapping to top scores.[/]"
        )
        existing = {c["song_id"] for c in heard_candidates}
        for c in all_heard_candidates:
            if c["song_id"] not in existing:
                heard_candidates.append(c)
                existing.add(c["song_id"])
        heard_wrapped = True

    if len(unheard_candidates) < target_unheard and fallback_score:
        _console.print(
            "[yellow][LB_CF] Unheard bucket ran dry — wrapping to top scores.[/]"
        )
        existing = {c["song_id"] for c in unheard_candidates}
        for c in all_unheard_candidates:
            if c["song_id"] not in existing:
                unheard_candidates.append(c)
                existing.add(c["song_id"])
        unheard_wrapped = True

    cursor.execute("DETACH DATABASE mb_db")
    conn.close()

    if heard_genre_inj:
        _console.print("[dim][LB_CF] Applying genre injection to heard pool.[/]")
        heard_candidates = filter_pool_by_genre(
            heard_candidates, target_heard, user_id, history_dict, alias_to_cat
        )

    if unheard_genre_inj:
        _console.print("[dim][LB_CF] Applying genre injection to unheard pool.[/]")
        unheard_req = size if backfill_unheard else target_unheard
        unheard_candidates = filter_pool_by_genre(
            unheard_candidates, unheard_req, user_id, history_dict, alias_to_cat
        )

    final_ids = []
    song_signals = {}
    seen = set()

    used_heard_candidates = []
    used_unheard_candidates = []

    def add_song(song: dict, signal: str, bucket: str):
        sid = song["song_id"]
        if sid not in seen:
            final_ids.append(sid)
            song_signals[sid] = signal
            seen.add(sid)
            if bucket == "heard":
                used_heard_candidates.append(song)
            elif bucket == "unheard":
                used_unheard_candidates.append(song)

    for song in heard_candidates:
        if (
            len(
                [s for s in final_ids if song_signals.get(s, "").startswith("cf_heard")]
            )
            >= target_heard
        ):
            break
        add_song(song, "cf_heard", "heard")

    for song in unheard_candidates:
        if (
            len(
                [
                    s
                    for s in final_ids
                    if song_signals.get(s, "") in ("cf_unheard", "cf_unheard_backfill")
                ]
            )
            >= target_unheard
        ):
            break
        add_song(song, "cf_unheard", "unheard")

    if backfill_unheard and len(final_ids) < size:
        for song in unheard_candidates:
            if len(final_ids) >= size:
                break
            add_song(song, "cf_unheard_backfill", "unheard")

    if use_blend and len(final_ids) < size:
        needed = size - len(final_ids)
        _console.print(
            f"[yellow][LB_CF] Short on CF tracks ({len(final_ids)}/{size}). "
            f"Pulling {needed} from local blend scores.[/]"
        )
        fallback_songs = [
            sid
            for sid, data in sorted(
                standard_scores.items(), key=lambda x: x[1]["score"], reverse=True
            )
            if sid not in seen and data["score"] >= 0
        ][:needed]

        for sid in fallback_songs:
            if sid not in seen:
                final_ids.append(sid)
                song_signals[sid] = "cf_blend_fallback"
                seen.add(sid)

    new_heard_score = 0.0
    if used_heard_candidates:
        new_heard_score = min(c["score"] for c in used_heard_candidates)
    if heard_wrapped:
        new_heard_score = 0.0

    new_unheard_score = 0.0
    if used_unheard_candidates:
        new_unheard_score = min(c["score"] for c in used_unheard_candidates)
    if unheard_wrapped:
        new_unheard_score = 0.0

    _log_cf_table(
        song_ids=final_ids[:size],
        song_signals=song_signals,
        candidates_by_id={c["song_id"]: c for c in raw_candidates},
        playlist_name=playlist_name,
        new_heard_score=new_heard_score,
        new_unheard_score=new_unheard_score,
    )

    return final_ids[:size], song_signals, new_heard_score, new_unheard_score
