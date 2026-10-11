import os
import re
import struct
import sys
from io import BytesIO
from zipfile import ZipFile
from UnityPy import Environment
from log import init_console_logger


class ByteReader:
    def __init__(self, data: bytes):
        self.data = data
        self.position = 0

    def readInt(self):
        self.position += 4
        return int.from_bytes(self.data[self.position - 4 : self.position], "little", signed=True)

    def peekInt(self, offset=0):
        pos = self.position + offset
        return int.from_bytes(self.data[pos : pos + 4], "little", signed=True)

    def readFloat(self):
        self.position += 4
        return struct.unpack("<f", self.data[self.position - 4 : self.position])[0]

    def align(self):
        self.position += (4 - (self.position % 4)) % 4

    def readString(self):
        length = self.readInt()
        if length < 0 or self.position + length > len(self.data):
            raise ValueError(f"bad string length {length} at {self.position - 4}")
        result = self.data[self.position : self.position + length].decode("utf8")
        self.position += length
        self.align()
        return result

    def looks_like_song_id(self, pos):
        if pos + 4 > len(self.data):
            return False
        length = int.from_bytes(self.data[pos : pos + 4], "little", signed=True)
        if not (6 <= length <= 200) or pos + 4 + length > len(self.data):
            return False
        try:
            text = self.data[pos + 4 : pos + 4 + length].decode("utf8")
        except Exception:
            return False
        return "." in text and text.endswith(".0")

    def skip_tail(self):
        while self.position + 4 <= len(self.data):
            if self.looks_like_song_id(self.position):
                return
            count = self.peekInt()
            if 1 <= count <= 500 and self.looks_like_song_id(self.position + 4):
                return
            self.position += 4

    def readSong(self):
        item = {
            "songsId": self.readString(),
            "songsKey": self.readString(),
            "songsName": self.readString(),
            "songsTitle": self.readString(),
            "difficulty": [self.readFloat() for _ in range(self.readInt())],
            "illustrator": self.readString(),
            "charter": [self.readString() for _ in range(self.readInt())],
            "composer": self.readString(),
            "levels": [self.readString() for _ in range(self.readInt())],
        }
        self.readFloat()
        self.readFloat()
        for _ in range(self.readInt()):
            self.readInt()
            for _ in range(self.readInt()):
                self.readString()
        for _ in range(self.readInt()):
            for _ in range(self.readInt()):
                self.readInt()
                self.position += 12
        for _ in range(self.readInt()):
            for _ in range(self.readInt()):
                self.readString()
        self.skip_tail()
        return item


def load_unity_env(path):
    env = Environment()
    with ZipFile(path) as apk:
        names = apk.namelist()
        if "assets/bin/Data/data.unity3d" in names:
            env.load_file(BytesIO(apk.read("assets/bin/Data/data.unity3d")), name="assets/bin/Data/data.unity3d")
        else:
            env.load_file(
                BytesIO(apk.read("assets/bin/Data/globalgamemanagers.assets")),
                name="assets/bin/Data/globalgamemanagers.assets",
            )
            env.load_file(BytesIO(apk.read("assets/bin/Data/level0")))
    return env


def read_monobehaviour_blobs(env):
    blobs = {}
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        data = obj.read()
        try:
            name = data.m_Script.get_obj().read().name
        except Exception:
            continue
        if name in ("GameInformation", "GetCollectionControl", "TipsProvider"):
            blobs[name] = bytes(data.raw_data)
    return blobs


GROUP_META = {
    "mainSongs": ("main", "main", "主线"),
    "extraSongs": ("extra", "extra", "单曲与合集"),
    "sideStorySongs": ("side", "side", "支线"),
}


def parse_songs(information: bytes):
    marker = b"Glaciaxion.SunsetRay.0"
    idx = information.find(marker, 20000)
    if idx < 0:
        idx = information.find(marker)
    if idx < 0:
        raise RuntimeError("未在 GameInformation 中找到曲目数据")
    reader = ByteReader(information)
    reader.position = idx - 8
    songs = []
    for group_key in ("mainSongs", "extraSongs", "sideStorySongs"):
        group, code, title = GROUP_META[group_key]
        count = reader.readInt()
        for _ in range(count):
            song = reader.readSong()
            song["_group"] = group
            song["_chapterCode"] = code
            song["_chapterTitle"] = title
            songs.append(song)
    return songs


def parse_avatars(collection: bytes):
    avatars = []
    for match in re.finditer(rb"avatar\.[A-Za-z0-9_\.\-]+", collection):
        key = match.group().decode("ascii")
        avatars.append(key[7:])
    # 去重且保持顺序
    seen = set()
    unique = []
    for item in avatars:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def parse_tips(tips: bytes):
    reader = ByteReader(tips)
    # 跳过 MonoBehaviour 头部，尽量定位到字符串列表
    found = []
    pos = 0
    while pos + 4 <= len(tips):
        length = int.from_bytes(tips[pos : pos + 4], "little", signed=True)
        if 4 <= length <= 200 and pos + 4 + length <= len(tips):
            raw = tips[pos + 4 : pos + 4 + length]
            try:
                text = raw.decode("utf8")
            except Exception:
                pos += 4
                continue
            if "\x00" not in text and any(ch.isalpha() or "\u4e00" <= ch <= "\u9fff" for ch in text):
                found.append(text)
                pos += 4 + length
                pos += (4 - (pos % 4)) % 4
                continue
        pos += 4
    return found


def run(path, logger):
    env = load_unity_env(path)
    blobs = read_monobehaviour_blobs(env)
    songs = parse_songs(blobs["GameInformation"])

    difficulty = []
    table = []
    for song in songs:
        diffs = list(song["difficulty"])
        charters = list(song["charter"])
        if len(diffs) == 5:
            diffs.pop()
            if charters:
                charters.pop()
        if diffs and diffs[-1] == 0.0:
            diffs.pop()
            if charters:
                charters.pop()
        diffs = [str(round(value, 1)) for value in diffs]
        song_id = song["songsId"][:-2] if song["songsId"].endswith(".0") else song["songsId"]
        difficulty.append([song_id] + diffs)
        table.append((song_id, song["songsName"], song["composer"], song["illustrator"], *charters))

    logger.info("解析曲目 %d 首", len(table))

    os.makedirs("info", exist_ok=True)
    with open("info/difficulty.tsv", "w", encoding="utf8") as f:
        for item in difficulty:
            f.write("\t".join(map(str, item)))
            f.write("\n")

    with open("info/info.tsv", "w", encoding="utf8") as f:
        for item in table:
            f.write("\t".join(item))
            f.write("\n")

    with open("info/chapters.tsv", "w", encoding="utf8") as f:
        for song in songs:
            song_id = song["songsId"][:-2] if song["songsId"].endswith(".0") else song["songsId"]
            f.write("\t".join((
                song_id,
                song.get("_group", ""),
                song.get("_chapterCode", ""),
                song.get("_chapterTitle", ""),
            )))
            f.write("\n")
    logger.info("写入章节建议 %d 条", len(songs))

    avatars = parse_avatars(blobs.get("GetCollectionControl", b""))
    with open("info/avatar.txt", "w", encoding="utf8") as avatar:
        with open("info/tmp.tsv", "w", encoding="utf8") as tmp:
            for item in avatars:
                avatar.write(item)
                avatar.write("\n")
                tmp.write("%s\t%s\n" % (item, item))
    logger.info("解析头像 %d 个", len(avatars))

    tips = parse_tips(blobs.get("TipsProvider", b""))
    with open("info/tips.txt", "w", encoding="utf8") as f:
        for tip in tips:
            f.write(tip)
            f.write("\n")
    logger.info("解析 tips %d 条", len(tips))

    # 兼容旧脚本可能读取的空文件
    for name in ("single.txt", "illustration.txt", "collection.tsv"):
        path_name = os.path.join("info", name)
        if not os.path.exists(path_name):
            open(path_name, "w", encoding="utf8").close()


if __name__ == "__main__":
    if len(sys.argv) == 1 and os.path.isdir("/data/"):
        import subprocess

        result = subprocess.run(
            "pm path com.PigeonGames.Phigros",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=True,
        )
        file_path = result.stdout[8:-1].decode()
    else:
        file_path = sys.argv[1]
    if not os.path.isdir("info"):
        os.mkdir("info")
    run(file_path, init_console_logger())
