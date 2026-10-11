# -*- coding: utf-8 -*-
"""把 info/*.tsv 合并进 site/songlist.json。新曲进入待分类，不阻塞主流程。"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

from settings import pack_options, pipeline_paths

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


SPECIAL_DIFFICULTY_KEY_MAPPING = {
    "slips": ["solips", "sølips"],
    "poseidon": ["ποσειδών"],
    "parsey": ["ρarsey"],
}

SPECIAL_DIFFICULTY_ID_MAPPING = {
    "anotherme": [
        ("anotherme.neutralmoon", "risingsun"),
        ("anotherme.daan", "kalpa"),
    ],
}


def normalize_title(text: str) -> str:
    if not text:
        return ""
    return " ".join(text.split()).strip()


def normalize_key(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[\s\u00a0\u3000\.,;:!?()\[\]&'\"～\-_]+", "", text)
    text = re.sub(r"[\u3000-\u303f\uff00-\uffef]", "", text)
    text = re.sub(r"[^\w]", "", text, flags=re.UNICODE)
    return text.lower()


SONG_FIELDS = ("标题", "bpm", "难度", "曲包", "攻略链接", "id", "别称")


def sanitize_song(song: dict) -> dict:
    """只保留 songlist 既有字段，丢掉 phigrosId / bpmList 等内部对照。"""
    clean = {}
    for key in SONG_FIELDS:
        if key in song:
            clean[key] = song[key]
    clean.setdefault("标题", song.get("标题") or "")
    clean.setdefault("bpm", song.get("bpm", 0))
    clean.setdefault("难度", song.get("难度") if isinstance(song.get("难度"), dict) else {"ez": 0, "hd": 0, "in": 0, "at": ""})
    clean.setdefault("曲包", song.get("曲包") or "")
    clean.setdefault("攻略链接", song.get("攻略链接") if isinstance(song.get("攻略链接"), dict) else {"ez": "", "hd": "", "in": "", "at": ""})
    clean.setdefault("id", song.get("id") or "")
    clean.setdefault("别称", list(song.get("别称") or []))
    return clean


def empty_songlist() -> dict:
    return {
        "version": "1.0.0",
        "lastUpdate": "",
        "data": [],
    }


def _read_songlist_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as handle:
        obj = json.load(handle)
    if not isinstance(obj, dict):
        return None
    obj.setdefault("version", "1.0.0")
    obj.setdefault("lastUpdate", "")
    obj.setdefault("data", [])
    return obj


def songlist_has_real_packs(obj: dict, pending_name: str | None = None) -> bool:
    pending_name = pending_name or pack_options()["pending_name"]
    for song in obj.get("data") or []:
        pack = (song.get("曲包") or "").strip()
        sid = (song.get("id") or "").strip()
        if pack and pack != pending_name and not sid.startswith(pack_options()["pending_prefix"] + "_"):
            return True
    return False


def resolve_songlist_path() -> Path:
    """优先用已有正式曲包的 songlist，避免空骨架/全待分类把旧目录冲掉。"""
    paths = pipeline_paths()
    pending_name = pack_options()["pending_name"]
    candidates = [paths["songlist"], paths["root"] / "songlist.json"]
    fallback = None
    for path in candidates:
        obj = _read_songlist_file(path)
        if obj is None:
            continue
        if songlist_has_real_packs(obj, pending_name):
            return path
        if fallback is None:
            fallback = path
    return fallback or paths["songlist"]


def load_songlist(path: Path | None = None) -> dict:
    paths = pipeline_paths()
    chosen = Path(path) if path else resolve_songlist_path()
    obj = _read_songlist_file(chosen)
    if obj is None:
        obj = empty_songlist()
        paths["songlist"].parent.mkdir(parents=True, exist_ok=True)
        save_songlist(obj, paths["songlist"])
        return obj
    if chosen.resolve() != paths["songlist"].resolve():
        save_songlist(obj, paths["songlist"])
    return obj


def save_songlist(obj: dict, path: Path | None = None) -> None:
    path = path or pipeline_paths()["songlist"]
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "version": obj.get("version") or "1.0.0",
        "lastUpdate": obj.get("lastUpdate") or "",
    }
    if "latestUpdateSongIds" in obj:
        out["latestUpdateSongIds"] = obj.get("latestUpdateSongIds") or []
    out["data"] = [sanitize_song(song) for song in obj.get("data") or [] if isinstance(song, dict)]
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(out, handle, ensure_ascii=False, indent=4)
        handle.write("\n")


def load_pack_map(path: Path | None = None) -> dict:
    path = path or pipeline_paths()["pack_map"]
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def save_pack_map(data: dict, path: Path | None = None) -> None:
    path = path or pipeline_paths()["pack_map"]
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=4)
        handle.write("\n")


def load_info_tsv(path: Path | None = None) -> list[dict]:
    path = path or pipeline_paths()["info_tsv"]
    rows = []
    if not path.exists():
        return rows
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n\r").split("\t")
            if not parts or not parts[0]:
                continue
            rows.append({
                "phigrosId": parts[0],
                "title": (parts[1] if len(parts) > 1 else "").strip(),
                "composer": (parts[2] if len(parts) > 2 else "").strip(),
                "illustrator": (parts[3] if len(parts) > 3 else "").strip(),
                "row": parts,
            })
    return rows


def load_chapters(path: Path | None = None) -> dict[str, dict]:
    path = path or pipeline_paths()["chapters_tsv"]
    result = {}
    if not path.exists():
        return result
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n\r").split("\t")
            if len(parts) < 2 or not parts[0]:
                continue
            result[parts[0]] = {
                "group": parts[1] if len(parts) > 1 else "",
                "code": parts[2] if len(parts) > 2 else "",
                "title": parts[3] if len(parts) > 3 else "",
            }
    return result


def load_difficulty_by_id(path: Path | None = None) -> dict[str, dict]:
    path = path or pipeline_paths()["difficulty_tsv"]
    result = {}
    if not path.exists():
        return result
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n\r").split("\t")
            if len(parts) < 4:
                continue
            try:
                ez, hd, inn = float(parts[1]), float(parts[2]), float(parts[3])
            except ValueError:
                continue
            at = ""
            if len(parts) >= 5:
                try:
                    at = float(parts[4])
                except ValueError:
                    at = ""
            result[parts[0]] = {"ez": ez, "hd": hd, "in": inn, "at": at}
    return result


def load_difficulty_by_title(path: Path | None = None) -> dict[str, dict]:
    path = path or pipeline_paths()["difficulty_tsv"]
    result = {}
    if not path.exists():
        return result
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n\r").split("\t")
            if len(parts) < 4:
                continue
            full_col = parts[0]
            split_full = full_col.split(".", 1)
            title_part = split_full[0]
            charter_part = split_full[1] if len(split_full) > 1 else ""
            base_key = normalize_key(title_part)
            full_key = f"{base_key}.{normalize_key(charter_part)}" if charter_part else base_key
            key = full_key if base_key in SPECIAL_DIFFICULTY_ID_MAPPING and charter_part else base_key
            try:
                ez, hd, inn = float(parts[1]), float(parts[2]), float(parts[3])
            except ValueError:
                continue
            at = ""
            if len(parts) >= 5:
                try:
                    at = float(parts[4])
                except ValueError:
                    at = ""
            diff = {"ez": ez, "hd": hd, "in": inn, "at": at}
            if key not in result or (diff["at"] != "" and result.get(key, {}).get("at", "") == ""):
                result[key] = diff
            for alias in SPECIAL_DIFFICULTY_KEY_MAPPING.get(key, []):
                if alias not in result or (diff["at"] != "" and result.get(alias, {}).get("at", "") == ""):
                    result[alias] = diff
    return result


def load_bpm_tsv(path: Path | None = None) -> dict[str, tuple]:
    path = path or pipeline_paths()["bpm_tsv"]
    result = {}
    if not path.exists():
        return result
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n\r").split("\t")
            if len(parts) < 2:
                continue
            try:
                main = float(parts[1])
            except ValueError:
                continue
            if abs(main - round(main)) < 1e-6:
                main = int(round(main))
            all_bpms = []
            if len(parts) >= 3 and parts[2]:
                for item in parts[2].split(","):
                    item = item.strip()
                    if not item:
                        continue
                    try:
                        number = float(item)
                    except ValueError:
                        continue
                    if abs(number - round(number)) < 1e-6:
                        number = int(round(number))
                    all_bpms.append(number)
            result[parts[0]] = (main, all_bpms)
    return result


def find_song_by_title(title: str, data: list, occupied: set[int] | None = None):
    occupied = occupied or set()
    norm = normalize_title(title)
    key = normalize_key(title)
    if norm:
        for index, song in enumerate(data):
            if index in occupied:
                continue
            if normalize_title(song.get("标题") or "") == norm:
                return index, song
            for alias in song.get("别称") or []:
                if normalize_title(alias or "") == norm:
                    return index, song
    if key:
        for index, song in enumerate(data):
            if index in occupied:
                continue
            if normalize_key(song.get("标题") or "") == key:
                return index, song
            for alias in song.get("别称") or []:
                if normalize_key(alias or "") == key:
                    return index, song
        if len(key) >= 4:
            for index, song in enumerate(data):
                if index in occupied:
                    continue
                song_key = normalize_key(song.get("标题") or "")
                if song_key and (song_key.startswith(key) or key.startswith(song_key)):
                    return index, song
    return None


def find_difficulty_for_song(song: dict, by_id: dict, by_title: dict) -> dict | None:
    apk_id = apk_id_for_song(song)
    if apk_id and apk_id in by_id:
        return by_id[apk_id]
    song_id = song.get("id") or ""
    key = normalize_key(song.get("标题") or "")
    if key in SPECIAL_DIFFICULTY_ID_MAPPING:
        song_id_norm = song_id.lower().replace("_", "").replace("-", "")
        for full_key, id_substr in SPECIAL_DIFFICULTY_ID_MAPPING[key]:
            if id_substr.lower().replace("_", "").replace("-", "") in song_id_norm:
                if full_key in by_title:
                    return by_title[full_key]
                break
    if key in by_title:
        return by_title[key]
    for alias in song.get("别称") or []:
        alias_key = normalize_key(alias or "")
        if alias_key in by_title:
            return by_title[alias_key]
    return None


def apply_difficulty(song: dict, diff: dict) -> bool:
    target = song.get("难度")
    if not isinstance(target, dict):
        song["难度"] = {}
        target = song["难度"]
    at_new = diff.get("at", "")
    changed = (
        target.get("ez") != diff["ez"]
        or target.get("hd") != diff["hd"]
        or target.get("in") != diff["in"]
        or ((target.get("at", "") or "") != (at_new if at_new != "" else "") and (target.get("at", "") or at_new))
    )
    target["ez"] = diff["ez"]
    target["hd"] = diff["hd"]
    target["in"] = diff["in"]
    target["at"] = at_new if at_new != "" else ""
    return changed


def same_bpm(left, right) -> bool:
    try:
        return abs(float(left) - float(right)) < 1e-6
    except (TypeError, ValueError):
        return left == right


def apply_bpm(song: dict, main, all_bpms: list | None = None) -> bool:
    if same_bpm(song.get("bpm"), main):
        return False
    song["bpm"] = main
    return True


def format_difficulty(diff: dict | None) -> str:
    diff = diff or {}
    parts = [f"ez {diff.get('ez', '')}", f"hd {diff.get('hd', '')}", f"in {diff.get('in', '')}"]
    at_val = diff.get("at", "")
    if at_val != "" and at_val is not None:
        parts.append(f"at {at_val}")
    return " / ".join(parts)


def apk_id_for_song(song: dict, info_rows: list[dict] | None = None) -> str:
    """用标题 / 别称 / 站点 id 对照 info.tsv，不写回 songlist。"""
    info_rows = info_rows if info_rows is not None else load_info_tsv()
    title_key = normalize_key(song.get("标题") or "")
    song_id = song.get("id") or ""
    names = [song.get("标题") or ""] + list(song.get("别称") or [])

    if title_key in SPECIAL_DIFFICULTY_ID_MAPPING:
        song_id_norm = song_id.lower().replace("_", "").replace("-", "")
        for _full_key, id_substr in SPECIAL_DIFFICULTY_ID_MAPPING[title_key]:
            needle = id_substr.lower().replace("_", "").replace("-", "")
            if needle not in song_id_norm:
                continue
            for row in info_rows:
                hay = (row.get("phigrosId") or "").lower().replace("_", "").replace("-", "").replace(".", "")
                if needle in hay and normalize_key(row.get("title") or "") == title_key:
                    return row["phigrosId"]

    matches = []
    for name in names:
        norm = normalize_title(name)
        key = normalize_key(name)
        if not norm and not key:
            continue
        for row in info_rows:
            row_title = row.get("title") or ""
            if (norm and normalize_title(row_title) == norm) or (key and normalize_key(row_title) == key):
                if row not in matches:
                    matches.append(row)
    if len(matches) == 1:
        return matches[0]["phigrosId"]
    if len(matches) > 1:
        return matches[0]["phigrosId"]
    return ""


def info_for_song(song: dict, info_rows: list[dict] | None = None) -> dict:
    info_rows = info_rows if info_rows is not None else load_info_tsv()
    apk_id = apk_id_for_song(song, info_rows)
    if not apk_id:
        return {}
    for row in info_rows:
        if row.get("phigrosId") == apk_id:
            return row
    return {}


def pack_to_id_prefix(pack_name: str) -> str:
    text = (pack_name or "").strip()
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "Pack"


def last_index_of_pack(data: list, pack_name: str) -> int:
    last = -1
    for index, song in enumerate(data):
        if (song.get("曲包") or "") == pack_name:
            last = index
    return last


def get_pack_prefix_and_next_num(data: list, pack_name: str) -> tuple[str, int]:
    prefix = pack_to_id_prefix(pack_name)
    max_num = 0
    for song in data:
        if (song.get("曲包") or "") != pack_name:
            continue
        sid = song.get("id") or ""
        match = re.match(r"^(.+)_(\d+)$", sid)
        if match:
            prefix = match.group(1)
            max_num = max(max_num, int(match.group(2)))
    return prefix, max_num + 1


def next_pending_ids(data: list, count: int, prefix: str) -> list[str]:
    max_n = 0
    used = set()
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)$")
    for song in data:
        match = pattern.match(song.get("id") or "")
        if match:
            number = int(match.group(1))
            used.add(number)
            max_n = max(max_n, number)
    ids = []
    number = max_n + 1
    while len(ids) < count:
        if number not in used:
            ids.append(f"{prefix}_{number}")
        number += 1
    return ids


def new_song_entry(title: str, pack_name: str, new_id: str, phigros_id: str = "") -> dict:
    return {
        "标题": title,
        "bpm": 0,
        "难度": {"ez": 0, "hd": 0, "in": 0, "at": ""},
        "曲包": pack_name,
        "攻略链接": {"ez": "", "hd": "", "in": "", "at": ""},
        "id": new_id,
        "别称": [],
    }


def existing_packs(data: list | None = None) -> list[str]:
    if data is None:
        data = load_songlist().get("data", [])
    packs = []
    seen = set()
    pending = pack_options()["pending_name"]
    for song in data:
        pack = song.get("曲包") or ""
        if pack and pack != pending and pack not in seen:
            seen.add(pack)
            packs.append(pack)
    return packs


def list_pending(data: list | None = None) -> list[dict]:
    if data is None:
        data = load_songlist().get("data", [])
    pending = pack_options()["pending_name"]
    return [song for song in data if (song.get("曲包") or "") == pending]


def normalize_latest_update_ids(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    seen = set()
    for item in raw:
        sid = str(item or "").strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
    return out


def merge_latest(this_run: list[str], previous: list[str]) -> list[str]:
    merged = []
    seen = set()
    for sid in this_run + previous:
        sid = (sid or "").strip()
        if sid and sid not in seen:
            seen.add(sid)
            merged.append(sid)
    return merged


def composer_map(info_rows: list[dict] | None = None) -> dict[str, str]:
    info_rows = info_rows if info_rows is not None else load_info_tsv()
    return {row["phigrosId"]: row.get("composer", "") for row in info_rows}


def update_from_info(logger=None, update_bpm: bool = False) -> dict:
    paths = pipeline_paths()
    packs = pack_options()
    log = logger.info if logger else print

    obj = load_songlist()
    data = obj.get("data", [])
    packs_found = existing_packs(data)
    if not data:
        log("警告：site/songlist.json 没有曲目，APK 中未匹配的曲将全部进入待分类")
    elif not packs_found:
        log("警告：songlist 里没有已确认曲包（可能是空骨架或全是待分类）。已有曲包的目录应放在 site/songlist.json")
    else:
        log("读取 songlist：%d 首，已识别曲包 %d 个" % (len(data), len(packs_found)))
    info_rows = load_info_tsv(paths["info_tsv"])
    diff_by_id = load_difficulty_by_id(paths["difficulty_tsv"])
    diff_by_title = load_difficulty_by_title(paths["difficulty_tsv"])
    bpm_map = load_bpm_tsv(paths["bpm_tsv"])

    occupied = set()
    new_entries = []
    changed_ids = []
    unmatched_info = []
    diff_logs = []
    bpm_logs = []

    for row in info_rows:
        pid = row["phigrosId"]
        title = normalize_title(row["title"]) or pid
        found = find_song_by_title(title, data, occupied)
        if found is None:
            unmatched_info.append(row)
            continue
        index, song = found
        occupied.add(index)
        sid = song.get("id") or ""
        diff = find_difficulty_for_song(song, diff_by_id, diff_by_title)
        if diff:
            before = dict(song.get("难度") or {})
            if apply_difficulty(song, diff):
                changed_ids.append(sid)
                diff_logs.append(
                    "  定数 %s (%s)  %s → %s"
                    % (song.get("标题") or title, sid, format_difficulty(before), format_difficulty(song.get("难度")))
                )
        if update_bpm and pid in bpm_map:
            old_bpm = song.get("bpm")
            if apply_bpm(song, *bpm_map[pid]):
                changed_ids.append(sid)
                bpm_logs.append(
                    "  BPM %s (%s)  %s → %s"
                    % (song.get("标题") or title, sid, old_bpm, song.get("bpm"))
                )

    pending_ids = next_pending_ids(data, len(unmatched_info), packs["pending_prefix"])
    for row, new_id in zip(unmatched_info, pending_ids):
        title = normalize_title(row["title"]) or row["phigrosId"]
        entry = new_song_entry(title, packs["pending_name"], new_id)
        diff = find_difficulty_for_song(entry, diff_by_id, diff_by_title)
        if diff:
            apply_difficulty(entry, diff)
        if row["phigrosId"] in bpm_map:
            apply_bpm(entry, *bpm_map[row["phigrosId"]])
        data.append(entry)
        new_entries.append(entry)
        changed_ids.append(new_id)

    this_run = []
    seen = set()
    for sid in changed_ids:
        sid = (sid or "").strip()
        if sid and sid not in seen:
            seen.add(sid)
            this_run.append(sid)

    changed = bool(new_entries or this_run)
    if changed:
        obj["data"] = [sanitize_song(song) for song in data]
        obj["lastUpdate"] = date.today().isoformat()
        if "latestUpdateSongIds" in obj:
            obj["latestUpdateSongIds"] = merge_latest(
                this_run,
                normalize_latest_update_ids(obj.get("latestUpdateSongIds")),
            )
        save_songlist(obj, paths["songlist"])

    pending_count = len(list_pending(data))
    if new_entries:
        log("新增待分类 %d 首：" % len(new_entries))
        for entry in new_entries:
            log("  %s  %s" % (entry.get("id"), entry.get("标题")))
    if diff_logs:
        log("更新定数 %d 首：" % len(diff_logs))
        for line in diff_logs:
            log(line)
    if bpm_logs:
        log("更新 BPM %d 首：" % len(bpm_logs))
        for line in bpm_logs:
            log(line)
    log(
        "songlist：新增 %d，变更定数/BPM %d，待分类 %d"
        % (len(new_entries), len(diff_logs) + len(bpm_logs), pending_count)
    )
    return {
        "new": new_entries,
        "changed_ids": this_run,
        "pending_count": pending_count,
        "diff_logs": diff_logs,
        "bpm_logs": bpm_logs,
        "songlist": obj,
    }


def assign_pack(song_ids: list[str], pack_name: str, chapter_code: str | None = None) -> list[tuple[str, str]]:
    """把待分类曲目写入正式曲包，并重命名已归档资源。"""
    from publish_resources import relocate_published

    pack_name = (pack_name or "").strip()
    if not pack_name:
        raise ValueError("曲包名不能为空")
    pending_name = pack_options()["pending_name"]
    if pack_name == pending_name:
        raise ValueError("不能把曲目分回待分类")

    obj = load_songlist()
    data = obj["data"]
    by_id = {song.get("id"): song for song in data}
    prefix, next_num = get_pack_prefix_and_next_num(data, pack_name)
    results = []
    for sid in song_ids:
        song = by_id.get(sid)
        if song is None:
            raise ValueError(f"找不到曲目 {sid}")
        new_id = f"{prefix}_{next_num}"
        next_num += 1
        old_id = song["id"]
        song["id"] = new_id
        song["曲包"] = pack_name
        relocate_published(old_id, new_id)
        data.remove(song)
        insert_at = last_index_of_pack(data, pack_name) + 1
        if insert_at <= 0:
            data.append(song)
        else:
            data.insert(insert_at, song)
        results.append((old_id, new_id))

    if chapter_code:
        mapping = load_pack_map()
        mapping[chapter_code] = pack_name
        save_pack_map(mapping)

    obj["data"] = [sanitize_song(song) for song in data]
    obj["lastUpdate"] = date.today().isoformat()
    if "latestUpdateSongIds" in obj:
        remap = dict(results)
        latest = [remap.get(item, item) for item in normalize_latest_update_ids(obj.get("latestUpdateSongIds"))]
        for _old_id, new_id in reversed(results):
            if new_id in latest:
                latest.remove(new_id)
            latest.insert(0, new_id)
        obj["latestUpdateSongIds"] = latest
    save_songlist(obj)
    return results


if __name__ == "__main__":
    from log import init_console_logger
    update_from_info(init_console_logger())
