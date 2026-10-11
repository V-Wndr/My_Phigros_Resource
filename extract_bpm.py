# -*- coding: utf-8 -*-
"""从官方谱 JSON 读取 BPM，写入 info/bpm.tsv。"""

import json
from collections import Counter
from pathlib import Path

from settings import pipeline_paths


DIFF_ORDER = ("IN", "HD", "EZ", "AT")


def fmt_bpm(value: float) -> str:
    number = float(value)
    if abs(number - round(number)) < 1e-6:
        return str(int(round(number)))
    return str(round(number, 4)).rstrip("0").rstrip(".")


def parse_bpm_from_chart(chart_path: Path) -> tuple[str, list[str]] | None:
    with open(chart_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    lines = data.get("judgeLineList") or []
    if not lines:
        return None
    values = []
    for line in lines:
        try:
            values.append(float(line.get("bpm")))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    counts = Counter(values)
    max_count = max(counts.values())
    main = next(value for value in values if counts[value] == max_count)
    unique = []
    for value in values:
        text = fmt_bpm(value)
        if text not in unique:
            unique.append(text)
    return fmt_bpm(main), unique


def find_chart_dir(phigros_id: str, work_dir: Path, chart_dir: Path) -> Path | None:
    for candidate in (
        work_dir / "chart" / f"{phigros_id}.0",
        chart_dir / f"{phigros_id}.0",
    ):
        if candidate.is_dir():
            return candidate
    return None


def load_phigros_ids(info_tsv: Path) -> list[str]:
    ids = []
    if not info_tsv.exists():
        return ids
    with open(info_tsv, "r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n\r").split("\t")
            if parts and parts[0]:
                ids.append(parts[0])
    return ids


def extract_bpm(logger=None) -> int:
    paths = pipeline_paths()
    work_dir = paths["work_dir"]
    chart_dir = paths["chart_dir"]
    info_tsv = paths["info_tsv"]
    bpm_tsv = paths["bpm_tsv"]
    log = logger.info if logger else print

    ids = load_phigros_ids(info_tsv)
    if not ids:
        chart_root = work_dir / "chart"
        if not chart_root.is_dir():
            chart_root = chart_dir
        if chart_root.is_dir():
            for item in sorted(chart_root.iterdir()):
                if item.is_dir() and item.name.endswith(".0"):
                    ids.append(item.name[:-2])

    rows = []
    missing = 0
    for phigros_id in ids:
        folder = find_chart_dir(phigros_id, work_dir, chart_dir)
        picked = None
        if folder:
            for diff in DIFF_ORDER:
                chart_path = folder / f"{diff}.json"
                if chart_path.exists():
                    picked = parse_bpm_from_chart(chart_path)
                    if picked:
                        break
        if not picked:
            missing += 1
            continue
        main, all_bpms = picked
        rows.append((phigros_id, main, ",".join(all_bpms)))

    bpm_tsv.parent.mkdir(parents=True, exist_ok=True)
    with open(bpm_tsv, "w", encoding="utf-8", newline="\n") as handle:
        for phigros_id, main, all_bpms in rows:
            handle.write(f"{phigros_id}\t{main}\t{all_bpms}\n")
    log("写入 BPM %d 条（缺谱 %d）" % (len(rows), missing))
    return len(rows)


if __name__ == "__main__":
    from log import init_console_logger
    extract_bpm(init_console_logger())
