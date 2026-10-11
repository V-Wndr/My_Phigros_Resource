# -*- coding: utf-8 -*-
"""按所选官方谱打包 Phira .pez。只写指定谱面，不清空、不重打整库。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from zipfile import ZipFile

from publish_resources import DIFFS, chart_folder
from settings import ROOT, pipeline_paths
from update_songlist import load_info_tsv


def levels_by_id() -> dict[str, list[str]]:
    path = pipeline_paths()["difficulty_tsv"]
    result = {}
    if not path.exists():
        return result
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n\r").split("\t")
            if parts and parts[0]:
                result[parts[0]] = parts[1:]
    return result


def list_chart_songs() -> list[dict]:
    """扫描 chart/{phigrosId}.0/ 里实际存在的官方 json。"""
    paths = pipeline_paths()
    info_by_id = {row["phigrosId"]: row for row in load_info_tsv()}
    level_map = levels_by_id()
    songs = []
    chart_dir = paths["chart_dir"]
    if not chart_dir.exists():
        return songs
    for folder in chart_dir.iterdir():
        if not folder.is_dir() or not folder.name.endswith(".0"):
            continue
        phigros_id = folder.name[:-2]
        info = info_by_id.get(phigros_id) or {}
        row = info.get("row") or []
        song_levels = level_map.get(phigros_id) or []
        charts = []
        for index, diff in enumerate(DIFFS):
            json_path = folder / f"{diff}.json"
            if not json_path.exists():
                continue
            charter = row[4 + index].strip() if len(row) > 4 + index else ""
            level = song_levels[index].strip() if index < len(song_levels) else ""
            charts.append({
                "diff": diff,
                "level": level,
                "charter": charter,
                "json": json_path,
            })
        if not charts:
            continue
        songs.append({
            "phigrosId": phigros_id,
            "title": (info.get("title") or phigros_id).strip(),
            "composer": (info.get("composer") or "").strip(),
            "illustrator": (info.get("illustrator") or "").strip(),
            "charts": charts,
        })
    songs.sort(key=lambda song: (song["title"].casefold(), song["phigrosId"].casefold()))
    return songs


def _first_file(*candidates: Path) -> Path | None:
    for path in candidates:
        if path is not None and path.exists():
            return path
    return None


def resolve_pack_files(phigros_id: str, diff: str, use_lowres: bool = True) -> dict:
    paths = pipeline_paths()
    folder = chart_folder(phigros_id, paths["chart_dir"])
    lowres = paths["root"] / "illustrationLowRes" / f"{phigros_id}.png"
    full = folder / f"{phigros_id}.png"
    if use_lowres:
        picture_candidates = (lowres, full)
    else:
        picture_candidates = (full, lowres)
    return {
        "json": folder / f"{diff}.json",
        "ogg": _first_file(folder / f"{phigros_id}.ogg", paths["root"] / "music" / f"{phigros_id}.ogg"),
        "png": _first_file(*picture_candidates),
    }


def info_text(song: dict, diff: str, level: str, charter: str) -> str:
    phigros_id = song["phigrosId"]
    level_part = f" Lv.{level}" if level else ""
    return (
        "#\n"
        f"Name: {song['title']}\n"
        f"Song: {phigros_id}.ogg\n"
        f"Picture: {phigros_id}.png\n"
        f"Chart: {phigros_id}.json\n"
        f"Level: {diff}{level_part}\n"
        f"Composer: {song['composer']}\n"
        f"Illustrator: {song['illustrator']}\n"
        f"Charter: {charter}"
    )


def pack_chart(song: dict, chart: dict, out_dir: Path, use_lowres: bool = True) -> Path:
    phigros_id = song["phigrosId"]
    diff = chart["diff"]
    files = resolve_pack_files(phigros_id, diff, use_lowres=use_lowres)
    missing = []
    if not files["json"].exists():
        missing.append(str(files["json"]))
    if not files["ogg"]:
        missing.append(f"{phigros_id}.ogg")
    if not files["png"]:
        missing.append(f"{phigros_id}.png")
    if missing:
        raise FileNotFoundError("缺少 " + "、".join(missing))
    dest = Path(out_dir)
    if not dest.is_absolute():
        dest = ROOT / dest
    pez_path = dest / diff / f"{phigros_id}-{diff}.pez"
    pez_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(pez_path, "w") as pez:
        pez.writestr(
            "info.txt",
            info_text(song, diff, chart.get("level") or "", chart.get("charter") or ""),
        )
        pez.write(files["json"], f"{phigros_id}.json")
        pez.write(files["png"], f"{phigros_id}.png")
        pez.write(files["ogg"], f"{phigros_id}.ogg")
    return pez_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把指定谱面打成 Phira .pez，不重打其它曲")
    parser.add_argument("phigros_id", help="chart 目录中的 phigrosId")
    parser.add_argument("diffs", nargs="+", help="EZ / HD / IN / AT")
    parser.add_argument("--out", default="phira", help="输出目录，默认 phira/")
    parser.add_argument("--full-picture", action="store_true", help="用原曲绘；默认优先低清 png")
    args = parser.parse_args(argv)
    wanted = [diff.upper() for diff in args.diffs]
    unknown = [diff for diff in wanted if diff not in DIFFS]
    if unknown:
        print("未知难度：" + "、".join(unknown), file=sys.stderr)
        return 1
    song = next((item for item in list_chart_songs() if item["phigrosId"] == args.phigros_id), None)
    if song is None:
        print(f"chart/ 里没有 {args.phigros_id}", file=sys.stderr)
        return 1
    by_diff = {chart["diff"]: chart for chart in song["charts"]}
    failed = 0
    for diff in wanted:
        chart = by_diff.get(diff)
        if chart is None:
            print(f"没有 {args.phigros_id} 的 {diff}.json", file=sys.stderr)
            failed += 1
            continue
        try:
            path = pack_chart(song, chart, Path(args.out), use_lowres=not args.full_picture)
        except Exception as error:
            print(f"{args.phigros_id} {diff}：{error}", file=sys.stderr)
            failed += 1
            continue
        print(path)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
