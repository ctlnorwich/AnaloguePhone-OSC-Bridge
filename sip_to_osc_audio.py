#!/usr/bin/env python3
"""SIP-to-OSC bridge that also plays a sound clip in the handset for each dialled code.

The code is sent to TouchDesigner as OSC, as in sip_to_osc.py. Then the call is answered,
sounds/<code>.wav (or sounds/default.wav) is played down the line, and the bridge hangs up.
Codes with no clip get --reply instead (a busy tone by default).
"""
import argparse
import random
import re
import socket
import struct
import threading
import time
from pathlib import Path
from urllib.parse import unquote

from sip_to_osc import local_tag, osc_message, parse, response, values

FRAME = 160  # bytes per RTP packet: 20 ms of 8 kHz G.711 μ-law
SILENCE = b"\xff"  # μ-law zero level


def ulaw(sample):
    """Encode one signed 16-bit sample as G.711 μ-law."""
    sign = 0x80 if sample < 0 else 0
    sample = min(abs(sample), 32635) + 0x84
    exponent = sample.bit_length() - 8
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | exponent << 4 | mantissa) & 0xFF


def load_clip(path):
    """Return the audio of an 8 kHz mono WAV file (μ-law or 16-bit PCM) as μ-law bytes."""
    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    fmt = audio = None
    pos = 12
    while pos + 8 <= len(data):
        chunk, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if chunk == b"fmt ":
            fmt = struct.unpack("<HHIIHH", data[pos + 8:pos + 24])
        elif chunk == b"data":
            audio = data[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
    if fmt is None or audio is None:
        raise ValueError("WAV file has no fmt/data chunk")
    tag, channels, rate, _, _, bits = fmt
    if channels == 1 and rate == 8000:
        if tag == 7 and bits == 8:
            return audio
        if tag == 1 and bits == 16:
            samples = struct.unpack(f"<{len(audio) // 2}h", audio[:len(audio) // 2 * 2])
            return bytes(ulaw(s) for s in samples)
    raise ValueError(f"need 8000 Hz mono μ-law or 16-bit WAV, got {rate} Hz, {channels} channel(s), "
                     f"format {tag}, {bits}-bit")


def find_clip(sounds, code):
    names = [code, "default"] if re.fullmatch(r"[\w*#+]+", code) else ["default"]
    for name in names:
        path = sounds / f"{name}.wav"
        if path.is_file():
            return path
    return None


def remote_media(body, fallback_ip):
    """Where the ATA wants to receive audio, from the SDP in its INVITE."""
    text = body.decode(errors="replace")
    port = re.search(r"^m=audio (\d+)", text, re.M)
    ips = re.findall(r"^c=IN IP4 (\S+)", text, re.M)
    return (ips[-1] if ips else fallback_ip, int(port.group(1))) if port else None


def sdp(ip, port):
    session = int(time.time())
    return (f"v=0\r\no=sip-to-osc {session} {session} IN IP4 {ip}\r\ns=sip-to-osc\r\n"
            f"c=IN IP4 {ip}\r\nt=0 0\r\nm=audio {port} RTP/AVP 0\r\n"
            "a=rtpmap:0 PCMU/8000\r\na=ptime:20\r\na=sendrecv\r\n").encode()


def bye_request(headers, ata, local_ip, sip_port):
    """The BYE we send to hang up a call we answered (we are the callee, so From/To are swapped)."""
    call_id = values(headers, "call-id")[0]
    contact = (values(headers, "contact") or [""])[0]
    m = re.search(r"<([^>]+)>", contact)
    target = m.group(1) if m else (contact.split(";")[0] or f"sip:{ata[0]}:{ata[1]}")
    ours = values(headers, "to")[0]
    if ";tag=" not in ours:
        ours += ";tag=" + local_tag(call_id)
    lines = [
        f"BYE {target} SIP/2.0",
        f"Via: SIP/2.0/UDP {local_ip}:{sip_port};branch=z9hG4bK{random.getrandbits(48):x};rport",
        "Max-Forwards: 70",
        f"From: {ours}",
        f"To: {values(headers, 'from')[0]}",
        f"Call-ID: {call_id}",
        "CSeq: 1 BYE",
        "User-Agent: sip-to-osc",
        "Content-Length: 0", "", "",
    ]
    return "\r\n".join(lines).encode()


class Call:
    def __init__(self, ok, bye, ata, media, rtp, audio):
        self.ok, self.bye, self.ata, self.media, self.rtp, self.audio = ok, bye, ata, media, rtp, audio
        self.acked = threading.Event()  # ATA confirmed our answer
        self.hung_up = threading.Event()  # caller put the handset down
        self.bye_ok = threading.Event()  # ATA confirmed our hang-up


def play(sip, calls, call_id, call):
    call.acked.wait(2)  # start anyway if the ACK went missing
    audio = SILENCE * 2000 + call.audio + SILENCE * 2400  # 0.25 s lead-in, 0.3 s tail
    audio += SILENCE * (-len(audio) % FRAME)
    seq, timestamp, ssrc = random.getrandbits(16), random.getrandbits(32), random.getrandbits(32)
    start = time.monotonic()
    for n, i in enumerate(range(0, len(audio), FRAME)):
        if call.hung_up.is_set():
            break
        header = struct.pack("!BBHII", 0x80, 0x80 if n == 0 else 0, (seq + n) & 0xFFFF,
                             (timestamp + i) & 0xFFFFFFFF, ssrc)
        call.rtp.sendto(header + audio[i:i + FRAME], call.media)
        time.sleep(max(0.0, start + (n + 1) * 0.02 - time.monotonic()))
    if not call.hung_up.is_set():
        for wait in (0.5, 1, 2, 4):  # resend the BYE until the ATA answers it
            sip.sendto(call.bye, call.ata)
            if call.bye_ok.wait(wait) or call.hung_up.is_set():
                break
    call.rtp.close()
    calls.pop(call_id, None)


def local_ip_for(addr):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(addr)
    ip = s.getsockname()[0]
    s.close()
    return ip


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--td-host", default="127.0.0.1", help="IP of the TouchDesigner machine")
    ap.add_argument("--td-port", type=int, default=7000, help="OSC In port in TouchDesigner")
    ap.add_argument("--osc-address", default="/phone/dial")
    ap.add_argument("--sip-port", type=int, default=5060)
    ap.add_argument("--sounds", type=Path, default=Path(__file__).resolve().parent / "sounds",
                    help="folder of <code>.wav clips, plus an optional default.wav")
    ap.add_argument("--reply", default="486 Busy Here", help="SIP reply for codes with no clip (486 = busy tone)")
    args = ap.parse_args()

    osc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    td = (args.td_host, args.td_port)
    sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sip.bind(("0.0.0.0", args.sip_port))
    clips = sorted(p.name for p in args.sounds.glob("*.wav")) if args.sounds.is_dir() else []
    print(f"listening for SIP on UDP {args.sip_port}, sending OSC to {td}", flush=True)
    print(f"clips in {args.sounds}: {', '.join(clips) or 'none'}", flush=True)
    seen, calls = {}, {}

    while True:
        data, addr = sip.recvfrom(65535)
        try:
            start, headers = parse(data)
            method, uri = start.split()[:2]
        except ValueError:
            continue
        call_id = (values(headers, "call-id") or [""])[0]
        call = calls.get(call_id)

        if method == "SIP/2.0":  # a response, e.g. the ATA confirming our BYE
            if call and uri.startswith("2") and (values(headers, "cseq") or [""])[0].endswith("BYE"):
                call.bye_ok.set()
            continue

        if method == "INVITE":
            if call:  # retransmission: the ATA didn't get our answer
                sip.sendto(call.ok, addr)
                continue
            now = time.time()
            seen = {k: t for k, t in seen.items() if now - t < 60}
            if call_id in seen:
                sip.sendto(response(args.reply, headers), addr)
                continue
            seen[call_id] = now
            m = re.match(r"sips?:([^@;>]+)@", uri)
            code = unquote(m.group(1)) if m else ""
            print(f"dialled: {code}", flush=True)
            osc.sendto(osc_message(args.osc_address, code), td)

            clip, audio = find_clip(args.sounds, code), None
            if clip:
                try:
                    audio = load_clip(clip)
                except (OSError, ValueError) as e:
                    print(f"can't play {clip}: {e}", flush=True)
            body = data.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in data else b""
            media = remote_media(body, addr[0])
            if audio is None or media is None:
                sip.sendto(response(args.reply, headers), addr)
                continue

            local_ip = local_ip_for(addr)
            rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            rtp.bind(("0.0.0.0", 0))
            extra = [f"Contact: <sip:{local_ip}:{args.sip_port}>", "Content-Type: application/sdp",
                     "Allow: INVITE, ACK, BYE, CANCEL, OPTIONS"]
            ok = response("200 OK", headers, extra, sdp(local_ip, rtp.getsockname()[1]))
            call = calls[call_id] = Call(ok, bye_request(headers, addr, local_ip, args.sip_port),
                                         addr, media, rtp, audio)
            sip.sendto(ok, addr)
            print(f"playing {clip.name} ({len(audio) / 8000:.1f} s)", flush=True)
            threading.Thread(target=play, args=(sip, calls, call_id, call), daemon=True).start()
        elif method == "ACK":
            if call:
                call.acked.set()
        elif method == "BYE":
            sip.sendto(response("200 OK", headers), addr)
            if call:
                call.hung_up.set()
        elif method == "REGISTER":
            extra = [f"Contact: {c}" for c in values(headers, "contact")] + ["Expires: 3600"]
            sip.sendto(response("200 OK", headers, extra), addr)
            print(f"ATA registered from {addr[0]}", flush=True)
        elif method in ("OPTIONS", "CANCEL"):
            sip.sendto(response("200 OK", headers), addr)
        else:
            sip.sendto(response("501 Not Implemented", headers), addr)


if __name__ == "__main__":
    main()
