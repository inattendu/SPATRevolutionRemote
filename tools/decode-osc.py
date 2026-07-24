#!/usr/bin/env python3
"""Decode les messages OSC d'un pcap loopback (SPAT Remote <-> Open Stage Control).

Usage: python3 decode_osc.py osc.pcap [--filter /source]
Sortie: sens | adresse | typetag | args
"""
import struct
import sys


def read_pcap(path):
    """Yield (src_port, dst_port, udp_payload) depuis un pcap classique."""
    with open(path, "rb") as f:
        gh = f.read(24)
        if len(gh) < 24:
            return
        magic = gh[:4]
        if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
            endian = "<"
        elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
            endian = ">"
        else:
            raise SystemExit(f"format pcap inconnu: {magic!r} (pcapng ? relancer tcpdump)")
        linktype = struct.unpack(endian + "I", gh[20:24])[0]
        while True:
            ph = f.read(16)
            if len(ph) < 16:
                return
            _, _, caplen, _ = struct.unpack(endian + "IIII", ph)
            data = f.read(caplen)
            if len(data) < caplen:
                return
            # NULL/loopback (0) -> 4 octets de famille ; EN10MB (1) -> 14 octets
            off = 4 if linktype == 0 else 14
            if linktype == 1 and data[12:14] != b"\x08\x00":
                continue
            ip = data[off:]
            if len(ip) < 20 or (ip[0] >> 4) != 4:
                continue
            ihl = (ip[0] & 0x0F) * 4
            if ip[9] != 17:  # UDP
                continue
            udp = ip[ihl:]
            if len(udp) < 8:
                continue
            sport, dport, ulen = struct.unpack(">HHH", udp[:6])
            yield sport, dport, udp[8:ulen]


def _pad(n):
    return (n + 3) // 4 * 4


def _read_str(buf, i):
    end = buf.index(b"\x00", i)
    return buf[i:end].decode("utf-8", "replace"), i + _pad(end - i + 1)


def parse_msg(buf):
    """-> (address, typetag, [args]) ; leve ValueError si malforme."""
    addr, i = _read_str(buf, 0)
    if i >= len(buf):
        return addr, "", []
    tt, i = _read_str(buf, i)
    if not tt.startswith(","):
        return addr, "", ["<pas de typetag>"]
    args = []
    for t in tt[1:]:
        if t == "i":
            args.append(struct.unpack(">i", buf[i:i + 4])[0]); i += 4
        elif t == "f":
            args.append(round(struct.unpack(">f", buf[i:i + 4])[0], 4)); i += 4
        elif t == "d":
            args.append(round(struct.unpack(">d", buf[i:i + 8])[0], 4)); i += 8
        elif t == "s":
            s, i = _read_str(buf, i); args.append(repr(s))
        elif t == "b":
            n = struct.unpack(">i", buf[i:i + 4])[0]; i += 4 + _pad(n)
            args.append(f"<blob {n}o>")
        elif t in "TF":
            args.append(t == "T")
        elif t in "N":
            args.append(None)
        else:
            args.append(f"<type {t}?>")
    return addr, tt, args


def parse_packet(buf, out):
    """Gere les bundles (#bundle) recursivement."""
    if buf.startswith(b"#bundle"):
        i = 16  # "#bundle\0" + timetag
        while i + 4 <= len(buf):
            size = struct.unpack(">i", buf[i:i + 4])[0]
            i += 4
            if size <= 0 or i + size > len(buf):
                break
            parse_packet(buf[i:i + size], out)
            i += size
    else:
        try:
            out.append(parse_msg(buf))
        except Exception as e:
            out.append(("<illisible>", "", [str(e)]))


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    path = sys.argv[1]
    filt = None
    if "--filter" in sys.argv:
        filt = sys.argv[sys.argv.index("--filter") + 1]

    n = 0
    for sport, dport, payload in read_pcap(path):
        if not payload:
            continue
        msgs = []
        parse_packet(payload, msgs)
        # 9400 = entree SPAT (Remote -> SPAT) ; 9401 = sortie SPAT (SPAT -> Remote)
        sens = "Remote->SPAT" if dport == 9400 else ("SPAT->Remote" if dport == 9401 else f"{sport}->{dport}")
        for addr, tt, args in msgs:
            if filt and filt not in addr:
                continue
            n += 1
            print(f"{sens:13} | {addr:34} | {tt:8} | {args}")
    print(f"\n--- {n} message(s) OSC ---", file=sys.stderr)


if __name__ == "__main__":
    main()
