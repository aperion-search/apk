import json
import os
import platform
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import flet as ft
import flet_audio as fa

# ================= REPOSITORY CONFIG =================
GITHUB_USER = "aperion-search"
GITHUB_REPO = "my-audio-server"
REPO_API = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents"
AUDIO_EXT_RE = re.compile(r"\.(mp3|wav|ogg|m4a|flac|aac)$", re.IGNORECASE)
# =====================================================

# Luxury Glass & Rounded Palette
BG_VOID = "#050807"
BG_LUX = "#0b1411"
GLASS_CARD = "#152420d8"
GLASS_ELEVATED = "#1d312ce8"
GLASS_DOCK = "#101c19f0"
GOLD = "#e2be52"
GOLD_SOFT = "#362d14"
GLASS_BORDER = "#35524a80"
TEXT_MAIN = "#f6f8f7"
TEXT_MUTED = "#98aca5"


def format_ms(ms: int | float | None) -> str:
    if not ms or ms < 0:
        return "0:00"
    total_sec = int(ms // 1000)
    mins = total_sec // 60
    secs = total_sec % 60
    return f"{mins}:{secs:02d}"


def http_get_json(url: str) -> list | dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SacredInstrumentalsPythonApp/2.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download_bytes(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SacredInstrumentalsPythonApp/2.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def resolve_storage_dirs(page: ft.Page) -> tuple[str, str]:
    """Resolve app state directory and platform root audio folder."""
    sys_name = platform.system().lower()
    home_dir = os.path.expanduser("~")
    state_dir = os.path.join(home_dir, ".sacred_instrumentals_py")

    candidate_roots: list[str] = []
    if page.platform == ft.PagePlatform.ANDROID or "android" in sys_name:
        candidate_roots.extend([
            "/storage/emulated/0/SacredInstrumentals",
            "/sdcard/SacredInstrumentals",
        ])
    elif page.platform == ft.PagePlatform.IOS or "darwin" in sys_name:
        candidate_roots.extend([
            os.path.join(home_dir, "Documents", "SacredInstrumentals"),
            os.path.join(home_dir, "SacredInstrumentals"),
        ])
    else:
        candidate_roots.append(os.path.join(state_dir, "SacredInstrumentals"))

    audio_root = os.path.join(state_dir, "SacredInstrumentals")
    for root in candidate_roots:
        try:
            os.makedirs(root, exist_ok=True)
            test_path = os.path.join(root, ".write_test")
            with open(test_path, "w", encoding="utf-8") as tf:
                tf.write("ok")
            os.remove(test_path)
            audio_root = root
            break
        except Exception:
            continue

    os.makedirs(state_dir, exist_ok=True)
    os.makedirs(audio_root, exist_ok=True)
    return state_dir, audio_root


def main(page: ft.Page):
    page.title = "Sacred Instrumentals"
    page.bgcolor = BG_VOID
    page.padding = 0
    page.spacing = 0
    page.theme_mode = ft.ThemeMode.DARK

    state_dir, audio_root_dir = resolve_storage_dirs(page)
    state_file = os.path.join(state_dir, "state.json")

    def load_state() -> dict:
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "catalog": [],
            "favorites": [],
            "playlists": {"Daily Listening": []},
        }

    def save_state():
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "catalog": state["catalog"],
                    "favorites": state["favorites"],
                    "playlists": state["playlists"],
                },
                f,
                indent=2,
            )

    state = load_state()

    # Runtime UI & playback state
    current_view = "explore"
    selected_category = "All"
    search_query = ""
    active_queue: list[dict] = []
    current_index = -1
    current_track: dict | None = None
    is_playing = False
    is_shuffle = False
    repeat_mode = "all"  # "off" | "all" | "one"
    duration_ms = 0
    position_ms = 0
    seeking = False

    def offline_file_path(track_name: str) -> str:
        safe_name = os.path.basename(track_name)
        return os.path.join(audio_root_dir, safe_name)

    def is_downloaded(track_name: str) -> bool:
        return os.path.exists(offline_file_path(track_name))

    def show_notice(msg: str):
        page.snack_bar = ft.SnackBar(
            content=ft.Text(msg, color=TEXT_MAIN),
            bgcolor=GLASS_ELEVATED,
        )
        page.snack_bar.open = True
        page.update()

    # Audio service
    def on_duration_change(e: fa.AudioDurationChangeEvent):
        nonlocal duration_ms
        if e.duration:
            duration_ms = e.duration.in_milliseconds()
            total_time_txt.value = format_ms(duration_ms)
            seek_slider.max = max(float(duration_ms), 1.0)
            page.update()

    def on_position_change(e: fa.AudioPositionChangeEvent):
        nonlocal position_ms
        if not seeking and e.position:
            position_ms = e.position.in_milliseconds()
            curr_time_txt.value = format_ms(position_ms)
            seek_slider.value = min(float(position_ms), seek_slider.max)
            page.update()

    def on_state_change(e: fa.AudioStateChangeEvent):
        nonlocal is_playing
        if e.state == fa.AudioState.PLAYING:
            is_playing = True
            play_btn.content = ft.Icon(ft.Icons.PAUSE_ROUNDED, color=BG_VOID, size=28)
        elif e.state in (fa.AudioState.PAUSED, fa.AudioState.STOPPED):
            is_playing = False
            play_btn.content = ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, color=BG_VOID, size=28)
        elif e.state == fa.AudioState.COMPLETED:
            is_playing = False
            play_btn.content = ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, color=BG_VOID, size=28)
            if repeat_mode == "one" and current_track:
                play_track_item(current_track, index=current_index)
            else:
                play_next(None)
        refresh_track_list()
        page.update()

    audio = fa.Audio(
        src="https://github.com",
        autoplay=False,
        on_duration_change=on_duration_change,
        on_position_change=on_position_change,
        on_state_change=on_state_change,
    )
    page.services.append(audio)

    # Top Header (Clean luxury title + directory info)
    status_text = ft.Text(
        f"Root Folder: {audio_root_dir}",
        size=11,
        color=TEXT_MUTED,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )

    search_field = ft.TextField(
        hint_text="Search melodies by exact filename...",
        prefix_icon=ft.Icons.SEARCH_ROUNDED,
        bgcolor=GLASS_CARD,
        border=ft.InputBorder.OUTLINE,
        color=TEXT_MAIN,
        text_size=14,
        content_padding=12,
        on_change=lambda e: on_search_change(e.control.value or ""),
    )

    category_row = ft.Row(scroll=ft.ScrollMode.AUTO, spacing=8)

    playlist_input = ft.TextField(
        hint_text="New playlist...",
        bgcolor=GLASS_CARD,
        border=ft.InputBorder.OUTLINE,
        color=TEXT_MAIN,
        text_size=13,
        content_padding=10,
        expand=True,
    )
    playlist_dropdown = ft.Dropdown(
        bgcolor=GLASS_CARD,
        border=ft.InputBorder.OUTLINE,
        color=TEXT_MAIN,
        text_size=13,
        content_padding=10,
        expand=True,
        on_select=lambda e: refresh_track_list(),
    )
    playlist_bar = ft.Row(
        controls=[
            playlist_input,
            ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.ADD_ROUNDED, color=BG_VOID, size=18),
                        ft.Text("Create", size=12, weight=ft.FontWeight.BOLD, color=BG_VOID),
                    ],
                    spacing=4,
                    tight=True,
                ),
                bgcolor=GOLD,
                border_radius=14,
                padding=ft.Padding.symmetric(horizontal=12, vertical=9),
                on_click=lambda e: create_playlist(),
            ),
            playlist_dropdown,
        ],
        visible=False,
        spacing=8,
    )

    track_column = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)

    # Player Dock Controls (All buttons grouped cleanly at bottom)
    player_title_txt = ft.Text(
        "Select a melody",
        size=14,
        weight=ft.FontWeight.W_600,
        color=TEXT_MAIN,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    player_cat_txt = ft.Text("Ready", size=11, color=TEXT_MUTED)
    source_pill = ft.Container(
        content=ft.Text("STREAM", size=10, weight=ft.FontWeight.BOLD, color=GOLD),
        bgcolor=GOLD_SOFT,
        padding=ft.Padding.symmetric(horizontal=8, vertical=2),
        border_radius=10,
    )
    player_fav_btn = ft.IconButton(
        icon=ft.Icons.FAVORITE_BORDER_ROUNDED,
        icon_color=GOLD,
        on_click=lambda e: toggle_current_fav(),
    )

    curr_time_txt = ft.Text("0:00", size=11, color=TEXT_MUTED)
    total_time_txt = ft.Text("0:00", size=11, color=TEXT_MUTED)
    seek_slider = ft.Slider(
        min=0,
        max=100,
        value=0,
        active_color=GOLD,
        inactive_color=GLASS_BORDER,
        thumb_color=GOLD,
        expand=True,
        on_change_start=lambda e: set_seeking(True),
        on_change_end=lambda e: commit_seek(e.control.value),
    )

    shuffle_btn = ft.IconButton(
        icon=ft.Icons.SHUFFLE_ROUNDED,
        icon_color=TEXT_MUTED,
        on_click=lambda e: toggle_shuffle(),
    )
    prev_btn = ft.IconButton(
        icon=ft.Icons.SKIP_PREVIOUS_ROUNDED,
        icon_color=TEXT_MAIN,
        on_click=lambda e: play_prev(e),
    )
    play_btn = ft.Container(
        content=ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, color=BG_VOID, size=28),
        width=52,
        height=52,
        border_radius=26,
        bgcolor=GOLD,
        alignment=ft.Alignment.CENTER,
        on_click=lambda e: toggle_play_pause(),
    )
    next_btn = ft.IconButton(
        icon=ft.Icons.SKIP_NEXT_ROUNDED,
        icon_color=TEXT_MAIN,
        on_click=lambda e: play_next(e),
    )
    repeat_btn = ft.IconButton(
        icon=ft.Icons.REPEAT_ROUNDED,
        icon_color=GOLD,
        on_click=lambda e: cycle_repeat(),
    )

    # Bottom Action & Navigation Bar (All main action buttons downside)
    download_all_btn = ft.Container(
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.DOWNLOAD_FOR_OFFLINE_ROUNDED, color=BG_VOID, size=18),
                ft.Text("Download All", size=12, weight=ft.FontWeight.BOLD, color=BG_VOID),
            ],
            spacing=6,
            tight=True,
        ),
        bgcolor=GOLD,
        border_radius=16,
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        on_click=lambda e: download_all_tracks(),
    )

    sync_bottom_btn = ft.Container(
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.SYNC_ROUNDED, color=GOLD, size=18),
                ft.Text("Sync", size=12, weight=ft.FontWeight.W_600, color=GOLD),
            ],
            spacing=4,
            tight=True,
        ),
        bgcolor=GLASS_CARD,
        border=ft.Border.all(1, GLASS_BORDER),
        border_radius=16,
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        on_click=lambda e: sync_server(e),
    )

    def set_seeking(val: bool):
        nonlocal seeking
        seeking = val

    def commit_seek(val: float):
        nonlocal seeking, position_ms
        seeking = False
        position_ms = int(val)
        curr_time_txt.value = format_ms(position_ms)
        audio.seek(position_ms)
        page.update()

    def toggle_play_pause():
        if not current_track and active_queue:
            play_track_item(active_queue[0], index=0)
            return
        if is_playing:
            audio.pause()
        else:
            audio.resume()

    def play_track_item(track: dict, index: int = -1):
        nonlocal current_track, current_index, duration_ms, position_ms
        current_track = track
        if index >= 0:
            current_index = index

        player_title_txt.value = track["name"]
        player_cat_txt.value = track["category"]
        player_fav_btn.icon = (
            ft.Icons.FAVORITE_ROUNDED if track["name"] in state["favorites"] else ft.Icons.FAVORITE_BORDER_ROUNDED
        )

        local_path = offline_file_path(track["name"])
        if os.path.exists(local_path):
            audio.src = local_path
            source_pill.content.value = "ROOT DIR"
        else:
            audio.src = track["url"]
            source_pill.content.value = "STREAM"

        duration_ms = 0
        position_ms = 0
        seek_slider.value = 0
        curr_time_txt.value = "0:00"
        total_time_txt.value = "0:00"
        audio.play()
        refresh_track_list()
        page.update()

    def play_next(e):
        if not active_queue:
            return
        if is_shuffle:
            idx = random.randrange(len(active_queue))
        elif current_index + 1 < len(active_queue):
            idx = current_index + 1
        elif repeat_mode == "all":
            idx = 0
        else:
            return
        play_track_item(active_queue[idx], index=idx)

    def play_prev(e):
        if not active_queue:
            return
        if position_ms > 3000:
            audio.seek(0)
            return
        if current_index > 0:
            play_track_item(active_queue[current_index - 1], index=current_index - 1)

    def toggle_shuffle():
        nonlocal is_shuffle
        is_shuffle = not is_shuffle
        shuffle_btn.icon_color = GOLD if is_shuffle else TEXT_MUTED
        page.update()

    def cycle_repeat():
        nonlocal repeat_mode
        order = ["all", "one", "off"]
        repeat_mode = order[(order.index(repeat_mode) + 1) % len(order)]
        if repeat_mode == "one":
            repeat_btn.icon = ft.Icons.REPEAT_ONE_ROUNDED
            repeat_btn.icon_color = GOLD
        elif repeat_mode == "all":
            repeat_btn.icon = ft.Icons.REPEAT_ROUNDED
            repeat_btn.icon_color = GOLD
        else:
            repeat_btn.icon = ft.Icons.REPEAT_ROUNDED
            repeat_btn.icon_color = TEXT_MUTED
        page.update()

    def toggle_favorite(track_name: str):
        if track_name in state["favorites"]:
            state["favorites"].remove(track_name)
        else:
            state["favorites"].append(track_name)
        save_state()
        if current_track and current_track["name"] == track_name:
            player_fav_btn.icon = (
                ft.Icons.FAVORITE_ROUNDED if track_name in state["favorites"] else ft.Icons.FAVORITE_BORDER_ROUNDED
            )
        refresh_track_list()
        page.update()

    def toggle_current_fav():
        if current_track:
            toggle_favorite(current_track["name"])

    def create_playlist():
        name = (playlist_input.value or "").strip()
        if not name:
            return
        if name not in state["playlists"]:
            state["playlists"][name] = []
            save_state()
        playlist_input.value = ""
        refresh_playlist_dropdown(select_name=name)
        refresh_track_list()
        page.update()

    def add_to_playlist(track_name: str):
        names = list(state["playlists"].keys())
        if not names:
            state["playlists"]["Daily Listening"] = []
            names = ["Daily Listening"]
        target = playlist_dropdown.value if playlist_dropdown.value in names else names[0]
        if track_name not in state["playlists"][target]:
            state["playlists"][target].append(track_name)
            save_state()
        refresh_playlist_dropdown(select_name=target)
        show_notice(f'Added "{track_name}" to {target}')

    def refresh_playlist_dropdown(select_name: str | None = None):
        names = list(state["playlists"].keys())
        playlist_dropdown.options = [
            ft.dropdown.Option(key=n, text=f"{n} ({len(state['playlists'][n])})") for n in names
        ]
        if select_name and select_name in names:
            playlist_dropdown.value = select_name
        elif playlist_dropdown.value not in names and names:
            playlist_dropdown.value = names[0]

    def trigger_browser_download(url: str):
        if not url:
            return
        try:
            page.launch_url(url)
        except Exception:
            pass

    def toggle_download(track: dict):
        url = track.get("url", "")
        if page.web:
            if url:
                trigger_browser_download(url)
                show_notice(f'Opening download URL for "{track["name"]}"')
            else:
                show_notice("Download URL unavailable")
            return

        path = offline_file_path(track["name"])
        if os.path.exists(path):
            os.remove(path)
            show_notice(f'Removed "{track["name"]}" from {audio_root_dir}')
            refresh_track_list()
            page.update()
            return

        if not url:
            show_notice("No online URL available for download")
            return

        show_notice(f'Downloading "{track["name"]}" to root folder...')

        def worker():
            try:
                data = download_bytes(url)
                with open(path, "wb") as f:
                    f.write(data)
                show_notice(f'Saved "{track["name"]}" in {audio_root_dir}')
            except Exception:
                show_notice("Download failed. Check your connection.")
            refresh_track_list()
            page.update()

        threading.Thread(target=worker, daemon=True).start()

    def download_all_tracks():
        targets = active_queue if active_queue else state["catalog"]
        with_urls = [t for t in targets if t.get("url")]
        if not with_urls:
            show_notice("No audio files available to download")
            return

        if page.web:
            for t in with_urls:
                trigger_browser_download(t["url"])
            show_notice(f"Triggered browser download URLs for {len(with_urls)} tracks")
            return

        pending = [t for t in with_urls if not is_downloaded(t["name"])]
        if not pending:
            show_notice("All tracks are already saved in the root folder")
            return

        show_notice(f"Downloading {len(pending)} tracks to {audio_root_dir}...")

        def worker():
            saved_count = 0
            for t in pending:
                try:
                    data = download_bytes(t["url"])
                    with open(offline_file_path(t["name"]), "wb") as f:
                        f.write(data)
                    saved_count += 1
                except Exception:
                    continue
            show_notice(f"Saved {saved_count} of {len(pending)} tracks to root folder")
            refresh_track_list()
            page.update()

        threading.Thread(target=worker, daemon=True).start()

    def refresh_categories():
        cats = ["All"] + sorted({t["category"] for t in state["catalog"]})
        chips = []
        for cat in cats:
            active = cat == selected_category
            chips.append(
                ft.Container(
                    content=ft.Text(cat, size=12, weight=ft.FontWeight.W_600 if active else ft.FontWeight.W_400, color=GOLD if active else TEXT_MUTED),
                    bgcolor=GOLD_SOFT if active else GLASS_CARD,
                    border=ft.Border.all(1, GOLD if active else GLASS_BORDER),
                    border_radius=20,
                    padding=ft.Padding.symmetric(horizontal=14, vertical=7),
                    on_click=lambda e, c=cat: select_category(c),
                )
            )
        category_row.controls = chips

    def select_category(cat: str):
        nonlocal selected_category
        selected_category = cat
        refresh_categories()
        refresh_track_list()
        page.update()

    def on_search_change(val: str):
        nonlocal search_query
        search_query = val.strip().lower()
        refresh_track_list()
        page.update()

    def build_filtered_tracks() -> list[dict]:
        catalog_map = {t["name"]: t for t in state["catalog"]}

        if current_view == "explore":
            items = [
                t for t in state["catalog"]
                if selected_category == "All" or t["category"] == selected_category
            ]
        elif current_view == "favorites":
            items = [t for t in state["catalog"] if t["name"] in state["favorites"]]
        elif current_view == "offline":
            items = []
            if not page.web and os.path.exists(audio_root_dir):
                for fname in sorted(os.listdir(audio_root_dir)):
                    if AUDIO_EXT_RE.search(fname):
                        meta = catalog_map.get(fname)
                        items.append({
                            "name": fname,
                            "category": meta["category"] if meta else "Root Folder",
                            "url": meta["url"] if meta else "",
                        })
        elif current_view == "playlists":
            pl_name = playlist_dropdown.value or ""
            track_names = state["playlists"].get(pl_name, [])
            items = [
                catalog_map.get(n, {"name": n, "category": pl_name, "url": ""})
                for n in track_names
            ]
        else:
            items = list(state["catalog"])

        if search_query:
            items = [t for t in items if search_query in t["name"].lower()]
        return items

    def refresh_track_list():
        nonlocal active_queue
        items = build_filtered_tracks()
        active_queue = items

        if not items:
            track_column.controls = [
                ft.Container(
                    content=ft.Text(
                        f"No instrumentals found here.\nUpload audio files to {GITHUB_USER}/{GITHUB_REPO} and tap Sync.",
                        size=13,
                        color=TEXT_MUTED,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    padding=40,
                    alignment=ft.Alignment.CENTER,
                )
            ]
            return

        active_name = current_track["name"] if current_track else ""
        cards = []
        for idx, trk in enumerate(items):
            is_curr = trk["name"] == active_name
            saved = (not page.web) and is_downloaded(trk["name"])
            fav = trk["name"] in state["favorites"]

            cards.append(
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Container(
                                content=ft.Icon(
                                    ft.Icons.PAUSE_ROUNDED if (is_curr and is_playing) else ft.Icons.PLAY_ARROW_ROUNDED,
                                    color=GOLD,
                                    size=22,
                                ),
                                width=42,
                                height=42,
                                border_radius=14,
                                bgcolor=GOLD_SOFT,
                                alignment=ft.Alignment.CENTER,
                                on_click=lambda e, t=trk, i=idx: play_track_item(t, index=i),
                            ),
                            ft.Container(
                                content=ft.Column(
                                    controls=[
                                        ft.Text(trk["name"], size=13, weight=ft.FontWeight.W_600, color=TEXT_MAIN),
                                        ft.Text(trk["category"], size=11, color=TEXT_MUTED),
                                    ],
                                    spacing=2,
                                ),
                                expand=True,
                                on_click=lambda e, t=trk, i=idx: play_track_item(t, index=i),
                            ),
                            ft.IconButton(
                                icon=ft.Icons.FAVORITE_ROUNDED if fav else ft.Icons.FAVORITE_BORDER_ROUNDED,
                                icon_color=GOLD if fav else TEXT_MUTED,
                                icon_size=19,
                                on_click=lambda e, name=trk["name"]: toggle_favorite(name),
                            ),
                            ft.IconButton(
                                icon=ft.Icons.QUEUE_MUSIC_ROUNDED,
                                icon_color=TEXT_MUTED,
                                icon_size=19,
                                on_click=lambda e, name=trk["name"]: add_to_playlist(name),
                            ),
                            ft.IconButton(
                                icon=ft.Icons.FILE_DOWNLOAD_DONE_ROUNDED if saved else ft.Icons.FILE_DOWNLOAD_ROUNDED,
                                icon_color=GOLD if saved else TEXT_MUTED,
                                icon_size=19,
                                on_click=lambda e, t=trk: toggle_download(t),
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=2,
                    ),
                    bgcolor=GLASS_ELEVATED if is_curr else GLASS_CARD,
                    border=ft.Border.all(1, GOLD if is_curr else GLASS_BORDER),
                    border_radius=18,
                    padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                )
            )
        track_column.controls = cards

    # Bottom Navigation Tabs (All nav buttons positioned downside)
    nav_items_meta = [
        ("explore", "Explore", ft.Icons.EXPLORE_ROUNDED),
        ("favorites", "Favorites", ft.Icons.FAVORITE_ROUNDED),
        ("playlists", "Playlists", ft.Icons.LIBRARY_MUSIC_ROUNDED),
        ("offline", "Saved", ft.Icons.FOLDER_ROUNDED),
    ]
    nav_buttons: dict[str, ft.Container] = {}

    def switch_view(view_name: str):
        nonlocal current_view
        current_view = view_name
        for key, label, icon_name in nav_items_meta:
            active = key == view_name
            btn_box = nav_buttons[key]
            btn_box.bgcolor = GOLD_SOFT if active else "transparent"
            btn_box.content = ft.Column(
                controls=[
                    ft.Icon(icon_name, color=GOLD if active else TEXT_MUTED, size=20),
                    ft.Text(
                        label,
                        size=10,
                        weight=ft.FontWeight.BOLD if active else ft.FontWeight.W_400,
                        color=GOLD if active else TEXT_MUTED,
                    ),
                ],
                spacing=2,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
            )
        category_row.visible = view_name == "explore"
        playlist_bar.visible = view_name == "playlists"
        if view_name == "playlists":
            refresh_playlist_dropdown()
        refresh_track_list()
        page.update()

    for key, label, icon_name in nav_items_meta:
        active = key == "explore"
        nav_buttons[key] = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Icon(icon_name, color=GOLD if active else TEXT_MUTED, size=20),
                    ft.Text(
                        label,
                        size=10,
                        weight=ft.FontWeight.BOLD if active else ft.FontWeight.W_400,
                        color=GOLD if active else TEXT_MUTED,
                    ),
                ],
                spacing=2,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
            ),
            bgcolor=GOLD_SOFT if active else "transparent",
            border_radius=14,
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            expand=True,
            on_click=lambda e, v=key: switch_view(v),
        )

    # Sync GitHub Repository (root files + category folders)
    def sync_server(e=None):
        status_text.value = f"Scanning {GITHUB_USER}/{GITHUB_REPO}..."
        page.update()

        def worker():
            try:
                bust = f"?t={int(time.time())}"
                root_items = http_get_json(f"{REPO_API}{bust}")
                discovered: list[dict] = []

                for item in root_items:
                    if item.get("type") == "file" and AUDIO_EXT_RE.search(item.get("name", "")):
                        discovered.append({
                            "name": item["name"],
                            "category": "General",
                            "url": item["download_url"],
                        })

                for folder in root_items:
                    if folder.get("type") == "dir" and not folder.get("name", "").startswith("."):
                        fname = folder["name"]
                        sub_url = f"{REPO_API}/{urllib.parse.quote(fname)}{bust}"
                        try:
                            sub_items = http_get_json(sub_url)
                            for f in sub_items:
                                if f.get("type") == "file" and AUDIO_EXT_RE.search(f.get("name", "")):
                                    discovered.append({
                                        "name": f["name"],
                                        "category": fname,
                                        "url": f["download_url"],
                                    })
                        except urllib.error.URLError:
                            pass

                state["catalog"] = discovered
                save_state()
                status_text.value = f"{len(discovered)} tracks • {audio_root_dir}"
            except Exception:
                status_text.value = f"Offline • {audio_root_dir}"

            refresh_categories()
            refresh_track_list()
            page.update()

        threading.Thread(target=worker, daemon=True).start()

    # Assemble Luxury Glass Layout with Bottom Controls
    header = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("Sacred Instrumentals", size=21, weight=ft.FontWeight.BOLD, color=GOLD),
                status_text,
            ],
            spacing=2,
        ),
        padding=ft.Padding.only(left=16, top=16, right=16, bottom=6),
    )

    bottom_dock = ft.Container(
        content=ft.Column(
            controls=[
                # Track & Favorite Row
                ft.Row(
                    controls=[
                        ft.Container(
                            content=ft.Icon(ft.Icons.GRAPHIC_EQ_ROUNDED, color=GOLD, size=20),
                            width=42,
                            height=42,
                            border_radius=14,
                            bgcolor=GOLD_SOFT,
                            border=ft.Border.all(1, GOLD),
                            alignment=ft.Alignment.CENTER,
                        ),
                        ft.Column(
                            controls=[
                                player_title_txt,
                                ft.Row(controls=[player_cat_txt, source_pill], spacing=8),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        player_fav_btn,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                # Seek Row
                ft.Row(
                    controls=[curr_time_txt, seek_slider, total_time_txt],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=8,
                ),
                # Playback Transport Row
                ft.Row(
                    controls=[shuffle_btn, prev_btn, play_btn, next_btn, repeat_btn],
                    alignment=ft.MainAxisAlignment.SPACE_AROUND,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                # Bulk Action Row (Download All & Sync)
                ft.Row(
                    controls=[download_all_btn, sync_bottom_btn],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                # Bottom Tab Navigation Row
                ft.Row(
                    controls=list(nav_buttons.values()),
                    spacing=4,
                ),
            ],
            spacing=6,
        ),
        bgcolor=GLASS_DOCK,
        border=ft.Border.all(1, GLASS_BORDER),
        border_radius=26,
        margin=ft.Margin.only(left=10, right=10, bottom=10),
        padding=ft.Padding.only(left=14, top=12, right=14, bottom=10),
    )

    body_panel = ft.Container(
        content=ft.Column(
            controls=[
                header,
                ft.Container(
                    content=search_field,
                    padding=ft.Padding.only(left=16, top=6, right=16, bottom=6),
                ),
                ft.Container(
                    content=category_row,
                    padding=ft.Padding.only(left=16, top=4, right=16, bottom=4),
                ),
                ft.Container(
                    content=playlist_bar,
                    padding=ft.Padding.only(left=16, top=4, right=16, bottom=4),
                ),
                ft.Container(
                    content=track_column,
                    padding=ft.Padding.only(left=16, top=6, right=16, bottom=6),
                    expand=True,
                ),
                bottom_dock,
            ],
            spacing=0,
            expand=True,
        ),
        bgcolor=BG_LUX,
        border=ft.Border.all(1, GLASS_BORDER),
        border_radius=28,
        margin=ft.Margin.symmetric(vertical=8, horizontal=8),
        width=540,
        expand=True,
    )

    page.add(
        ft.Container(
            content=body_panel,
            bgcolor=BG_VOID,
            alignment=ft.Alignment.CENTER,
            expand=True,
        )
    )

    refresh_playlist_dropdown()
    refresh_categories()
    refresh_track_list()
    sync_server()


if __name__ == "__main__":
    ft.run(main)