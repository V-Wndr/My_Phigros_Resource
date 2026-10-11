#   期望功能：
#   1、能够将phi谱面转换为malody谱面，不提取黄键，同时将note分轨填充，红键填4，5轨道，蓝键长条填1，2轨道，出现长条就填2轨，填不进就3轨
#   2、导出Malody谱面
#   额外功能：UI界面，显示谱面预览
# type1,2,3,4分别是蓝键，黄键，长条，红键
# malody note结构： [aa,bb,cc] aa为小节编号（一拍一小节），bb为小节内对应横线编号，cc为放置音符时选择的拍子切割数1/c
import contextlib
import io
import json
import math
import os

MODE = 2  # 默认 4K；1=2K
TIME_MODE = 2  # 1=精确(tick约分，含128分) 2=吸附(靠近12/24分吸到/3/6，否则精确)；默认吸附
TEMPO_MODE = 1  # 1=单主BPM(不写多段time[]) 2=连续精确+time[]

PHI_TICKS_PER_BEAT = 32          # 1拍=32 tick；1 tick=128分
SNAP_24_THRESHOLD = 0.5          # 吸附模式：与24分理想格距离≤该值(tick)则吸附

# 4K模式轨道（Malody 0-indexed）：tap用0-3对应1-4轨，hold用6对应7轨，红键用4-5对应5-6轨
TAP_4K_LEFT_PRIORITY = [1, 0]    # 左半屏：2轨优先，其次1轨
TAP_4K_RIGHT_PRIORITY = [2, 3]   # 右半屏：3轨优先，其次4轨
TAP_4K_TWO_NOTE_COLUMNS = [1, 2] # 同刻仅两note：2轨、3轨
HOLD_4K_COLUMN = 6               # 长条统一7轨
FLICK_COLUMN_OFFSET = 3          # 2K红键相对左右轨偏移 → 4/5轨
FLICK_4K_COLUMN_OFFSET = 4       # 4K红键相对左右轨偏移 → 5/6轨

def getPhiJson():
    with open("../Phi2Malody/Chart/message Chart_AT.json", 'r', newline='') as MapFile:
        content = MapFile.read()
        jsonFile = json.loads(content)
    MapFile.close()
    return jsonFile

# Phi: 1 tick = 1.875/lineBpm 秒；32 tick = 1 拍（相对该判定线 bpm）
def phiTickToSeconds(phiTick, lineBpm, chartOffsetSec=0.0):
    return float(chartOffsetSec) + float(phiTick) * 1.875 / float(lineBpm)


def inferTempoSegments(noteSecBpmList):
    """
    假定：不同 bpm 判定线的音符在时间上不重叠，线 bpm 即歌曲变速。
    按音符起始现实时间扫描，在 bpm 变化处切段。
    返回 [(start_sec, bpm), ...]，第一段从 0 秒开始。
    """
    if not noteSecBpmList:
        return [(0.0, 120.0)]

    ordered = sorted(noteSecBpmList, key=lambda x: (x[0], x[1]))
    # 同刻多 bpm → 约定被破坏
    by_time = {}
    for sec, bpm in ordered:
        key = round(sec, 6)
        by_time.setdefault(key, set()).add(bpm)
    conflicts = {t: bpms for t, bpms in by_time.items() if len(bpms) > 1}
    if conflicts:
        sample = list(conflicts.items())[:5]
        raise ValueError(
            '同刻出现多种判定线 BPM，无法推断全局 time[] 变速。样例: %s' % sample
        )

    segments = [(0.0, float(ordered[0][1]))]
    cur = float(ordered[0][1])
    for sec, bpm in ordered:
        bpm = float(bpm)
        if abs(bpm - cur) > 1e-9:
            segments.append((float(sec), bpm))
            cur = bpm
    return segments


def buildTimebase(segments):
    """
    segments: [(start_sec, bpm), ...]
    返回 starts 表：starts[i] = (start_sec, start_beat, bpm)
    """
    starts = []
    beat = 0.0
    for i, (sec, bpm) in enumerate(segments):
        starts.append((float(sec), beat, float(bpm)))
        if i + 1 < len(segments):
            dt = float(segments[i + 1][0]) - float(sec)
            beat += dt * float(bpm) / 60.0
    return starts


def secondsToBeat(sec, timebaseStarts):
    sec = float(sec)
    idx = 0
    for i, (t0, _, _) in enumerate(timebaseStarts):
        if t0 <= sec:
            idx = i
        else:
            break
    t0, b0, bpm = timebaseStarts[idx]
    return b0 + (sec - t0) * bpm / 60.0


def beatToTick(beat):
    return float(beat) * PHI_TICKS_PER_BEAT


def collectRawNotes(noteJsonFile):
    """收集 (type, sec0, sec1, pos, lineBpm)，并返回统计与主 BPM。"""
    judgeLineList = noteJsonFile["judgeLineList"]
    chartOffsetSec = float(noteJsonFile.get("offset") or 0.0)
    masterBpm = float(judgeLineList[0]["bpm"])
    upNoteNum, downNoteNum, rawNotes = [], [], []

    for eachLine in judgeLineList:
        lineBpm = float(eachLine["bpm"])
        noteListUp = eachLine["notesAbove"]
        noteListDown = eachLine["notesBelow"]
        upNoteNum.append(len(noteListUp))
        downNoteNum.append(len(noteListDown))
        for note in noteListUp + noteListDown:
            if note["type"] == 2:
                continue
            sec0 = phiTickToSeconds(note["time"], lineBpm, chartOffsetSec)
            sec1 = phiTickToSeconds(note["time"] + note["holdTime"], lineBpm, chartOffsetSec)
            rawNotes.append((note["type"], sec0, sec1, note["positionX"], lineBpm))
        print("line finish,line bpm = ", lineBpm)

    print("UpNotenum: ", upNoteNum)
    print("DownNotenum: ", downNoteNum)
    print("notesum: ", sum(upNoteNum) + sum(downNoteNum))
    print("chart offset(s): ", chartOffsetSec, " master bpm: ", masterBpm)
    return rawNotes, masterBpm, chartOffsetSec


def mapNotesSingleMaster(rawNotes, masterBpm):
    """
    TEMPO_MODE=1：不写多段 time[]。
    各线先转现实秒，再统一映到主 BPM 拍轴（跨线异 bpm 仍秒级对齐）。
    """
    entireNoteList = []
    for noteType, sec0, sec1, pos, lineBpm in rawNotes:
        b0 = float(sec0) * float(masterBpm) / 60.0
        b1 = float(sec1) * float(masterBpm) / 60.0
        entireNoteList.append((noteType, beatToTick(b0), beatToTick(b1 - b0), pos, lineBpm))
    entireNoteList.sort(key=lambda x: x[1])
    bpmAnchors = [(0, float(masterBpm))]
    print("TEMPO_MODE=1 单主BPM，time[] 仅一段:", masterBpm)
    return entireNoteList, bpmAnchors


def mapNotesContinuous(rawNotes, segments):
    """
    TEMPO_MODE=2：推断 time[] 变速，连续积分，听感/秒级完全准确；
    变速点与音符可不在全局正拍/半拍上。
    """
    timebaseStarts = buildTimebase(segments)
    entireNoteList = []
    for noteType, sec0, sec1, pos, lineBpm in rawNotes:
        b0 = secondsToBeat(sec0, timebaseStarts)
        b1 = secondsToBeat(sec1, timebaseStarts)
        entireNoteList.append((noteType, beatToTick(b0), beatToTick(b1 - b0), pos, lineBpm))
    entireNoteList.sort(key=lambda x: x[1])
    bpmAnchors = [(start_beat, bpm) for (_sec, start_beat, bpm) in timebaseStarts]
    print(
        "TEMPO_MODE=2 连续精确 time[]:",
        [(round(s, 4), round(b, 4), bpm) for (s, b, bpm) in timebaseStarts],
    )
    return entireNoteList, bpmAnchors


def extractBpm(bpmAnchors, beatOffset=0):
    """
    bpmAnchors: [(start_beat, bpm), ...]
    整数拍写 [n,0,1]；非整数（连续精确模式）走 mapTimeExchange。
    """
    maBpmForm = []
    for i, (beat_k, bpm) in enumerate(bpmAnchors):
        beat_k = float(beat_k)
        if i == 0 or abs(beat_k) < 1e-12:
            beat_pos = [beatOffset, 0, 1]
        elif abs(beat_k - round(beat_k)) < 1e-8:
            beat_pos = [int(round(beat_k)) + beatOffset, 0, 1]
        else:
            a, b, c = mapTimeExchange(beatToTick(beat_k))
            beat_pos = [a + beatOffset, b, c]
        maBpmForm.append({'beat': beat_pos, 'bpm': float(bpm)})
    return maBpmForm


def extractnote(noteJsonFile, tempoMode=None):
    """
    按 TEMPO_MODE 提取并映射 note。
    1 单主BPM（无多段 time[]）
    2 连续精确 + time[]
    返回 (entireNoteList, bpmAnchors, chartOffsetSec)
    """
    if tempoMode is None:
        tempoMode = TEMPO_MODE

    rawNotes, masterBpm, chartOffsetSec = collectRawNotes(noteJsonFile)
    lineBpms = sorted({float(n[4]) for n in rawNotes}) if rawNotes else [masterBpm]
    if len(lineBpms) > 1:
        print('检测到多判定线 BPM:', lineBpms)

    if tempoMode == 1:
        entireNoteList, bpmAnchors = mapNotesSingleMaster(rawNotes, masterBpm)
    elif tempoMode == 2:
        segments = inferTempoSegments([(n[1], n[4]) for n in rawNotes])
        entireNoteList, bpmAnchors = mapNotesContinuous(rawNotes, segments)
    else:
        raise ValueError('未知 TEMPO_MODE: %s (应为 1/2)' % tempoMode)

    return entireNoteList, bpmAnchors, chartOffsetSec

def assign4kTapColumns(tap_notes):
    """同一phi时刻的tap列表，返回 [(note, column), ...]"""
    n = len(tap_notes)
    if n == 0:
        return []

    if n == 2:
        return [(tap_notes[i], TAP_4K_TWO_NOTE_COLUMNS[i]) for i in range(2)]

    used = set()
    result = []
    pos_count = {}

    for note in tap_notes:
        pos = note[3]
        pool = TAP_4K_LEFT_PRIORITY if pos < 0 else TAP_4K_RIGHT_PRIORITY
        same_pos_idx = pos_count.get(pos, 0)
        pos_count[pos] = same_pos_idx + 1

        col = None
        for i in range(len(pool)):
            candidate = pool[(same_pos_idx + i) % len(pool)]
            if candidate not in used:
                col = candidate
                break

        if col is None and len(used) < 4:
            other_pool = TAP_4K_RIGHT_PRIORITY if pos < 0 else TAP_4K_LEFT_PRIORITY
            for candidate in other_pool:
                if candidate not in used:
                    col = candidate
                    break

        if col is not None:
            used.add(col)
            result.append((note, col))

    return result

def build4kTapColumnMap(entireNoteList):
    tap_by_time = {}
    for i, note in enumerate(entireNoteList):
        if note[0] == 1:
            tap_by_time.setdefault(note[1], []).append((i, note))

    column_map = {}
    for group in tap_by_time.values():
        notes = [n for _, n in group]
        assignments = assign4kTapColumns(notes)
        for (idx, _), (_, col) in zip(group, assignments):
            column_map[idx] = col
    return column_map

def putInTrack2K(entireNoteList, mapTemplate, offset):

    #根据位置决定轨道
    def placeNote(position):
        if position <= 0:
            return 0
        else: return 1
    # {"beat":[38,3,4],"column":1} 蓝键
    # {"beat":[9,2,4],"endbeat":[16,2,4],"column":0} 长条
    #  提取4轨道，tap note默认一轨，若冲突则放到二轨，同理红键放于3,4轨道，如果出现3押和长条，保证一轨蓝键一轨长条

    #noteDict初始化
    noteDict = {"beat":[0,0,8],"column":0}
    # holdDict = {"beat":[10,0,4],"endbeat":[10,2,4],"column":0} 默认时值八分，也就是占据了16个phi time
    lastNote = (0,0,0,0)
    dualPressAtBeat = set()  # 记录已构成双押的 beat 位置

    def placeTapOrFlick(noteDict, noteTime, noteP, lastNote, columnOffset=0):
        baseCol = placeNote(noteP)
        beatKey = tuple(noteDict['beat'])
        noteDict['column'] = baseCol + columnOffset

        if noteTime != lastNote[1]:
            mapTemplate['note'].insert(-2, noteDict)
        elif placeNote(lastNote[3]) != placeNote(noteP):
            mapTemplate['note'].insert(-2, noteDict)
            dualPressAtBeat.add(beatKey)
        elif beatKey not in dualPressAtBeat:
            # 同位置且尚未双押：补到对面轨形成双押
            noteDict['column'] = (1 - baseCol) + columnOffset
            mapTemplate['note'].insert(-2, noteDict)
            dualPressAtBeat.add(beatKey)

    for note in entireNoteList:  # [0] type [1] time [2] holdtime [3] position

        # 确定下一个note的放置位置
        placeP = mapTimeExchange(note[1])  # (note['type'],note['time'],note['holdTime'],note['positionX'])
        section = placeP[0]
        subSection = placeP[1]
        splitNum = placeP[2]

        noteType = note[0]
        noteTime = note[1]
        noteHoldTime = note[2]
        noteP = note[3]
        noteEndTime = noteTime + noteHoldTime  # hold稍微复杂点，要确定一个时间范围


        # 需要转换note的放置位置，phi中每过32时间为一拍，间隔16为八分音，间隔32为4分音
        noteDict1 = {"beat":[section + offset,subSection,splitNum],"column":placeNote(noteP)}
        endBeat = mapTimeExchange(noteEndTime)
        noteDict2 = {"beat":[section + offset,subSection,splitNum],"endbeat":[endBeat[0],endBeat[1],endBeat[2]], "column":placeNote(noteP)}

        if entireNoteList.index(note) == 0:
            noteDict1['column'] = 3
            mapTemplate['note'].insert(-2, noteDict1)
            continue
        # 确定放置轨道
        if noteType == 1:  # tap
            placeTapOrFlick(noteDict1, noteTime, noteP, lastNote)

        elif noteType == 3:  # hold
            if entireNoteList.index(note) != len(entireNoteList) - 1:
                nextNoteCandidate = entireNoteList[entireNoteList.index(note) + 1]

            while (placeNote(nextNoteCandidate[3]) != placeNote(noteP) and entireNoteList.index(note) == len(entireNoteList) - 1):  # 按顺序找到下一个该轨道的note
                nextNoteCandidate = entireNoteList[entireNoteList.index(nextNoteCandidate) + 1]
            else: nextNote = note

            if noteTime != lastNote[1] and noteEndTime <= nextNote[1]:  # 比较放置时间，如果不同，且持续范围内没有其他note
                mapTemplate['note'].insert(-2,noteDict2)
            else:
                noteDict2['column'] = 2 # 冲突丢到3轨
                mapTemplate['note'].insert(-2,noteDict2)

        elif noteType == 4:  # flick
            placeTapOrFlick(noteDict1, noteTime, noteP, lastNote, columnOffset=FLICK_COLUMN_OFFSET)

        else: pass  # drag其实已经在entirelist中过滤掉了

        lastNote = note

    outputJson = mapTemplate
    print(type(outputJson))
    return outputJson

def putInTrack4K(entireNoteList, mapTemplate, offset):
    def placeNote(position):
        if position <= 0:
            return 0
        else:
            return 1

    lastNote = (0, 0, 0, 0)
    dualPressAtBeat = set()
    tap_column_map = build4kTapColumnMap(entireNoteList)

    def placeTapOrFlick(noteDict, noteTime, noteP, lastNote, columnOffset=0):
        baseCol = placeNote(noteP)
        beatKey = tuple(noteDict['beat'])
        noteDict['column'] = baseCol + columnOffset

        if noteTime != lastNote[1]:
            mapTemplate['note'].insert(-2, noteDict)
        elif placeNote(lastNote[3]) != placeNote(noteP):
            mapTemplate['note'].insert(-2, noteDict)
            dualPressAtBeat.add(beatKey)
        elif beatKey not in dualPressAtBeat:
            noteDict['column'] = (1 - baseCol) + columnOffset
            mapTemplate['note'].insert(-2, noteDict)
            dualPressAtBeat.add(beatKey)

    for i, note in enumerate(entireNoteList):
        placeP = mapTimeExchange(note[1])
        section, subSection, splitNum = placeP
        noteType, noteTime, noteHoldTime, noteP = note[0], note[1], note[2], note[3]
        noteEndTime = noteTime + noteHoldTime

        noteDict1 = {"beat": [section + offset, subSection, splitNum], "column": 0}
        endBeat = mapTimeExchange(noteEndTime)
        noteDict2 = {
            "beat": [section + offset, subSection, splitNum],
            "endbeat": [endBeat[0], endBeat[1], endBeat[2]],
            "column": HOLD_4K_COLUMN,
        }

        if noteType == 1:
            if i in tap_column_map:
                noteDict1['column'] = tap_column_map[i]
                mapTemplate['note'].insert(-2, noteDict1)
        elif noteType == 3:
            mapTemplate['note'].insert(-2, noteDict2)
        elif noteType == 4:
            placeTapOrFlick(noteDict1, noteTime, noteP, lastNote, columnOffset=FLICK_4K_COLUMN_OFFSET)

        lastNote = note

    return mapTemplate

def putInTrack(entireNoteList, mapTemplate, offset):
    if MODE == 2:
        return putInTrack4K(entireNoteList, mapTemplate, offset)
    return putInTrack2K(entireNoteList, mapTemplate, offset)

def _normalize_malody_beat(section, bb, cc):
    """进位到拍，并约分 [section, bb, cc]。"""
    if cc <= 0:
        return (section, 0, 1)
    if bb != 0:
        section += bb // cc
        bb = bb % cc
    if bb == 0:
        return (section, 0, 1)
    g = math.gcd(int(bb), int(cc))
    return (section, bb // g, cc // g)


def _phi_tick_split(phiTime):
    """phiTime → (拍序号, 拍内tick)。"""
    tick = int(round(float(phiTime)))
    if tick < 0:
        tick = 0
    section = tick // PHI_TICKS_PER_BEAT
    rem = tick % PHI_TICKS_PER_BEAT
    return section, rem


def mapTimeExchangeExact(phiTime):
    """精确模式：rem/32 约分，可表示128分，整数tick误差为0。"""
    section, rem = _phi_tick_split(phiTime)
    return _normalize_malody_beat(section, rem, PHI_TICKS_PER_BEAT)


def mapTimeExchangeSnap(phiTime):
    """
    吸附模式：靠近24分格（一拍6等分，含12分）则吸到 /6（约分后可能为 /3）；
    否则走精确约分（含128分）。
    Phi 常见近似 tick：5,11,21,27 ↔ 理想 5.33,10.67,21.33,26.67。
    """
    section, rem = _phi_tick_split(phiTime)
    if rem == 0:
        return (section, 0, 1)

    bb6 = int(round(rem * 6 / PHI_TICKS_PER_BEAT))
    ideal = bb6 * PHI_TICKS_PER_BEAT / 6.0
    if abs(rem - ideal) <= SNAP_24_THRESHOLD:
        return _normalize_malody_beat(section, bb6, 6)

    return _normalize_malody_beat(section, rem, PHI_TICKS_PER_BEAT)


def mapTimeExchange(phiTime):  # 从Phi时间转换到Malody时间
    if TIME_MODE == 2:
        return mapTimeExchangeSnap(phiTime)
    return mapTimeExchangeExact(phiTime)


def _template_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.json")


def convert_chart(
    phi_json_path: str,
    out_mc_path: str,
    *,
    title: str,
    artist: str = "",
    ogg_name: str,
    background: str,
    version: str,
    mode: int = 2,
    time_mode: int = 2,
    tempo_mode: int = 1,
    quiet: bool = True,
) -> None:
    """把官方 Phigros JSON 转成 Malody .mc，写入 out_mc_path。"""
    global MODE, TIME_MODE, TEMPO_MODE
    MODE = int(mode)
    TIME_MODE = int(time_mode)
    TEMPO_MODE = int(tempo_mode)

    with open(phi_json_path, "r", encoding="utf-8", newline="") as handle:
        note_json = json.load(handle)

    sink = io.StringIO() if quiet else None
    ctx = contextlib.redirect_stdout(sink) if quiet else contextlib.nullcontext()
    with ctx:
        entire_note_list, bpm_anchors, _chart_offset = extractnote(note_json, TEMPO_MODE)
        with open(_template_path(), "r", encoding="utf-8", newline="") as handle:
            map_template = json.load(handle)
        output = putInTrack(entire_note_list, map_template, 0)

    output["meta"]["song"]["title"] = title
    output["meta"]["song"]["artist"] = artist or "none"
    output["meta"]["version"] = version
    output["meta"]["background"] = background
    output["note"][-1]["sound"] = ogg_name
    output["time"] = extractBpm(bpm_anchors, 0)

    parent = os.path.dirname(out_mc_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_mc_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
