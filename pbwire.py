"""Minimal protobuf wire-format reader/writer (no schema). Used to read and patch VRoid .vroid data.bin."""


def read_varint(b, i):
    r = s = 0
    while True:
        c = b[i]; i += 1
        r |= (c & 0x7F) << s; s += 7
        if c < 0x80:
            return r, i


def enc_varint(v):
    out = bytearray()
    while True:
        c = v & 0x7F; v >>= 7
        if v:
            out.append(c | 0x80)
        else:
            out.append(c); return bytes(out)


def fields(b, start=0, end=None):
    """Yield (field_no, wire_type, value, (field_start, value_start, value_end)) for one message level."""
    i = start; end = len(b) if end is None else end
    while i < end:
        fs = i
        key, i = read_varint(b, i)
        fn, wt = key >> 3, key & 7
        if wt == 0:
            v, i2 = read_varint(b, i); yield fn, wt, v, (fs, i, i2); i = i2
        elif wt == 1:
            yield fn, wt, b[i:i + 8], (fs, i, i + 8); i += 8
        elif wt == 2:
            ln, j = read_varint(b, i); yield fn, wt, None, (fs, j, j + ln); i = j + ln
        elif wt == 5:
            yield fn, wt, b[i:i + 4], (fs, i, i + 4); i += 4
        else:
            raise ValueError(f"bad wire type {wt} at {fs}")
    if i != end:
        raise ValueError("overran message end")


def is_message(b, s, e):
    if e - s < 2:
        return False
    try:
        n = 0
        for fn, wt, _, _ in fields(b, s, e):
            if fn == 0 or fn > 10000:
                return False
            n += 1
        return n > 0
    except (ValueError, IndexError):
        return False
