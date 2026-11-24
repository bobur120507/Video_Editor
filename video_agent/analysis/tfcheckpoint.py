"""Reads TensorFlow checkpoint weights (a SavedModel's variables.index + variables.data-*) into numpy, without
TensorFlow: the index is a LevelDB table of tensor name -> BundleEntryProto, the data file raw little-endian
tensors. Used for the TransNetV2 weights, so no TensorFlow install is needed.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

DTYPES = {1: np.float32, 2: np.float64, 3: np.int32, 9: np.int64, 10: np.bool_}


def _varint(b: bytes, i: int) -> tuple[int, int]:
    out = shift = 0
    while True:
        c = b[i]
        i += 1
        out |= (c & 0x7F) << shift
        if c < 0x80:
            return out, i
        shift += 7


def _fields(b: bytes):
    """(field number, wire type, value) of a protobuf message; value is int or bytes."""
    i = 0
    while i < len(b):
        key, i = _varint(b, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        elif wt == 5:
            v, i = b[i:i + 4], i + 4
        elif wt == 1:
            v, i = b[i:i + 8], i + 8
        else:
            raise ValueError(f"unsupported protobuf wire type {wt}")
        yield num, wt, v


def _block(data: bytes, offset: int, size: int) -> list[tuple[bytes, bytes]]:
    blk = data[offset:offset + size]
    if data[offset + size] != 0:
        raise ValueError("compressed checkpoint blocks are not supported")
    n_restarts = struct.unpack("<I", blk[-4:])[0]
    end = len(blk) - 4 - 4 * n_restarts
    out, key, i = [], b"", 0
    while i < end:
        shared, i = _varint(blk, i)
        non_shared, i = _varint(blk, i)
        vlen, i = _varint(blk, i)
        key = key[:shared] + blk[i:i + non_shared]
        i += non_shared
        out.append((key, blk[i:i + vlen]))
        i += vlen
    return out


def read(variables_prefix: str | Path) -> dict[str, np.ndarray]:
    """{checkpoint key: array} for every tensor in `<prefix>.index` / `<prefix>.data-00000-of-00001`."""
    prefix = Path(variables_prefix)
    index = prefix.with_name(prefix.name + ".index").read_bytes()
    data = prefix.with_name(prefix.name + ".data-00000-of-00001").read_bytes()
    footer = index[-48:]
    if struct.unpack("<Q", footer[-8:])[0] != 0xDB4775248B80FB57:
        raise ValueError("not a TensorFlow checkpoint index")
    _, i = _varint(footer, 0)
    _, i = _varint(footer, i)  # metaindex handle (unused)
    idx_off, i = _varint(footer, i)
    idx_size, i = _varint(footer, i)
    tensors = {}
    for _, handle in _block(index, idx_off, idx_size):
        off, j = _varint(handle, 0)
        size, _ = _varint(handle, j)
        for key, value in _block(index, off, size):
            if not key:
                continue  # the bundle header
            entry = {num: v for num, _, v in _fields(value)}
            dtype = DTYPES.get(entry.get(1, 1))
            if dtype is None:
                continue  # strings (the object graph) and other non-numeric tensors
            dims = [dict((n, v) for n, _, v in _fields(d)).get(1, 0)
                    for num, _, d in _fields(entry.get(2, b"")) if num == 2]
            start, nbytes = entry.get(4, 0), entry.get(5, 0)
            arr = np.frombuffer(data[start:start + nbytes], dtype=dtype)
            tensors[key.decode()] = arr.reshape(dims) if dims else arr.reshape(())
    return tensors
