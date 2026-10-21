# -*- coding: utf-8 -*-
"""项目路径与 config.ini 读取。"""

from configparser import ConfigParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.ini"


def load_ini() -> ConfigParser:
    parser = ConfigParser()
    parser.optionxform = str
    parser.read(CONFIG_PATH, encoding="utf8")
    return parser


def _get(parser: ConfigParser, section: str, key: str, fallback: str) -> str:
    if parser.has_section(section) and parser.has_option(section, key):
        return parser.get(section, key)
    return fallback


def _getint(parser: ConfigParser, section: str, key: str, fallback: int) -> int:
    if parser.has_section(section) and parser.has_option(section, key):
        return parser.getint(section, key)
    return fallback


def pipeline_paths(parser: ConfigParser | None = None) -> dict:
    parser = parser or load_ini()
    work_dir = ROOT / _get(parser, "PIPELINE", "work_dir", "_work")
    chart_dir = ROOT / _get(parser, "PIPELINE", "chart_dir", "chart")
    site_dir = ROOT / _get(parser, "PIPELINE", "site_dir", "site")
    apk_name = _get(parser, "PIPELINE", "apk", "Phigros.apk")
    apk = Path(apk_name)
    if not apk.is_absolute():
        apk = ROOT / apk
    return {
        "root": ROOT,
        "apk": apk,
        "work_dir": work_dir,
        "chart_dir": chart_dir,
        "site_dir": site_dir,
        "info_dir": ROOT / "info",
        "songlist": site_dir / "songlist.json",
        "illustration_dir": site_dir / "illustration",
        "pack_map": ROOT / "pack_map.json",
        "song_map": ROOT / "song_map.json",
        "info_tsv": ROOT / "info" / "info.tsv",
        "difficulty_tsv": ROOT / "info" / "difficulty.tsv",
        "bpm_tsv": ROOT / "info" / "bpm.tsv",
        "chapters_tsv": ROOT / "info" / "chapters.tsv",
    }


def phi2malody_options(parser: ConfigParser | None = None) -> dict:
    parser = parser or load_ini()
    return {
        "mode": _getint(parser, "PHI2MALODY", "mode", 2),
        "time_mode": _getint(parser, "PHI2MALODY", "time_mode", 2),
        "tempo_mode": _getint(parser, "PHI2MALODY", "tempo_mode", 1),
    }


def pack_options(parser: ConfigParser | None = None) -> dict:
    parser = parser or load_ini()
    return {
        "pending_name": _get(parser, "PACK", "pending_name", "待分类"),
        "pending_prefix": _get(parser, "PACK", "pending_prefix", "Pending"),
    }


def resource_types(parser: ConfigParser | None = None) -> dict:
    parser = parser or load_ini()
    types = parser["TYPES"] if parser.has_section("TYPES") else {}
    update = parser["UPDATE"] if parser.has_section("UPDATE") else {}

    def flag(key: str, default: bool) -> bool:
        if not types:
            return default
        return types.getboolean(key, fallback=default)

    return {
        "avatar": flag("avatar", False),
        "chart": flag("Chart", True),
        "illustrationBlur": flag("IllustrationBlur", False),
        "illustrationLowRes": flag("IllustrationLowRes", True),
        "illustration": flag("Illustration", True),
        "music": flag("music", True),
        "UPDATE": {
            "main_story": int(update.get("main_story", 0)) if update else 0,
            "side_story": int(update.get("side_story", 0)) if update else 0,
            "other_song": int(update.get("other_song", 0)) if update else 0,
        },
    }
