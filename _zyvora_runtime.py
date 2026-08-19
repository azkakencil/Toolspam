import base64
import bz2
import hashlib
import importlib.abc
import importlib.util
import lzma
import marshal
import struct
import sys
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent

MAGIC = b"ZYVORA5"
VERSION = 1

KEY_SEED = b"ZYVORA-5-LAYER-PROTECTOR-2026"

KEY = hashlib.sha256(KEY_SEED).digest()


def xor_data(data, key):
    return bytes(
        value ^ key[i % len(key)]
        for i, value in enumerate(data)
    )


def decrypt(blob):

    data = base64.b85decode(blob)

    if not data.startswith(MAGIC):
        raise RuntimeError(
            "Invalid Zyvora protected module"
        )

    version = data[len(MAGIC)]

    if version != VERSION:
        raise RuntimeError(
            "Unsupported Zyvora version"
        )

    size = struct.unpack(
        ">I",
        data[
            len(MAGIC) + 1:
            len(MAGIC) + 5
        ]
    )[0]

    payload = data[
        len(MAGIC) + 5:
    ]

    if len(payload) != size:
        raise RuntimeError(
            "Corrupted Zyvora module"
        )

    # Layer 5
    payload = xor_data(
        payload,
        KEY
    )

    # Layer 4
    payload = lzma.decompress(
        payload
    )

    # Layer 3
    payload = bz2.decompress(
        payload
    )

    # Layer 2
    payload = zlib.decompress(
        payload
    )

    # Layer 1
    return marshal.loads(
        payload
    )


class ZyvoraLoader(
    importlib.abc.Loader
):

    def __init__(self, fullname, filename):
        self.fullname = fullname
        self.filename = filename

    def create_module(self, spec):
        return None

    def exec_module(self, module):

        blob = self.filename.read_bytes()

        code = decrypt(blob)

        module.__file__ = str(
            self.filename
        )

        module.__loader__ = self

        module.__package__ = (
            self.fullname.rpartition(".")[0]
        )

        exec(
            code,
            module.__dict__,
            module.__dict__
        )


class ZyvoraFinder(
    importlib.abc.MetaPathFinder
):

    def find_spec(
        self,
        fullname,
        path=None,
        target=None
    ):

        # Hanya module sederhana seperti:
        #
        # import handlers
        # import core
        # import cam
        #
        if "." in fullname:
            return None

        filename = ROOT / (
            fullname + ".py.zyv"
        )

        if not filename.is_file():
            return None

        loader = ZyvoraLoader(
            fullname,
            filename
        )

        return importlib.util.spec_from_file_location(
            fullname,
            filename,
            loader=loader
        )


# Pasang finder SEBELUM run.py.zyv dijalankan
if not any(
    isinstance(
        finder,
        ZyvoraFinder
    )
    for finder in sys.meta_path
):
    sys.meta_path.insert(
        0,
        ZyvoraFinder()
    )