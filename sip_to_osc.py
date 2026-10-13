#!/usr/bin/env python3
"""Tiny SIP-to-OSC bridge: the number dialled on the ATA is sent to TouchDesigner as OSC."""
import argparse
import hashlib
import re
import socket
import time
from urllib.parse import unquote

COMPACT = {"v": "via", "f": "from", "t": "to", "i": "call-id", "m": "contact", "l": "content-length"}


def osc_pad(b):
    return b + b"\0" * (4 - len(b) % 4)


def osc_message(address, text):
    return osc_pad(address.encode()) + osc_pad(b",s") + osc_pad(text.encode())


def parse(data):
    lines = data.decode(errors="replace").split("\r\n\r\n", 1)[0].split("\r\n")
    headers = []
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            name = name.strip().lower()
            headers.append((COMPACT.get(name, name), value.strip()))
    return lines[0], headers


def values(headers, name):
    return [v for n, v in headers if n == name]


def local_tag(call_id):
    return hashlib.md5(call_id.encode()).hexdigest()[:8]


def response(status, headers, extra=(), body=b""):
    call_id = (values(headers, "call-id") or [""])[0]
    to = (values(headers, "to") or [""])[0]
    if ";tag=" not in to:
        to += ";tag=" + local_tag(call_id)
    lines = [f"SIP/2.0 {status}"]
    lines += [f"Via: {v}" for v in values(headers, "via")]
    lines += [f"From: {v}" for v in values(headers, "from")]
    lines += [f"To: {to}", f"Call-ID: {call_id}"]
    lines += [f"CSeq: {v}" for v in values(headers, "cseq")]
    lines += list(extra) + ["Server: sip-to-osc", f"Content-Length: {len(body)}", "", ""]
    return "\r\n".join(lines).encode() + body


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--td-host", default="127.0.0.1", help="IP of the TouchDesigner machine")
    ap.add_argument("--td-port", type=int, default=7000, help="OSC In port in TouchDesigner")
    ap.add_argument("--osc-address", default="/phone/dial")
    ap.add_argument("--sip-port", type=int, default=5060)
    ap.add_argument("--reply", default="486 Busy Here", help="SIP reply to the call (486 = busy tone)")
    ap.add_argument("--test", metavar="CODE", help="send one OSC message and exit")
    args = ap.parse_args()

    osc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    td = (args.td_host, args.td_port)
    if args.test:
        osc.sendto(osc_message(args.osc_address, args.test), td)
        print(f"sent {args.osc_address} '{args.test}' to {td}")
        return

    sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sip.bind(("0.0.0.0", args.sip_port))
    print(f"listening for SIP on UDP {args.sip_port}, sending OSC to {td}", flush=True)
    seen = {}

    while True:
        data, addr = sip.recvfrom(65535)
        try:
            start, headers = parse(data)
            method, uri = start.split()[:2]
        except ValueError:
            continue
        if method == "SIP/2.0":  # a response, not a request
            continue

        if method == "INVITE":
            call_id = (values(headers, "call-id") or [""])[0]
            now = time.time()
            seen = {k: t for k, t in seen.items() if now - t < 60}
            if call_id not in seen:  # ignore retransmissions of the same call
                seen[call_id] = now
                m = re.match(r"sips?:([^@;>]+)@", uri)
                code = unquote(m.group(1)) if m else ""
                print(f"dialled: {code}", flush=True)
                osc.sendto(osc_message(args.osc_address, code), td)
            sip.sendto(response(args.reply, headers), addr)
        elif method == "REGISTER":
            extra = [f"Contact: {c}" for c in values(headers, "contact")] + ["Expires: 3600"]
            sip.sendto(response("200 OK", headers, extra), addr)
            print(f"ATA registered from {addr[0]}", flush=True)
        elif method in ("OPTIONS", "BYE", "CANCEL"):
            sip.sendto(response("200 OK", headers), addr)
        elif method != "ACK":
            sip.sendto(response("501 Not Implemented", headers), addr)


if __name__ == "__main__":
    main()
