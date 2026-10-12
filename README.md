# Phone → ATA → Raspberry Pi → TouchDesigner

Dial a code on an old phone and have TouchDesigner react to it.

```
 ┌─────────┐  RJ11  ┌───────────────┐  Ethernet   ┌──────────────┐   Wi-Fi (OSC/UDP)  ┌───────────────┐
 │ Old     │───────▶│ Grandstream   │────────────▶│ Raspberry Pi │───────────────────▶│ TouchDesigner │
 │ phone   │        │ ATA (SIP)     │  SIP INVITE │  sip_to_osc  │  /phone/dial "123" │  OSC In DAT   │
 └─────────┘        └───────────────┘  eth0       └──────────────┘  wlan0             └───────────────┘
```

## Do I need Asterisk?

**No, not for this.** The ATA already does the phone side for you: it supplies the dial tone, reads the
dialled digits (tone or rotary pulse), and decides when the number is complete. When you finish dialling, it
sends one SIP `INVITE` message to its "SIP server". The dialled code is in that message:
`INVITE sip:1234@192.168.50.1 SIP/2.0`.

So the Pi only needs to:

1. Get an IP address onto the ATA (the Pi gives it one over Ethernet).
2. Listen for that `INVITE`, take out the number, and reject the call politely so the phone plays a busy tone.
3. Send the number to TouchDesigner as an OSC message.

[The bridge script](./sip_to_osc.py) in this repo does steps 2–3 in about 100 lines of plain
Python, with no packages to install.

Install Asterisk only if you later want any of these (see [When to switch to Asterisk](#when-to-switch-to-asterisk)):
spoken/audio feedback through the handset, reading digits live *during* a call, ringing the phone from
TouchDesigner, or more than one phone.

---

## 1. Network the Pi and the ATA (Pi's `eth0`)

The ATA is plugged straight into the Pi, so there's no router to give it an address. Let the Pi act as a DHCP
server on `eth0`. You'll keep using `wlan0` for SSH and for reaching TouchDesigner.

On Raspberry Pi OS Bookworm (or newer, which uses NetworkManager), over SSH:

```bash
# See existing connections. There may be an automatic "Wired connection 1" on eth0
nmcli con show

# Create a "shared" connection: the Pi becomes 192.168.50.1 and runs DHCP for the ATA.
# (It also NATs the ATA out through Wi-Fi, so the ATA can sync time and update its firmware.)
sudo nmcli con add type ethernet ifname eth0 con-name ata-link \
  ipv4.method shared ipv4.addresses 192.168.50.1/24 ipv6.method disabled \
  connection.autoconnect yes connection.autoconnect-priority 100
sudo nmcli con up ata-link
```

If "Wired connection 1" keeps taking over `eth0`, delete it: `sudo nmcli con delete "Wired connection 1"`.
Changing `eth0` does not affect `wlan0`, so your SSH session over Wi-Fi stays connected.

> On older Raspberry Pi OS (Bullseye, which uses `dhcpcd`), `nmcli` won't exist. Instead, give `eth0` a static IP in
> `/etc/dhcpcd.conf` and install `dnsmasq` with `dhcp-range=192.168.50.10,192.168.50.50,12h` on `eth0`.

Power-cycle the ATA (or unplug and replug the Ethernet) so it requests an address.

## 2. Find the ATA and open its web page

Find its IP address in any one of these ways:

- From the Pi: `cat /var/lib/NetworkManager/dnsmasq-eth0.leases` or `ip neigh show dev eth0`
- From the phone: pick up, dial `***`, wait for the voice menu, then dial `02`. The ATA reads out its IP address.

The ATA is only reachable from the Pi, so open its web UI from your laptop through an SSH tunnel
(swap in the ATA's IP address and your Pi username/hostname):

```bash
ssh -L 8080:192.168.50.10:80 pi@raspberrypi.local
```

Then browse to <http://localhost:8080>. The login is `admin` with either `admin` (older units) or the
random password printed on the sticker underneath the ATA.

> On a model with separate **WAN** and **LAN** ports (HT812/HT814), plug the Pi into **WAN**.

## 3. Configure the ATA

The menu names below come from the HT801/HT802 firmware. Other models use very similar names. Under
**FXS Port** (or **Profile 1**):

| Setting | Value | Why |
|---|---|---|
| Account Active | Yes | |
| Primary SIP Server | `192.168.50.1` | The Pi |
| Outbound Proxy | *(empty)* | |
| SIP User ID | `100` | Any number. Nothing checks it |
| Authenticate ID / Password | *(empty)* | |
| SIP Registration | Yes | The bridge accepts any registration |
| Outgoing Call without Registration | Yes | Calls still go out even if registration fails |
| Local SIP Port | `5060` | Default |
| SIP Transport | UDP | Default |
| Use # as Dial Key | Yes | Dial `1234#` to send right away… |
| No Key Entry Timeout | `3`–`4` | …or just stop dialling and wait this many seconds |
| Dial Plan | `{ x+ \| *x+ }` | Accepts any string of digits |
| Enable Call Features | No | Stops the ATA from capturing `*` codes (`*30`, `*67`…) for itself |
| Enable Pulse Dialing | Yes | **Only if it's a rotary phone** |

Click **Apply**, then reboot the ATA. Dial `***` then `01` to hear the status, or check **Status → Port Status**.

## 4. Install the SIP → OSC bridge on the Pi

The bridge is [`sip_to_osc.py`](./sip_to_osc.py) in this repo. It only uses the Python standard library, so there's
nothing to `pip install`. Clone the repo onto the Pi over SSH:

```bash
cd ~
git clone https://github.com/<your-username>/AnaloguePhone-OSC-bridge.git
cd AnaloguePhone-OSC-bridge
```

To get updates later, run `git pull` in that folder, then `sudo systemctl restart sip-to-osc` if you've set up
[the boot service](#6-start-the-bridge-on-boot).

Run it, pointing it at the computer running TouchDesigner. TouchDesigner has no Raspberry Pi version, so it
runs on your Mac/PC. That computer must be on the same Wi-Fi as the Pi:

```bash
python3 ~/AnaloguePhone-OSC-bridge/sip_to_osc.py --td-host 192.168.1.50 --td-port 7000
```

Now pick up the phone and dial `1234#`. You should see:

```
ATA registered from 192.168.50.10
dialled: 1234
```

The handset then plays a busy tone. That's expected, because the call is deliberately rejected after the code
is read.

Options: `--osc-address /phone/dial`, `--reply "486 Busy Here"` (try `"404 Not Found"` or `"603 Decline"`
to get a different tone), and `--test 1234`, which sends one OSC message and exits. Use `--test` to check the
TouchDesigner side without the phone.

## 5. TouchDesigner

1. Add an **OSC In DAT** and set **Network Port** to `7000`. Allow TouchDesigner through the OS firewall
   (Windows Defender / macOS) when asked.
2. Run `python3 ~/AnaloguePhone-OSC-bridge/sip_to_osc.py --td-host <TD-IP> --test 1234` on the Pi. A
   `/phone/dial 1234` row should show up in the DAT.
3. Edit the DAT's callbacks to act on each code:

```python
# osc in callbacks
def onReceiveOSC(dat, rowIndex, message, byteData, timeStamp, address, args, peer):
    if address != '/phone/dial' or not args:
        return
    code = str(args[0])          # string, so leading zeros ("007") survive
    print('phone dialled', code)

    if code == '1':
        op('switch1').par.index = 0
    elif code == '2':
        op('switch1').par.index = 1
    elif code == '999':
        op('moviefilein1').par.cuepulse.pulse()
    # ...or use: op('codes_table')[code, 1] to look up codes in a Table DAT
    return
```

## 6. Start the bridge on boot

```bash
sudo tee /etc/systemd/system/sip-to-osc.service >/dev/null <<'EOF'
[Unit]
Description=Phone dial codes -> TouchDesigner OSC
After=network-online.target
Wants=network-online.target

[Service]
User=pi
ExecStart=/usr/bin/python3 /home/pi/AnaloguePhone-OSC-bridge/sip_to_osc.py --td-host 192.168.1.50 --td-port 7000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sip-to-osc
journalctl -u sip-to-osc -f      # watch the live log
```

Replace `pi` / `/home/pi` with your Pi username. Give the TouchDesigner computer a fixed IP (a DHCP reservation
on the router) so `--td-host` keeps working.

## Troubleshooting

| Symptom | Check |
|---|---|
| No dial tone | ATA powered and phone in the **Phone/FXS** port. Wait ~1 min after boot. |
| No address on the ATA | `nmcli con show --active` shows `ata-link` on `eth0`. Check the cable. Power-cycle the ATA. |
| Script prints nothing when you dial | Run `sudo tcpdump -i eth0 -n -A port 5060` to see the SIP traffic. Check that **Primary SIP Server** is `192.168.50.1` and **Account Active** is Yes. |
| `dialled:` appears but nothing reaches TouchDesigner | Correct `--td-host`, same Wi-Fi, OS firewall allows UDP 7000. Try `--test`. |
| Code arrives several seconds late | Dial `#` at the end, or lower **No Key Entry Timeout**. |
| `*` codes don't arrive | Set **Enable Call Features = No**, and check the Dial Plan allows them. |
| Rotary phone digits not recognised | **Enable Pulse Dialing = Yes**. Some older Grandstream models (HT70x) don't support pulse dialing. |
| `Address already in use` | Another SIP program (e.g. Asterisk) is using port 5060, or the service is already running. |

## When to switch to Asterisk

Move to Asterisk once the simple version works and you need any of these:

- **Audio feedback**: play a sound or spoken message in the handset ("code accepted").
- **Live digits**: answer the call, then send each key press to TouchDesigner as it's dialled (DTMF).
- **Ringing the phone** from TouchDesigner (an incoming call to the ATA).
- **Several phones/ATAs**, or calling between them.

Asterisk isn't in the Raspberry Pi OS (Debian) repositories, so you'd have to compile it from source
(~30–60 min on a Pi 4), or flash a ready-made image such as RasPBX/FreePBX. Stop `sip-to-osc` first, because both use
port 5060. The ATA settings stay the same, except you'd add a matching user/password in Asterisk's
`pjsip.conf`. TouchDesigner gets the same OSC message from the dialplan via `liblo-tools`
(`sudo apt install liblo-tools`):

```ini
; extensions.conf (sketch)
[from-phone]
exten => _X.,1,Answer()
 same => n,System(oscsend 192.168.1.50 7000 /phone/dial s ${EXTEN})
 same => n,Playback(beep)
 same => n,Hangup()
```
