"""只读分析 MOV 的盒子结构,用来和 iPhone 真品对照。  python inspect_mov.py 文件.MOV [--hex]"""
import struct
import sys

CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"dinf", b"edts", b"udta", b"meta", b"ilst", b"keys"}


def boxes(buf, start, end):
    i = start
    while i + 8 <= end:
        size, typ = struct.unpack(">I4s", buf[i:i + 8])
        hdr = 8
        if size == 1:
            size, hdr = struct.unpack(">Q", buf[i + 8:i + 16])[0], 16
        elif size == 0:
            size = end - i
        if size < hdr or i + size > end:
            return
        yield typ, i, i + size, hdr
        i += size


def show(buf, start, end, depth, out):
    for typ, s, e, h in boxes(buf, start, end):
        body = buf[s + h:e]
        extra = ""
        if typ == b"hdlr":
            extra = f"  handler={body[8:12]!r} name={body[24:].rstrip(b'\x00')[:40]!r}"
        elif typ in (b"mdhd", b"mvhd"):
            v = body[0]
            ts = struct.unpack(">I", body[12:16] if v == 0 else body[20:24])[0]
            extra = f"  v{v} timescale={ts}"
        elif typ == b"tkhd":
            extra = f"  flags={int.from_bytes(body[1:4], 'big')} id={struct.unpack('>I', body[12:16])[0]}"
        elif typ == b"stsd":
            extra = f"  entries={struct.unpack('>I', body[4:8])[0]}"
        elif typ in (b"stts", b"stsz", b"stco", b"stsc"):
            extra = "  " + " ".join(str(x) for x in struct.unpack(">" + "I" * min(6, (len(body) - 4) // 4), body[4:4 + 4 * min(6, (len(body) - 4) // 4)]))
        out.append(f"{'  ' * depth}{typ.decode('latin1')}  size={e - s}{extra}")
        if typ == b"meta":
            inner = body[4:] if body[:4] == b"\x00\x00\x00\x00" else body
            off = s + h + (4 if body[:4] == b"\x00\x00\x00\x00" else 0)
            show(buf, off, e, depth + 1, out)
        elif typ == b"stsd":
            show(buf, s + h + 8, e, depth + 1, out)
        elif typ == b"mebx":
            show(buf, s + h + 8, e, depth + 1, out)   # sample entry: 6 保留 + 2 引用
        elif typ in CONTAINERS and typ != b"meta":
            show(buf, s + h, e, depth + 1, out)
        elif typ in (b"keyd", b"dtyp", b"data", b"mdta"):
            out.append(f"{'  ' * (depth + 1)}内容: {body[:80]!r}")


if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read()
    lines = []
    show(data, 0, len(data), 0, lines)
    print("\n".join(lines))
