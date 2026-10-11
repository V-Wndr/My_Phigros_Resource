# -*- coding: utf-8 -*-
"""命令行流水线入口。与 GUI「开始」共用 run_pipeline。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from extract_bpm import extract_bpm
from log import init_console_logger
from settings import ROOT, pipeline_paths
from update_songlist import list_pending, pack_options, update_from_info


def attach_callback(logger: logging.Logger, callback) -> logging.Logger:
    if callback is None:
        return logger

    class CallbackHandler(logging.Handler):
        def emit(self, record):
            try:
                callback(self.format(record))
            except Exception:
                pass

    handler = CallbackHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def run_pipeline(
    apk: str | os.PathLike | None = None,
    skip_extract: bool = False,
    skip_convert: bool = False,
    update_bpm: bool = False,
    log=None,
    logger: logging.Logger | None = None,
) -> dict:
    os.chdir(str(ROOT))
    paths = pipeline_paths()
    logger = attach_callback(logger or init_console_logger(), log)
    apk_path = Path(apk) if apk else paths["apk"]
    if not apk_path.is_absolute():
        apk_path = ROOT / apk_path

    if not skip_extract and not apk_path.exists():
        raise FileNotFoundError(f"未找到 APK：{apk_path}")

    if not skip_extract:
        logger.info("1/5 解析 GameInformation → info/")
        import gameInformation
        gameInformation.run(str(apk_path), logger)

        logger.info("2/5 提取资源 → %s", paths["work_dir"])
        import resource as resource_mod
        from settings import resource_types
        paths["work_dir"].mkdir(parents=True, exist_ok=True)
        resource_mod.run(
            str(apk_path),
            logger,
            work_dir=str(paths["work_dir"]),
            type_config=resource_types(),
        )
    else:
        logger.info("跳过提取（--skip-extract）")
        if not paths["info_tsv"].exists():
            raise FileNotFoundError("缺少 info/info.tsv，无法跳过提取")

    logger.info("3/5 读取官方谱 BPM")
    extract_bpm(logger)

    logger.info("4/5 更新 site/songlist.json")
    if not update_bpm:
        logger.info("默认不检查已有曲的 BPM（需要时加 --update-bpm）")
    result = update_from_info(logger, update_bpm=update_bpm)

    logger.info("5/5 归档 chart/ 与站点低清图")
    from publish_resources import publish_all
    publish = publish_all(skip_convert=skip_convert, logger=logger)

    pending = list_pending()
    pending_name = pack_options()["pending_name"]
    if pending:
        logger.info(
            "流水线结束：%d 首在「%s」，请用 python gui.py 分章",
            len(pending),
            pending_name,
        )
    else:
        logger.info("流水线结束：没有待分类曲目")

    return {
        "songlist": result,
        "publish": publish,
        "pending": pending,
        "apk": str(apk_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phigros 资源流水线")
    parser.add_argument("apk", nargs="?", default=None, help="APK 路径，默认读 config.ini")
    parser.add_argument("--skip-extract", action="store_true", help="跳过 APK 解析与资源提取")
    parser.add_argument("--skip-convert", action="store_true", help="只归档，不转 .mc")
    parser.add_argument("--update-bpm", action="store_true", help="对照官方谱覆盖已有曲的 BPM，默认不检查")
    args = parser.parse_args(argv)
    try:
        run_pipeline(
            args.apk,
            skip_extract=args.skip_extract,
            skip_convert=args.skip_convert,
            update_bpm=args.update_bpm,
        )
    except Exception as error:
        print(f"流水线失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
