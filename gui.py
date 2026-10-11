# -*- coding: utf-8 -*-
"""流水线进度、待分类分章、单张转谱与选择性 Phira 打包。"""

from __future__ import annotations

import os
import sys
import traceback

from PyQt5.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from phira import list_chart_songs, pack_chart
from publish_resources import (
    chart_folder,
    clear_convert_overrides,
    convert_one,
    load_convert_overrides,
    rewrite_mc_bindings,
    save_convert_overrides,
)
from run import run_pipeline
from settings import ROOT, phi2malody_options, pipeline_paths

_CONVERT_LABELS = {
    "mode": {1: "2K", 2: "4K"},
    "time_mode": {1: "精确", 2: "吸附"},
    "tempo_mode": {1: "单主 BPM", 2: "连续变速"},
}


def _convert_label(kind: str, value: int) -> str:
    return _CONVERT_LABELS[kind].get(int(value), str(value))
from update_songlist import (
    apk_id_for_song,
    assign_pack,
    composer_map,
    existing_packs,
    get_pack_prefix_and_next_num,
    info_for_song,
    list_pending,
    load_chapters,
    load_info_tsv,
    load_pack_map,
    load_songlist,
    pack_options,
)


class PipelineWorker(QThread):
    log_line = pyqtSignal(str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, apk: str, skip_extract: bool, skip_convert: bool, update_bpm: bool = False):
        super().__init__()
        self.apk = apk
        self.skip_extract = skip_extract
        self.skip_convert = skip_convert
        self.update_bpm = update_bpm

    def run(self):
        try:
            result = run_pipeline(
                self.apk,
                skip_extract=self.skip_extract,
                skip_convert=self.skip_convert,
                update_bpm=self.update_bpm,
                log=self.log_line.emit,
            )
            self.finished_ok.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())


class ChartWorker(QThread):
    log_line = pyqtSignal(str)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, kind: str, jobs: list[dict], out_dir: str = "", use_lowres: bool = True):
        super().__init__()
        self.kind = kind
        self.jobs = jobs
        self.out_dir = out_dir
        self.use_lowres = use_lowres

    def run(self):
        try:
            if self.kind == "convert":
                summary = self._convert()
            else:
                summary = self._pack()
            self.finished_ok.emit(summary)
        except Exception:
            self.failed.emit(traceback.format_exc())

    def _convert(self) -> str:
        saved = []
        ok = 0
        failed = 0
        for job in self.jobs:
            title = job["title"]
            diff = job["diff"]
            phigros_id = job["phigrosId"]
            options = job["options"]
            try:
                folder = chart_folder(phigros_id)
                json_path = folder / f"{diff}.json"
                mc_path = folder / f"{phigros_id}_{diff}.mc"
                convert_one(
                    json_path,
                    mc_path,
                    title,
                    job.get("composer") or "",
                    phigros_id,
                    diff,
                    options,
                )
                rewrite_mc_bindings(mc_path, f"{phigros_id}.ogg", f"{phigros_id}.png")
            except Exception as error:
                failed += 1
                self.log_line.emit(f"转谱失败 {title} {diff}：{error}")
                continue
            saved.append((phigros_id, diff, options))
            ok += 1
            self.log_line.emit(
                "转谱 %s %s → %s（%s / %s / %s）" % (
                    title,
                    diff,
                    mc_path.name,
                    _convert_label("mode", options["mode"]),
                    _convert_label("time_mode", options["time_mode"]),
                    _convert_label("tempo_mode", options["tempo_mode"]),
                )
            )
        if saved:
            save_convert_overrides(saved)
            self.log_line.emit("已写入 info/convert.json，之后整库转谱会沿用这 %d 张的参数" % len(saved))
        return "转谱完成：成功 %d，失败 %d" % (ok, failed)

    def _pack(self) -> str:
        ok = 0
        failed = 0
        for job in self.jobs:
            title = job["title"]
            diff = job["diff"]
            try:
                path = pack_chart(job, job, self.out_dir, use_lowres=self.use_lowres)
            except Exception as error:
                failed += 1
                self.log_line.emit(f"打包失败 {title} {diff}：{error}")
                continue
            ok += 1
            self.log_line.emit(f"打包 {title} {diff} → {path}")
        return "打包完成：成功 %d，失败 %d" % (ok, failed)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        os.chdir(str(ROOT))
        self.setWindowTitle("Phigros Resource")
        self.resize(960, 680)
        self.worker = None
        self.chart_worker = None
        self.chart_worker_kind = ""
        self.chart_index = {}
        self.convert_overrides = {}
        self._updating_checks = False
        self._shown_override_key = None
        self.pending_songs = []
        self.chapters = {}
        self.composers = {}
        self.info_rows = []

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_extract_tab(), "提取")
        self.tabs.addTab(self._build_pending_tab(), "待分类")
        self.tabs.addTab(self._build_chart_tab(), "谱面")
        self.setCentralWidget(self.tabs)
        self.statusBar().showMessage("就绪")
        self.refresh_pending()
        self.refresh_chart_list()

    def _build_extract_tab(self) -> QWidget:
        paths = pipeline_paths()
        page = QWidget()
        layout = QVBoxLayout(page)

        row = QHBoxLayout()
        row.addWidget(QLabel("APK"))
        self.apk_edit = QLineEdit(str(paths["apk"]))
        row.addWidget(self.apk_edit, 1)
        browse = QPushButton("浏览")
        browse.clicked.connect(self.browse_apk)
        row.addWidget(browse)
        layout.addLayout(row)

        flags = QHBoxLayout()
        self.skip_extract_btn = QPushButton("跳过提取")
        self.skip_extract_btn.setCheckable(True)
        self.skip_convert_btn = QPushButton("跳过转谱")
        self.skip_convert_btn.setCheckable(True)
        self.update_bpm_btn = QPushButton("检查旧 BPM")
        self.update_bpm_btn.setCheckable(True)
        self.update_bpm_btn.setChecked(False)
        self.update_bpm_btn.setToolTip("默认不对照已有曲的 BPM；勾选后才用官方谱覆盖")
        flags.addWidget(self.skip_extract_btn)
        flags.addWidget(self.skip_convert_btn)
        flags.addWidget(self.update_bpm_btn)
        flags.addStretch(1)
        self.start_btn = QPushButton("开始")
        self.start_btn.clicked.connect(self.start_pipeline)
        flags.addWidget(self.start_btn)
        layout.addLayout(flags)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)
        return page

    def _build_pending_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("待分类曲目（可多选）"))
        self.pending_list = QListWidget()
        self.pending_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.pending_list.itemSelectionChanged.connect(self.show_selected)
        left_layout.addWidget(self.pending_list, 1)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.preview = QLabel("未选择")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(QSize(240, 135))
        self.preview.setStyleSheet("QLabel { background: #222; color: #ddd; }")
        right_layout.addWidget(self.preview)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        right_layout.addWidget(self.detail)

        box = QGroupBox("分章")
        box_layout = QVBoxLayout(box)
        pack_row = QHBoxLayout()
        pack_row.addWidget(QLabel("曲包"))
        self.pack_combo = QComboBox()
        self.pack_combo.setEditable(False)
        self.pack_combo.currentIndexChanged.connect(self.update_id_preview)
        pack_row.addWidget(self.pack_combo, 1)
        box_layout.addLayout(pack_row)
        self.id_preview = QLabel("选择曲目和曲包后，这里会显示即将写入的 id")
        self.id_preview.setWordWrap(True)
        box_layout.addWidget(self.id_preview)

        btn_row = QHBoxLayout()
        new_pack = QPushButton("新建曲包")
        new_pack.clicked.connect(self.create_pack)
        suggest = QPushButton("采用建议")
        suggest.clicked.connect(self.apply_suggestion)
        confirm = QPushButton("确认")
        confirm.clicked.connect(self.confirm_assign)
        btn_row.addWidget(new_pack)
        btn_row.addWidget(suggest)
        btn_row.addWidget(confirm)
        box_layout.addLayout(btn_row)
        right_layout.addWidget(box)
        right_layout.addWidget(QLabel("变更回显"))
        self.change_view = QPlainTextEdit()
        self.change_view.setReadOnly(True)
        self.change_view.setPlaceholderText("流水线新增、定数/BPM 变更、分章确认后的 id 会显示在这里")
        right_layout.addWidget(self.change_view, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return page

    def browse_apk(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 Phigros.apk", str(ROOT), "APK (*.apk);;所有文件 (*.*)")
        if path:
            self.apk_edit.setText(path)

    def _task_running(self) -> bool:
        return bool(
            (self.worker and self.worker.isRunning())
            or (self.chart_worker and self.chart_worker.isRunning())
        )

    def _set_busy(self, busy: bool):
        for button in (self.start_btn, self.convert_btn, self.pack_btn, self.clear_override_btn):
            button.setEnabled(not busy)

    def start_pipeline(self):
        if self._task_running():
            return
        self._set_busy(True)
        self.log_view.appendPlainText("—— 开始流水线 ——")
        self.worker = PipelineWorker(
            self.apk_edit.text().strip(),
            self.skip_extract_btn.isChecked(),
            self.skip_convert_btn.isChecked(),
            self.update_bpm_btn.isChecked(),
        )
        self.worker.log_line.connect(self.log_view.appendPlainText)
        self.worker.finished_ok.connect(self.pipeline_done)
        self.worker.failed.connect(self.pipeline_failed)
        self.worker.start()

    def pipeline_done(self, result: dict):
        self._set_busy(False)
        pending = result.get("pending") or []
        songlist = result.get("songlist") or {}
        self.refresh_pending()
        self.echo_pipeline(songlist)
        if pending:
            self.statusBar().showMessage(f"完成：{len(pending)} 首待分类")
            self.tabs.setCurrentIndex(1)
        else:
            self.statusBar().showMessage("完成：没有待分类")

    def echo_pipeline(self, songlist: dict):
        lines = []
        for entry in songlist.get("new") or []:
            lines.append(f"新增待分类  {entry.get('id')}  {entry.get('标题')}")
        for line in songlist.get("diff_logs") or []:
            lines.append(line.strip())
        for line in songlist.get("bpm_logs") or []:
            lines.append(line.strip())
        if not lines:
            self.change_view.appendPlainText("本轮没有 songlist 变更。")
            return
        self.change_view.appendPlainText("—— 流水线变更 ——")
        self.change_view.appendPlainText("\n".join(lines))

    def pipeline_failed(self, text: str):
        self._set_busy(False)
        self.log_view.appendPlainText(text)
        self.statusBar().showMessage("流水线失败")
        QMessageBox.critical(self, "流水线失败", text[-2000:])

    def refresh_pending(self):
        os.chdir(str(ROOT))
        self.pending_songs = list_pending()
        self.chapters = load_chapters()
        self.info_rows = load_info_tsv()
        self.composers = composer_map(self.info_rows)
        self.pending_list.clear()
        for song in self.pending_songs:
            title = song.get("标题") or ""
            sid = song.get("id") or "（无 id）"
            item = QListWidgetItem(f"{sid}    {title}")
            item.setData(Qt.UserRole, song.get("id"))
            self.pending_list.addItem(item)
        self.reload_packs()
        self.update_id_preview()
        self.statusBar().showMessage(f"待分类 {len(self.pending_songs)} 首")
        if not self.pending_songs:
            self.preview.setText("没有待分类")
            self.detail.setText("")
            self.id_preview.setText("没有待分类曲目")

    def reload_packs(self, selected: str | None = None):
        current = selected or self.current_pack_name()
        data = load_songlist().get("data", [])
        self.pack_combo.blockSignals(True)
        self.pack_combo.clear()
        for pack in existing_packs(data):
            prefix, next_num = get_pack_prefix_and_next_num(data, pack)
            self.pack_combo.addItem(f"{pack}    {prefix}_{next_num}", pack)
        if current:
            self.set_current_pack(current)
        self.pack_combo.blockSignals(False)

    def current_pack_name(self) -> str:
        data = self.pack_combo.currentData()
        if data:
            return str(data)
        return self.pack_combo.currentText().strip()

    def set_current_pack(self, pack_name: str):
        for index in range(self.pack_combo.count()):
            if self.pack_combo.itemData(index) == pack_name:
                self.pack_combo.setCurrentIndex(index)
                return
        data = load_songlist().get("data", [])
        prefix, next_num = get_pack_prefix_and_next_num(data, pack_name)
        self.pack_combo.addItem(f"{pack_name}    {prefix}_{next_num}", pack_name)
        self.pack_combo.setCurrentIndex(self.pack_combo.count() - 1)

    def upcoming_ids(self, pack_name: str, count: int) -> list[str]:
        if not pack_name or count <= 0:
            return []
        data = load_songlist().get("data", [])
        prefix, next_num = get_pack_prefix_and_next_num(data, pack_name)
        return [f"{prefix}_{next_num + offset}" for offset in range(count)]

    def update_id_preview(self):
        songs = self.selected_songs()
        pack_name = self.current_pack_name()
        if not songs:
            self.id_preview.setText("先在左侧选择曲目，再选曲包，这里会预览正式 id")
            return
        if not pack_name:
            self.id_preview.setText("请选择或新建曲包")
            return
        ids = self.upcoming_ids(pack_name, len(songs))
        lines = [
            f"{song.get('标题')}  {song.get('id')}  →  {new_id}"
            for song, new_id in zip(songs, ids)
        ]
        self.id_preview.setText("即将写入：\n" + "\n".join(lines))

    def selected_songs(self) -> list[dict]:
        ids = [item.data(Qt.UserRole) for item in self.pending_list.selectedItems()]
        by_id = {song.get("id"): song for song in self.pending_songs}
        return [by_id[sid] for sid in ids if sid in by_id]

    def show_selected(self):
        songs = self.selected_songs()
        if not songs:
            self.preview.setText("未选择")
            self.detail.setText("")
            return
        song = songs[0]
        apk_id = apk_id_for_song(song, self.info_rows)
        chapter = self.chapters.get(apk_id, {})
        info = info_for_song(song, self.info_rows)
        composer = info.get("composer") or self.composers.get(apk_id, "")
        suggestion = " / ".join(part for part in (
            chapter.get("code"),
            chapter.get("title"),
        ) if part) or "（无）"
        self.detail.setText(
            f"标题：{song.get('标题')}\n"
            f"作曲：{composer or '（info.tsv 无记录）'}\n"
            f"临时 id：{song.get('id')}\n"
            f"APK 建议章节：{suggestion}"
        )
        self.load_preview(song.get("id") or "", apk_id)
        self.update_id_preview()

    def load_preview(self, site_id: str, phigros_id: str):
        paths = pipeline_paths()
        candidates = [
            paths["illustration_dir"] / f"{site_id}.webp",
            paths["work_dir"] / "illustrationLowRes" / f"{phigros_id}.png",
            ROOT / "illustrationLowRes" / f"{phigros_id}.png",
        ]
        for path in candidates:
            if path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    self.preview.setPixmap(
                        pixmap.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    )
                    return
        self.preview.setText("无预览图")

    def create_pack(self):
        name, ok = QInputDialog.getText(self, "新建曲包", "曲包名称")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if name == pack_options()["pending_name"]:
            QMessageBox.warning(self, "无效名称", "不能使用待分类作为正式曲包。")
            return
        self.set_current_pack(name)
        self.update_id_preview()

    def apply_suggestion(self):
        songs = self.selected_songs()
        if not songs:
            return
        mapping = load_pack_map()
        codes = []
        for song in songs:
            chapter = self.chapters.get(apk_id_for_song(song, self.info_rows), {})
            codes.append(chapter.get("code") or "")
        if not codes or not all(codes) or len(set(codes)) != 1:
            QMessageBox.information(self, "采用建议", "所选曲目没有统一的 APK 章节建议。")
            return
        code = codes[0]
        pack = mapping.get(code)
        if not pack:
            chapter = self.chapters.get(apk_id_for_song(songs[0], self.info_rows), {})
            title = chapter.get("title") or ""
            packs = [self.pack_combo.itemText(i) for i in range(self.pack_combo.count())]
            pack = title if title in packs else ""
        if not pack:
            QMessageBox.information(
                self,
                "采用建议",
                f"章节代码 {code} 尚未写入 pack_map.json，请先选择或新建曲包。",
            )
            return
        self.set_current_pack(pack)
        self.update_id_preview()

    def confirm_assign(self):
        songs = self.selected_songs()
        pack_name = self.current_pack_name()
        if not songs:
            QMessageBox.information(self, "确认", "请先选择待分类曲目。")
            return
        if not pack_name:
            QMessageBox.information(self, "确认", "请选择或新建曲包。")
            return
        chapter_code = None
        codes = []
        for song in songs:
            chapter = self.chapters.get(apk_id_for_song(song, self.info_rows), {})
            if chapter.get("code"):
                codes.append(chapter["code"])
        if len(set(codes)) == 1:
            chapter_code = codes[0]
        try:
            results = assign_pack([song["id"] for song in songs], pack_name, chapter_code)
        except Exception as error:
            QMessageBox.critical(self, "分章失败", str(error))
            return
        echo = []
        title_by_old = {song.get("id"): song.get("标题") for song in songs}
        for old_id, new_id in results:
            echo.append(f"{title_by_old.get(old_id, old_id)}  {old_id}  →  {new_id}  （{pack_name}）")
        self.change_view.appendPlainText("—— 分章确认 ——")
        self.change_view.appendPlainText("\n".join(echo) if echo else "完成")
        self.refresh_pending()
        if echo:
            self.statusBar().showMessage(echo[-1])

    def _build_chart_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.chart_filter = QLineEdit()
        self.chart_filter.setPlaceholderText("筛选标题、作曲、谱师或 id")
        self.chart_filter.textChanged.connect(self.apply_chart_filter)
        left_layout.addWidget(self.chart_filter)
        self.chart_tree = QTreeWidget()
        self.chart_tree.setHeaderHidden(True)
        self.chart_tree.itemChanged.connect(self.on_chart_item_changed)
        left_layout.addWidget(self.chart_tree, 1)
        check_row = QHBoxLayout()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_chart_list)
        check_visible = QPushButton("勾选筛选结果")
        check_visible.clicked.connect(self.check_visible_charts)
        clear_checks = QPushButton("取消勾选")
        clear_checks.clicked.connect(self.clear_chart_checks)
        check_row.addWidget(refresh)
        check_row.addWidget(check_visible)
        check_row.addWidget(clear_checks)
        left_layout.addLayout(check_row)
        self.chart_count = QLabel("已勾选 0 张谱面")
        left_layout.addWidget(self.chart_count)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        convert_box = QGroupBox("转谱")
        convert_layout = QVBoxLayout(convert_box)
        defaults = phi2malody_options()
        convert_layout.addLayout(self._option_row(
            "键位", "mode_combo", ((2, "4K"), (1, "2K")), defaults["mode"],
        ))
        convert_layout.addLayout(self._option_row(
            "时间", "time_combo", ((2, "吸附"), (1, "精确")), defaults["time_mode"],
        ))
        convert_layout.addLayout(self._option_row(
            "BPM", "tempo_combo", ((1, "单主 BPM"), (2, "连续变速")), defaults["tempo_mode"],
        ))
        self.convert_hint = QLabel("")
        self.convert_hint.setWordWrap(True)
        convert_layout.addWidget(self.convert_hint)
        convert_btns = QHBoxLayout()
        self.convert_btn = QPushButton("转所选谱面")
        self.convert_btn.setToolTip("只重转勾选的官方 json，并记下这几张的参数")
        self.convert_btn.clicked.connect(self.start_convert)
        self.clear_override_btn = QPushButton("取消单独配置")
        self.clear_override_btn.setToolTip("删掉勾选谱面在 info/convert.json 里的记录，不立刻重转")
        self.clear_override_btn.clicked.connect(self.clear_selected_overrides)
        convert_btns.addWidget(self.convert_btn)
        convert_btns.addWidget(self.clear_override_btn)
        convert_layout.addLayout(convert_btns)
        right_layout.addWidget(convert_box)
        self._update_convert_hint()

        pack_box = QGroupBox("Phira 打包")
        pack_layout = QVBoxLayout(pack_box)
        pack_hint = QLabel(
            "只打包勾选的谱面。每个 .pez 自带该难度的谱、音乐和曲绘，"
            "不勾的难度不会各存一份音频。"
        )
        pack_hint.setWordWrap(True)
        pack_layout.addWidget(pack_hint)
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("输出"))
        self.pez_dir = QLineEdit(str(ROOT / "phira"))
        out_row.addWidget(self.pez_dir, 1)
        browse_out = QPushButton("浏览")
        browse_out.clicked.connect(self.browse_pez_dir)
        out_row.addWidget(browse_out)
        pack_layout.addLayout(out_row)
        self.lowres_check = QCheckBox("曲绘用低清 png（没有低清时用 chart 里的原图）")
        self.lowres_check.setChecked(True)
        pack_layout.addWidget(self.lowres_check)
        self.pack_btn = QPushButton("打包所选")
        self.pack_btn.clicked.connect(self.start_pack)
        pack_layout.addWidget(self.pack_btn)
        right_layout.addWidget(pack_box)

        right_layout.addWidget(QLabel("日志"))
        self.chart_log = QPlainTextEdit()
        self.chart_log.setReadOnly(True)
        self.chart_log.setPlaceholderText("转谱和打包结果会写在这里。已有的 phira 目录不会被清空。")
        right_layout.addWidget(self.chart_log, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return page

    def _option_row(self, label: str, attr: str, choices: tuple, current: int) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        combo = QComboBox()
        for value, text in choices:
            combo.addItem(text, value)
        for index in range(combo.count()):
            if combo.itemData(index) == current:
                combo.setCurrentIndex(index)
                break
        combo.currentIndexChanged.connect(self._update_convert_hint)
        setattr(self, attr, combo)
        row.addWidget(combo, 1)
        return row

    def _update_convert_hint(self):
        options = self.current_convert_options()
        self.convert_hint.setText(
            "本次：%s / %s / %s。转完记入 info/convert.json；"
            "之后整库转谱这些谱面沿用，其余仍用 config.ini。" % (
                _convert_label("mode", options["mode"]),
                _convert_label("time_mode", options["time_mode"]),
                _convert_label("tempo_mode", options["tempo_mode"]),
            )
        )

    def current_convert_options(self) -> dict:
        return {
            "mode": int(self.mode_combo.currentData()),
            "time_mode": int(self.time_combo.currentData()),
            "tempo_mode": int(self.tempo_combo.currentData()),
        }

    def _set_convert_options(self, options: dict):
        for combo, key in (
            (self.mode_combo, "mode"),
            (self.time_combo, "time_mode"),
            (self.tempo_combo, "tempo_mode"),
        ):
            combo.blockSignals(True)
            for index in range(combo.count()):
                if combo.itemData(index) == int(options[key]):
                    combo.setCurrentIndex(index)
                    break
            combo.blockSignals(False)
        self._update_convert_hint()

    def refresh_chart_list(self):
        checked = set(self._checked_chart_keys()) if hasattr(self, "chart_tree") else set()
        scroll = self.chart_tree.verticalScrollBar().value()
        self.convert_overrides = load_convert_overrides()
        self.chart_index = {}
        songs = list_chart_songs()
        self._updating_checks = True
        self.chart_tree.blockSignals(True)
        self.chart_tree.clear()
        for song in songs:
            parent = QTreeWidgetItem(["%s    %s" % (song["title"], song["composer"])])
            parent.setFlags(parent.flags() | Qt.ItemIsUserCheckable)
            parent.setCheckState(0, Qt.Unchecked)
            parent.setToolTip(0, song["phigrosId"])
            blob = [song["title"], song["composer"], song["illustrator"], song["phigrosId"]]
            for chart in song["charts"]:
                key = (song["phigrosId"], chart["diff"])
                level = ("Lv.%s  " % chart["level"]) if chart["level"] else ""
                mark = "  · 单独配置" if chart["diff"] in (self.convert_overrides.get(song["phigrosId"]) or {}) else ""
                child = QTreeWidgetItem(["%s    %s%s%s" % (chart["diff"], level, chart["charter"], mark)])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Checked if key in checked else Qt.Unchecked)
                child.setData(0, Qt.UserRole, key)
                parent.addChild(child)
                self.chart_index[key] = {
                    "phigrosId": song["phigrosId"],
                    "title": song["title"],
                    "composer": song["composer"],
                    "illustrator": song["illustrator"],
                    "diff": chart["diff"],
                    "level": chart["level"],
                    "charter": chart["charter"],
                }
                blob.extend([chart["diff"], chart["charter"], chart["level"]])
            parent.setData(0, Qt.UserRole + 1, " ".join(blob).casefold())
            self._sync_parent_check(parent)
            self.chart_tree.addTopLevelItem(parent)
        self.chart_tree.blockSignals(False)
        self._updating_checks = False
        self._shown_override_key = None
        self.apply_chart_filter()
        self.chart_tree.verticalScrollBar().setValue(scroll)
        self._update_chart_selection_label()

    def apply_chart_filter(self):
        text = self.chart_filter.text().strip().casefold()
        for index in range(self.chart_tree.topLevelItemCount()):
            parent = self.chart_tree.topLevelItem(index)
            blob = parent.data(0, Qt.UserRole + 1) or ""
            parent.setHidden(bool(text) and text not in blob)

    def on_chart_item_changed(self, item, _column):
        if self._updating_checks:
            return
        self._updating_checks = True
        try:
            state = item.checkState(0)
            if item.childCount() and state in (Qt.Checked, Qt.Unchecked):
                for index in range(item.childCount()):
                    item.child(index).setCheckState(0, state)
            parent = item.parent()
            if parent is not None:
                self._sync_parent_check(parent)
        finally:
            self._updating_checks = False
        self._update_chart_selection_label()
        self._maybe_load_single_override()

    def _sync_parent_check(self, parent):
        if parent.childCount() == 0:
            parent.setCheckState(0, Qt.Unchecked)
            return
        states = [parent.child(index).checkState(0) for index in range(parent.childCount())]
        if all(state == Qt.Checked for state in states):
            parent.setCheckState(0, Qt.Checked)
        elif all(state == Qt.Unchecked for state in states):
            parent.setCheckState(0, Qt.Unchecked)
        else:
            parent.setCheckState(0, Qt.PartiallyChecked)

    def _checked_chart_keys(self) -> list[tuple]:
        keys = []
        if not hasattr(self, "chart_tree"):
            return keys
        for index in range(self.chart_tree.topLevelItemCount()):
            parent = self.chart_tree.topLevelItem(index)
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                if child.checkState(0) == Qt.Checked:
                    keys.append(tuple(child.data(0, Qt.UserRole)))
        return keys

    def _update_chart_selection_label(self):
        self.chart_count.setText("已勾选 %d 张谱面" % len(self._checked_chart_keys()))

    def _maybe_load_single_override(self):
        keys = self._checked_chart_keys()
        if len(keys) != 1:
            self._shown_override_key = None
            return
        key = keys[0]
        if key == self._shown_override_key:
            return
        self._shown_override_key = key
        custom = (self.convert_overrides.get(key[0]) or {}).get(key[1])
        if custom:
            self._set_convert_options(custom)

    def _set_children_checked(self, state, visible_only: bool):
        self._updating_checks = True
        self.chart_tree.blockSignals(True)
        try:
            for index in range(self.chart_tree.topLevelItemCount()):
                parent = self.chart_tree.topLevelItem(index)
                if visible_only and parent.isHidden():
                    continue
                for child_index in range(parent.childCount()):
                    parent.child(child_index).setCheckState(0, state)
                self._sync_parent_check(parent)
        finally:
            self.chart_tree.blockSignals(False)
            self._updating_checks = False
        self._shown_override_key = None
        self._update_chart_selection_label()

    def check_visible_charts(self):
        self._set_children_checked(Qt.Checked, visible_only=True)

    def clear_chart_checks(self):
        self._set_children_checked(Qt.Unchecked, visible_only=False)

    def chart_jobs(self) -> list[dict]:
        options = self.current_convert_options()
        jobs = []
        for key in self._checked_chart_keys():
            meta = self.chart_index.get(key)
            if not meta:
                continue
            job = dict(meta)
            job["options"] = dict(options)
            jobs.append(job)
        return jobs

    def browse_pez_dir(self):
        current = self.pez_dir.text().strip() or str(ROOT)
        path = QFileDialog.getExistingDirectory(self, "Phira 输出目录", current)
        if path:
            self.pez_dir.setText(path)

    def start_convert(self):
        self._start_chart_job("convert")

    def start_pack(self):
        self._start_chart_job("pack")

    def _start_chart_job(self, kind: str):
        if self._task_running():
            QMessageBox.information(self, "请稍候", "上一项还在跑。")
            return
        jobs = self.chart_jobs()
        if not jobs:
            QMessageBox.information(self, "谱面", "请先勾选要处理的谱面。")
            return
        out_dir = self.pez_dir.text().strip() or "phira"
        self.chart_worker_kind = kind
        self._set_busy(True)
        if kind == "convert":
            options = jobs[0]["options"]
            self.chart_log.appendPlainText(
                "—— 转谱 %d 张：%s / %s / %s ——" % (
                    len(jobs),
                    _convert_label("mode", options["mode"]),
                    _convert_label("time_mode", options["time_mode"]),
                    _convert_label("tempo_mode", options["tempo_mode"]),
                )
            )
        else:
            picture = "低清曲绘" if self.lowres_check.isChecked() else "原曲绘"
            self.chart_log.appendPlainText("—— 打包 %d 张 → %s（%s）——" % (len(jobs), out_dir, picture))
        self.chart_worker = ChartWorker(
            kind,
            jobs,
            out_dir=out_dir,
            use_lowres=self.lowres_check.isChecked(),
        )
        self.chart_worker.log_line.connect(self.chart_log.appendPlainText)
        self.chart_worker.finished_ok.connect(self.chart_done)
        self.chart_worker.failed.connect(self.chart_failed)
        self.chart_worker.start()

    def chart_done(self, summary: str):
        kind = self.chart_worker_kind
        self._set_busy(False)
        self.chart_log.appendPlainText(summary)
        self.statusBar().showMessage(summary)
        if kind == "convert":
            self.refresh_chart_list()

    def chart_failed(self, text: str):
        self._set_busy(False)
        self.chart_log.appendPlainText(text)
        self.statusBar().showMessage("谱面操作失败")
        QMessageBox.critical(self, "谱面操作失败", text[-2000:])

    def clear_selected_overrides(self):
        if self._task_running():
            QMessageBox.information(self, "请稍候", "上一项还在跑。")
            return
        keys = self._checked_chart_keys()
        if not keys:
            QMessageBox.information(self, "单独配置", "请先勾选谱面。")
            return
        clear_convert_overrides(keys)
        self.chart_log.appendPlainText(
            "已取消 %d 张的单独配置。已生成的 .mc 还在，下次整库转谱会按 config.ini 重转。" % len(keys)
        )
        self.refresh_chart_list()


def main():
    os.chdir(str(ROOT))
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
