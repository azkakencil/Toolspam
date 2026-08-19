#!/usr/bin/env python3

import base64
import bz2
import hashlib
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
            "Invalid protected run.py"
        )

    version = data[len(MAGIC)]

    if version != VERSION:
        raise RuntimeError(
            "Unsupported version"
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
            "Corrupted run.py"
        )

    payload = xor_data(
        payload,
        KEY
    )

    payload = lzma.decompress(
        payload
    )

    payload = bz2.decompress(
        payload
    )

    payload = zlib.decompress(
        payload
    )

    return marshal.loads(
        payload
    )


def run():

    # --------------------------------------------------------
    # Aktifkan custom encrypted-import system
    # --------------------------------------------------------

    runtime = ROOT / "_zyvora_runtime.py"

    if not runtime.exists():
        raise FileNotFoundError(
            "_zyvora_runtime.py tidak ditemukan"
        )

    runtime_code = compile(
        runtime.read_text(
            encoding="utf-8"
        ),
        str(runtime),
        "exec"
    )

    exec(
        runtime_code,
        {
            "__name__": "_zyvora_runtime",
            "__file__": str(runtime),
            "__package__": None,
        }
    )

    # --------------------------------------------------------
    # Load protected run.py
    # --------------------------------------------------------

    target = ROOT / "run.py.zyv"

    if not target.exists():
        raise FileNotFoundError(
            "run.py.zyv tidak ditemukan"
        )

    code = decrypt(
        target.read_bytes()
    )

    namespace = {
        "__name__": "__main__",
        "__file__": str(target),
        "__package__": None,
        "__cached__": None,
    }

    sys.path.insert(
        0,
        str(ROOT)
    )

    exec(
        code,
        namespace,
        namespace
    )


if __name__ == "__main__":
    run()