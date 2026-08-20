
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz, process
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from core.db import get_db_connection_lib, get_db_connection_Musicbrainz

console = Console()
BATCH_SIZE = 100


@dataclass
class _HydrationTrack:
    title: str | None
    artist: str | None
    album: str | None
    mbid: str

    def model_dump(self) -> dict:
        return {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "mbid": self.mbid,
        }


def getSongsFromDb() -> List[Dict]:
    conn = get_db_connection_lib()
    cursor = conn.cursor()
    songs = cursor.execute(
        "SELECT song_id, title, artist, album, artistJSON, genre FROM library"
    ).fetchall()
    conn.close()

    songDict = []
    for row in songs:
        parsed_artists = [row[2]] if row[2] else []
        try:
            if row[4]:
                ajson = json.loads(row[4])
                parsed_artists.extend([a["name"] for a in ajson if "name" in a])
        except Exception:
            pass
        clean_artists = list(set([str(a).lower().strip() for a in parsed_artists if a]))
        songDict.append(
            {
                "songId": row[0],
                "title": row[1],
                "artist": row[2],
                "album": row[3],
                "all_artists": clean_artists,
                "genre": (str(row[5]) if len(row) > 5 and row[5] else ""),
            }
        )
    return songDict


def fallback_stage_1(unmatched_listens, songs_list, matched_records):
    console.print(
        "[cyan]Building Artist, Album, and Title indexes for fallback...[/cyan]"
    )
    artist_dict = defaultdict(list)
    album_dict = defaultdict(list)
    title_dict = defaultdict(list)

    for s in songs_list:
        db_artist = str(s.get("artist", "")).lower().strip()
        db_album = str(s.get("album", "")).lower().strip()
        db_title = str(s.get("title", "")).lower().strip()
        if db_artist:
            artist_dict[db_artist].append(s)
        if db_album:
            album_dict[db_album].append(s)
        if db_title:
            title_dict[db_title].append(s)

    console.print(
        "[bold yellow]Starting Fallback 1: Targeted Fuzzy Matching[/bold yellow]"
    )
    deep_unmatched = []

    for unmatched in unmatched_listens:
        metadata = unmatched.get("track_metadata", {})
        um_title = str(metadata.get("track_name", "")).lower().strip()
        um_artist = str(metadata.get("artist_name", "")).lower().strip()
        um_album = str(metadata.get("release_name", "")).lower().strip()

        candidates = []
        lookup_type = ""

        if um_artist and um_artist in artist_dict:
            candidates = artist_dict[um_artist]
            lookup_type = "Artist"
        elif um_album and um_album in album_dict:
            candidates = album_dict[um_album]
            lookup_type = "Album"
        elif um_title and um_title in title_dict:
            candidates = title_dict[um_title]
            lookup_type = "Title"

        if candidates:
            choices = {s["songId"]: s["title"] for s in candidates}
            result = process.extractOne(um_title, choices, scorer=fuzz.token_set_ratio)
            if result and result[1] >= 85.0:
                matched_id = result[2]
                matched_song = next(
                    (s for s in candidates if s["songId"] == matched_id), None
                )
                matched_records.append({"listen": unmatched, "song": matched_song})
                console.print(
                    f"[bold blue]✔ FUZZY ({result[1]:.1f}% via {lookup_type}):[/bold blue] "
                    f"'{um_title}' -> [dim]ID: {matched_id}[/dim]"
                )
            else:
                deep_unmatched.append(unmatched)
        else:
            deep_unmatched.append(unmatched)

    return deep_unmatched, artist_dict


def fallback_stage_2(unmatched_listens, artist_dict, matched_records):
    console.print(
        "[bold yellow]Starting Fallback 2: Strict Artist -> Title Dictionary Search[/bold yellow]"
    )
    known_artists = list(artist_dict.keys())
    final_misses = []

    for unmatched in unmatched_listens:
        metadata = unmatched.get("track_metadata", {})
        um_title = str(metadata.get("track_name", "")).lower().strip()
        um_artist = str(metadata.get("artist_name", "")).lower().strip()

        if not um_artist or not um_title:
            final_misses.append(unmatched)
            continue

        artist_matches = process.extract(
            um_artist, known_artists, scorer=fuzz.token_set_ratio, limit=10
        )
        title_pool = {}
        full_songs_pool = []

        for match in artist_matches:
            if match[1] >= 80.0:
                for song in artist_dict[match[0]]:
                    title_pool[song["songId"]] = str(song["title"]).lower().strip()
                    full_songs_pool.append(song)

        if title_pool:
            title_match = process.extractOne(
                um_title, title_pool, scorer=fuzz.token_set_ratio
            )
            if title_match and title_match[1] >= 85.0:
                matched_id = title_match[2]
                matched_song = next(
                    (s for s in full_songs_pool if s["songId"] == matched_id), None
                )
                matched_records.append({"listen": unmatched, "song": matched_song})
                console.print(
                    f"[bold magenta]✔ STAGE 2 MATCH ({title_match[1]:.1f}%):[/bold magenta] "
                    f"'{um_title}' -> [dim]ID: {matched_id}[/dim]"
                )
                continue

        final_misses.append(unmatched)
    return final_misses


def fallback_stage_3(unmatched_listens, songs_list, matched_records):
    console.print(
        "[bold yellow]Starting Fallback 3: Global Title -> Multi-Artist Verification[/bold yellow]"
    )
    absolute_misses = []
    title_dict = defaultdict(list)
    all_titles_pool = {}
    song_by_id = {}

    for s in songs_list:
        db_title = str(s.get("title", "")).lower().strip()
        song_id = s["songId"]
        if db_title:
            title_dict[db_title].append(s)
            all_titles_pool[song_id] = db_title
        song_by_id[song_id] = s

    for unmatched in unmatched_listens:
        metadata = unmatched.get("track_metadata", {})
        um_title = str(metadata.get("track_name", "")).lower().strip()
        um_artist = str(metadata.get("artist_name", "")).lower().strip()

        if not um_artist or not um_title:
            absolute_misses.append(unmatched)
            continue

        matched = False

        if um_title in title_dict:
            for candidate in title_dict[um_title]:
                artist_match = process.extractOne(
                    um_artist, candidate["all_artists"], scorer=fuzz.token_set_ratio
                )
                if artist_match and artist_match[1] >= 80.0:
                    matched_records.append({"listen": unmatched, "song": candidate})
                    console.print(
                        f"[bold magenta]✔ STAGE 3 MATCH (Exact Title):[/bold magenta] "
                        f"'{um_title}' -> [dim]ID: {candidate['songId']}[/dim]"
                    )
                    matched = True
                    break

        if matched:
            continue

        title_matches = process.extract(
            um_title, all_titles_pool, scorer=fuzz.token_set_ratio, limit=5
        )
        for t_match in title_matches:
            if t_match[1] >= 85.0:
                candidate_id = t_match[2]
                candidate = song_by_id[candidate_id]
                artist_match = process.extractOne(
                    um_artist, candidate["all_artists"], scorer=fuzz.token_set_ratio
                )
                if artist_match and artist_match[1] >= 80.0:
                    matched_records.append({"listen": unmatched, "song": candidate})
                    console.print(
                        f"[bold magenta]✔ STAGE 3 MATCH (Fuzzy Title {t_match[1]:.1f}%):[/bold magenta] "
                        f"'{t_match[0]}' -> [dim]ID: {candidate_id}[/dim]"
                    )
                    matched = True
                    break

        if not matched:
            absolute_misses.append(unmatched)

    return absolute_misses


def batchMatchNavidromeTracks(tracks: List[Any]) -> tuple[List[Dict[str, Any]], int]:
    songs_list = getSongsFromDb()
    exact_match_dict = {}
    for s in songs_list:
        db_artist = str(s.get("artist", "")).lower().strip()
        db_title = str(s.get("title", "")).lower().strip()
        exact_match_dict[f"{db_artist} - {db_title}"] = s

    matched_results = {}
    unmatched_listens = []

    for idx, track in enumerate(tracks):
        um_title = str(track.title or "").strip()
        um_artist = str(track.artist or "").strip()
        um_album = str(track.album or "").strip()
        lookup_key = f"{um_artist.lower()} - {um_title.lower()}"
        exact = exact_match_dict.get(lookup_key)

        if exact:
            matched_results[idx] = {
                "navidrome_id": exact["songId"],
                "matched_name": f"{exact.get('artist', '')} - {exact.get('title', '')}",
                "match_type": "exact",
            }
        else:
            fake_listen = {
                "_original_index": idx,
                "track_metadata": {
                    "track_name": um_title,
                    "artist_name": um_artist,
                    "release_name": um_album,
                },
            }
            unmatched_listens.append(fake_listen)

    matched_records = []
    if unmatched_listens:
        remaining_1, artist_index = fallback_stage_1(
            unmatched_listens, songs_list, matched_records
        )
        remaining_2 = fallback_stage_2(remaining_1, artist_index, matched_records)
        fallback_stage_3(remaining_2, songs_list, matched_records)

    for record in matched_records:
        listen = record["listen"]
        song = record["song"]
        idx = listen["_original_index"]
        matched_results[idx] = {
            "navidrome_id": song["songId"],
            "matched_name": f"{song.get('artist', '')} - {song.get('title', '')}",
            "match_type": "fallback",
        }

    output_tracks = []
    matched_count = 0

    for idx, track in enumerate(tracks):
        track_data = track.model_dump()
        match_info = matched_results.get(idx)

        if match_info:
            track_data["navidrome_id"] = match_info["navidrome_id"]
            track_data["matched_name"] = match_info["matched_name"]
            matched_count += 1
            console.print(
                f"MATCHED [{match_info['match_type']}]: {match_info['matched_name']}"
            )

        output_tracks.append(track_data)

    return output_tracks, matched_count


def match_and_update_nvid(batch_size: int = BATCH_SIZE):
    conn = get_db_connection_Musicbrainz()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT recording_mbid, title, artist, album
        FROM   hydration_cache
        WHERE  fetch_status = 'DONE'
          AND  nvid IS NULL
          AND  title  IS NOT NULL
          AND  artist IS NOT NULL
    """)
    rows = cursor.fetchall()

    if not rows:
        console.print(
            "[yellow]⚠ No DONE rows without nvid found in hydration_cache.[/yellow]"
        )
        conn.close()
        return

    total = len(rows)
    console.print(
        Panel.fit(
            f"[bold cyan]Navidrome ID Matching[/bold cyan]\n"
            f"[white]{total} hydrated tracks to match[/white]",
            box=box.DOUBLE_EDGE,
        )
    )

    total_matched = 0
    total_unmatched = 0
    batch_num = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        refresh_per_second=4,
    ) as progress:
        task = progress.add_task("Matching batches…", total=total)

        for offset in range(0, total, batch_size):
            batch_rows = rows[offset : offset + batch_size]
            batch_num += 1

            tracks: List[_HydrationTrack] = [
                _HydrationTrack(
                    title=row["title"],
                    artist=row["artist"],
                    album=row["album"],
                    mbid=row["recording_mbid"],
                )
                for row in batch_rows
            ]

            console.print(
                f"\n[bold blue]Batch {batch_num} "
                f"({offset + 1}–{min(offset + batch_size, total)} of {total})[/bold blue]"
            )

            output_tracks, matched_count = batchMatchNavidromeTracks(tracks)

            unmatched_count = len(batch_rows) - matched_count
            total_matched += matched_count
            total_unmatched += unmatched_count

            console.print(
                f"  [green]✓ Matched : {matched_count}[/green]  "
                f"[red]✗ Unmatched : {unmatched_count}[/red]"
            )

            updates = []
            for track_data in output_tracks:
                navidrome_id = track_data.get("navidrome_id")
                mbid = track_data.get("mbid")
                if navidrome_id and mbid:
                    updates.append((navidrome_id, mbid))

            if updates:
                cursor.executemany(
                    "UPDATE hydration_cache SET nvid = ? WHERE recording_mbid = ?",
                    updates,
                )
                conn.commit()
                console.print(f"  [cyan]↳ Wrote {len(updates)} nvid(s) to DB.[/cyan]")

            progress.advance(task, len(batch_rows))

    conn.close()

    console.print(
        Panel.fit(
            f"[bold green]✓ Matched   : {total_matched}[/bold green]\n"
            f"[bold red]✗ Unmatched : {total_unmatched}[/bold red]\n"
            f"[dim]Total processed : {total}[/dim]",
            box=box.ROUNDED,
        )
    )

