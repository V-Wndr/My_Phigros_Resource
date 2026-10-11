# -*- coding: utf-8 -*-
"""按 phigrosId 归档 chart/，站点 id 只用于 site/illustration webp。"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from settings import phi2malody_options, pipeline_paths
from update_songlist import apk_id_for_song, load_info_tsv, load_songlist


DIFFS = ("EZ", "HD", "IN", "AT")


def chart_folder(phigros_id: str, chart_dir: Path | None = None) -> Path:
    chart_dir = chart_dir or pipeline_paths()["chart_dir"]
    return chart_dir / f"{phigros_id}.0"


def link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dst.resolve():
        return
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def copy_if_needed(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dst.resolve():
        return
    shutil.copy2(src, dst)


def first_existing(*candidates: Path) -> Path | None:
    for path in candidates:
        if path and path.exists():
            return path
    return None


def source_paths(phigros_id: str, paths: dict) -> dict:
    work = paths["work_dir"]
    root = paths["root"]
    dest = chart_folder(phigros_id, paths["chart_dir"])
    return {
        "music": first_existing(
            work / "music" / f"{phigros_id}.ogg",
            root / "music" / f"{phigros_id}.ogg",
            dest / f"{phigros_id}.ogg",
        ),
        "illustration": first_existing(
            work / "illustration" / f"{phigros_id}.png",
            root / "illustration" / f"{phigros_id}.png",
            dest / f"{phigros_id}.png",
        ),
        "lowres": first_existing(
            work / "illustrationLowRes" / f"{phigros_id}.png",
            root / "illustrationLowRes" / f"{phigros_id}.png",
        ),
        "charts": {
            diff: first_existing(
                work / "chart" / f"{phigros_id}.0" / f"{diff}.json",
                dest / f"{diff}.json",
            )
            for diff in DIFFS
        },
    }


def write_webp(src: Path, dst: Path) -> None:
    from PIL import Image

    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as image:
        image.save(dst, "WEBP", quality=80, method=4)


def rewrite_mc_bindings(mc_path: Path, ogg_name: str, background: str) -> None:
    with open(mc_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    notes = data.get("note") or []
    if notes:
        notes[-1]["sound"] = ogg_name
    meta = data.setdefault("meta", {})
    meta["background"] = background
    with open(mc_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
        handle.write("\n")


def relocate_published(old_id: str, new_id: str) -> None:
    """分章只改站点资源。chart/ 按 phigrosId 归档，不随曲包 id 改名。"""
    paths = pipeline_paths()
    webp_old = paths["illustration_dir"] / f"{old_id}.webp"
    webp_new = paths["illustration_dir"] / f"{new_id}.webp"
    if webp_old.exists():
        if webp_new.exists() and webp_new.resolve() != webp_old.resolve():
            webp_new.unlink()
        webp_old.rename(webp_new)


def convert_one(phi_json: Path, out_mc: Path, title: str, artist: str, phigros_id: str, diff: str, options: dict) -> None:
    from phi2malody.convert import convert_chart

    convert_chart(
        str(phi_json),
        str(out_mc),
        title=title,
        artist=artist or "",
        ogg_name=f"{phigros_id}.ogg",
        background=f"{phigros_id}.png",
        version=diff,
        mode=options["mode"],
        time_mode=options["time_mode"],
        tempo_mode=options["tempo_mode"],
        quiet=True,
    )


def convert_overrides_path() -> Path:
    return pipeline_paths()["info_dir"] / "convert.json"


def _option_value(value, allowed: tuple[int, ...], fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number in allowed else fallback


def normalize_convert_options(raw: dict | None, defaults: dict | None = None) -> dict:
    defaults = defaults or {"mode": 2, "time_mode": 2, "tempo_mode": 1}
    raw = raw or {}
    return {
        "mode": _option_value(raw.get("mode", defaults["mode"]), (1, 2), int(defaults["mode"])),
        "time_mode": _option_value(raw.get("time_mode", defaults["time_mode"]), (1, 2), int(defaults["time_mode"])),
        "tempo_mode": _option_value(raw.get("tempo_mode", defaults["tempo_mode"]), (1, 2), int(defaults["tempo_mode"])),
    }


def load_convert_overrides() -> dict:
    path = convert_overrides_path()
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for phigros_id, song in data.items():
        if not isinstance(song, dict):
            continue
        charts = {}
        for diff in DIFFS:
            raw = song.get(diff)
            if isinstance(raw, dict):
                charts[diff] = normalize_convert_options(raw)
        if charts:
            cleaned[str(phigros_id)] = charts
    return cleaned


def write_convert_overrides(data: dict) -> None:
    path = convert_overrides_path()
    cleaned = {}
    for phigros_id in sorted(data):
        song = data[phigros_id]
        if not isinstance(song, dict):
            continue
        charts = {diff: normalize_convert_options(song[diff]) for diff in DIFFS if isinstance(song.get(diff), dict)}
        if charts:
            cleaned[phigros_id] = charts
    if not cleaned:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(cleaned, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def save_convert_override(phigros_id: str, diff: str, options: dict) -> None:
    data = load_convert_overrides()
    song = dict(data.get(phigros_id) or {})
    song[diff] = normalize_convert_options(options)
    data[phigros_id] = song
    write_convert_overrides(data)


def clear_convert_override(phigros_id: str, diff: str) -> None:
    clear_convert_overrides([(phigros_id, diff)])


def save_convert_overrides(entries: list[tuple[str, str, dict]]) -> None:
    if not entries:
        return
    data = load_convert_overrides()
    for phigros_id, diff, options in entries:
        song = dict(data.get(phigros_id) or {})
        song[diff] = normalize_convert_options(options)
        data[phigros_id] = song
    write_convert_overrides(data)


def clear_convert_overrides(keys: list[tuple[str, str]]) -> None:
    if not keys:
        return
    data = load_convert_overrides()
    for phigros_id, diff in keys:
        song = dict(data.get(phigros_id) or {})
        song.pop(diff, None)
        if song:
            data[phigros_id] = song
        else:
            data.pop(phigros_id, None)
    write_convert_overrides(data)


def publish_all(skip_convert: bool = False, clear_work: bool = True, logger=None) -> dict:
    paths = pipeline_paths()
    options = phi2malody_options()
    overrides = load_convert_overrides()
    log = logger.info if logger else print
    warn = logger.warning if logger and hasattr(logger, "warning") else log

    info_rows = load_info_tsv()
    obj = load_songlist(paths["songlist"])
    published = 0
    converted = 0
    custom_converted = 0
    missing = 0

    paths["chart_dir"].mkdir(parents=True, exist_ok=True)
    paths["illustration_dir"].mkdir(parents=True, exist_ok=True)

    for row in info_rows:
        phigros_id = row["phigrosId"]
        sources = source_paths(phigros_id, paths)
        dest = chart_folder(phigros_id, paths["chart_dir"])
        dest.mkdir(parents=True, exist_ok=True)

        if sources["music"]:
            link_or_copy(sources["music"], dest / f"{phigros_id}.ogg")
        else:
            warn("缺少音乐 %s" % phigros_id)
            missing += 1
        if sources["illustration"]:
            copy_if_needed(sources["illustration"], dest / f"{phigros_id}.png")
        else:
            warn("缺少曲绘 %s" % phigros_id)
            missing += 1

        has_chart = False
        for diff, chart_path in sources["charts"].items():
            if not chart_path:
                continue
            has_chart = True
            json_dest = dest / f"{diff}.json"
            copy_if_needed(chart_path, json_dest)
            if not skip_convert:
                custom = (overrides.get(phigros_id) or {}).get(diff)
                chart_options = custom or options
                if custom:
                    custom_converted += 1
                convert_one(
                    json_dest,
                    dest / f"{phigros_id}_{diff}.mc",
                    row.get("title") or phigros_id,
                    row.get("composer") or "",
                    phigros_id,
                    diff,
                    chart_options,
                )
                rewrite_mc_bindings(
                    dest / f"{phigros_id}_{diff}.mc",
                    f"{phigros_id}.ogg",
                    f"{phigros_id}.png",
                )
                converted += 1
        if has_chart or sources["music"] or sources["illustration"]:
            published += 1

    for song in obj.get("data", []):
        site_id = (song.get("id") or "").strip()
        phigros_id = apk_id_for_song(song, info_rows)
        if not site_id or not phigros_id:
            continue
        lowres = source_paths(phigros_id, paths)["lowres"]
        if lowres:
            write_webp(lowres, paths["illustration_dir"] / f"{site_id}.webp")

    if clear_work and paths["work_dir"].exists():
        shutil.rmtree(paths["work_dir"], ignore_errors=True)
        log("已清空 %s" % paths["work_dir"])

    if skip_convert:
        log("归档 %d 首，跳过转谱，缺文件提醒 %d" % (published, missing))
    else:
        log("归档 %d 首，转出 .mc %d 张（单独配置 %d 张），缺文件提醒 %d" % (
            published, converted, custom_converted, missing,
        ))
    return {
        "published": published,
        "converted": converted,
        "custom_converted": custom_converted,
        "missing": missing,
    }


if __name__ == "__main__":
    from log import init_console_logger
    publish_all(logger=init_console_logger())
