import base64
from concurrent.futures import ThreadPoolExecutor
import gc
from io import BytesIO
import json
import os
from queue import Queue
import shutil
import sys
import threading
import time
from UnityPy import Environment
from UnityPy.classes import AudioClip
from UnityPy.enums import ClassIDType
from zipfile import ZipFile
from log import init_console_logger
import logging



class ByteReader:
    def __init__(self, data):
        self.data = data
        self.position = 0

    def readInt(self):
        self.position += 4
        return self.data[self.position - 4] ^ self.data[self.position - 3] << 8 ^ self.data[self.position - 2] << 16


queue_out = Queue()
queue_in = Queue()
OUTPUT_ROOT = ""
config = None
FSB5 = None


def _out(rel):
    return os.path.join(OUTPUT_ROOT, rel) if OUTPUT_ROOT else rel


def io():
    while True:
        item = queue_in.get()
        if item is None:
            break
        else:
            path, resource = item
            if type(resource) == BytesIO:
                with resource:
                    with open(path, "wb") as f:
                        f.write(resource.getbuffer())
            else:
                with open(path, "wb") as f:
                    f.write(resource)


def save_image(path, image):
    bytesIO = BytesIO()
    image.save(bytesIO, "png")
    queue_in.put((path, bytesIO))


def save_music(path, music: AudioClip):
    fsb = FSB5(music.m_AudioData)
    rebuilt_sample = fsb.rebuild_sample(fsb.samples[0])
    queue_in.put((path, rebuilt_sample))


def read_bundle(apk, entry):
    names = [
        "assets/aa/Android/%s" % entry,
    ]
    if "_" in str(entry):
        names.append("assets/aa/Android/%s" % str(entry).split("_", 1)[1])
    if not str(entry).endswith(".bundle"):
        names.append("assets/aa/Android/%s.bundle" % entry)
    last_error = None
    for name in names:
        try:
            return apk.read(name)
        except KeyError as error:
            last_error = error
    raise last_error


classes = ClassIDType.TextAsset, ClassIDType.Sprite, ClassIDType.AudioClip


def save(key, entry, pool, logger):
    obj = entry.get_filtered_objects(classes)
    obj = next(obj).read()
    if config["avatar"] and key[:7] == "avatar.":
        key = key[7:]
        bytesIO = BytesIO()
        obj.image.save(bytesIO, "png")
        queue_in.put((_out("avatar/%s.png" % key), bytesIO))
    elif config["chart"] and key[-14:-7] == "/Chart_" and key[-5:] == ".json":
        logger.info(key)
        p = _out("chart/" + key[:-14])
        if not os.path.exists(p):
            os.makedirs(p, exist_ok=True)
        queue_in.put((_out("chart/%s/%s.json" % (key[:-14], key[-7:-5])), obj.script))
    elif config["illustrationBlur"] and key[-23:-3] == ".0/IllustrationBlur.":
        key = key[:-23]
        bytesIO = BytesIO()
        obj.image.save(bytesIO, "png")
        queue_in.put((_out("illustrationBlur/%s.png" % key), bytesIO))
    elif config["illustrationLowRes"] and key[-25:-3] == ".0/IllustrationLowRes.":
        key = key[:-25]
        pool.submit(save_image, _out("illustrationLowRes/%s.png" % key), obj.image)
    elif config["illustration"] and key[-19:-3] == ".0/Illustration.":
        key = key[:-19]
        pool.submit(save_image, _out("illustration/%s.png" % key), obj.image)
    elif config["music"] and key[-12:] == ".0/music.wav":
        key = key[:-12]
        pool.submit(save_music, _out("music/%s.ogg" % key), obj)
        # save_music(f"music/{key}.wav", obj)


def run(path, logger, work_dir="_work", type_config=None):
    global config, OUTPUT_ROOT, FSB5
    if type_config is None:
        from settings import resource_types
        type_config = resource_types()
    config = type_config
    OUTPUT_ROOT = work_dir or ""
    if config.get("music"):
        from fsb5 import FSB5 as _FSB5
        FSB5 = _FSB5
    type_list = ("avatar", "chart", "illustrationBlur", "illustrationLowRes", "illustration", "music")
    for directory in type_list:
        if not config.get(directory):
            continue
        target = _out(directory)
        os.makedirs(target, exist_ok=True)
    _run_extract(path, logger)


def _run_extract(path, logger):
    with ZipFile(path) as apk:
        with apk.open("assets/aa/catalog.json") as f:
            data = json.load(f)

    key = base64.b64decode(data["m_KeyDataString"])
    bucket = base64.b64decode(data["m_BucketDataString"])
    entry = base64.b64decode(data["m_EntryDataString"])

    table = []
    reader = ByteReader(bucket)
    for x in range(reader.readInt()):
        key_position = reader.readInt()
        key_type = key[key_position]
        key_position += 1
        if key_type == 0:
            length = key[key_position]
            key_position += 4
            key_value = key[key_position:key_position + length].decode()
        elif key_type == 1:
            length = key[key_position]
            key_position += 4
            key_value = key[key_position:key_position + length].decode("utf16")
        elif key_type == 4:
            key_value = key[key_position]
        else:
            raise BaseException(key_position, key_type)
        entry_value = None
        for i in range(reader.readInt()):
            entry_position = reader.readInt()
            entry_value = entry[4 + 28 * entry_position:4 + 28 * entry_position + 28]
            entry_value = entry_value[8] ^ entry_value[9] << 8
        table.append([key_value, entry_value])
    for i in range(len(table)):
        if table[i][1] != 65535:
            table[i][1] = table[table[i][1]][0]
    for i in range(len(table) - 1, -1, -1):
        if type(table[i][0]) == int or table[i][0][:15] == "Assets/Tracks/#" or table[i][0][:14] != "Assets/Tracks/" and \
                table[i][0][:7] != "avatar.":
            del table[i]
        elif table[i][0][:14] == "Assets/Tracks/":
            table[i][0] = table[i][0][14:]
    for key, value in table:
        logger.info('{key}, {value}'.format(key=key, value=value))

    if config["avatar"]:
        avatar = {}
        with open("info/tmp.tsv",encoding="utf8") as f:
            line = f.readline()[:-1]
            while line:
                l = line.split("\t")
                avatar[l[1]] = l[0]
                line = f.readline()[:-1]

    thread = threading.Thread(target=io)
    thread.start()
    ti = time.time()
    update = config["UPDATE"]
    with ThreadPoolExecutor(6) as pool:
        if update["main_story"] == 0 and update["other_song"] == 0 and update["side_story"] == 0:
            with ZipFile(path) as apk:
                for key, entry in table:
                    env = Environment()
                    env.load_file(BytesIO(read_bundle(apk, entry)), name=key)
                    for i_key, i_entry in env.files.items():
                        save(i_key, i_entry, pool, logger)
        else:
            l = []
            with open("info/difficulty.tsv", encoding="utf8") as f:
                line = f.readline()
                while line:
                    l.append(line.split("\t", 2)[0])
                    line = f.readline()
            index1 = l.index("Doppelganger.LeaF")
            index2 = l.index("Poseidon.1112vsStar")
            del l[index2:len(l) - update["side_story"]]
            del l[index1:index2 - update["other_song"]]
            del l[:index1 - update["main_story"]]
            logger.info(str(l))
            env = Environment()
            with ZipFile(path) as apk:
                for key, entry in table:
                    if key[:7] == "avatar.":
                        env.load_file(BytesIO(read_bundle(apk, entry)), name=key)
                        continue
                    for song_id in l:
                        if key.startswith("%s.0/" % song_id):
                            env.load_file(BytesIO(read_bundle(apk, entry)), name=key)
                            break
            for i_key, i_entry in env.files.items():
                save(i_key, i_entry, pool, logger)
    queue_in.put(None)
    thread.join()
    logger.info("%f秒" % round(time.time() - ti, 4))


if __name__ == "__main__":
    if len(sys.argv) == 1 and os.path.isdir("/data/"):
        import subprocess
        r = subprocess.run("pm path com.PigeonGames.Phigros",stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,shell=True)
        file_path = r.stdout[8:-1].decode()
    else:
        file_path = sys.argv[1]
    from settings import pipeline_paths, resource_types
    paths = pipeline_paths()
    run(file_path, init_console_logger(), work_dir=str(paths["work_dir"]), type_config=resource_types())
