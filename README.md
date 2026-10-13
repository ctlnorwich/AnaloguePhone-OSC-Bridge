# Phone → ATA → Mac (TouchDesigner)

Dial a code on an old phone and have TouchDesigner react to it, with the ATA plugged straight into the Mac
that runs TouchDesigner. Optionally, play a sound clip in the handset for each code.

```
 ┌─────────┐  RJ11  ┌───────────────┐  Ethernet    ┌──────────────────────────────────────────┐
 │ Old     │───────▶│ Grandstream   │─────────────▶│ Mac                                      │
 │ phone   │        │ ATA (SIP)     │  SIP INVITE  │  sip_to_osc.py ──OSC──▶ TouchDesigner    │
 └─────────┘        └───────────────┘              │                127.0.0.1:7000            │
                     192.168.50.10                 │ 192.168.50.1                             │
                                                   └──────────────────────────────────────────┘
```

## How it works

The ATA (analogue telephone adapter) does the phone side. It supplies the dial tone, reads the dialled digits
(tone or rotary pulse), and decides when the number is complete. When you finish dialling, it sends a SIP
`INVITE` message to its "SIP server", which here is the Mac. The dialled code is in that message:
`INVITE sip:1234@192.168.50.1 SIP/2.0`.

A small Python script on the Mac:

1. Listens for that `INVITE` and takes out the number.
2. Sends the number to TouchDesigner as an OSC message (`/phone/dial "1234"`).
3. Either rejects the call so the phone plays a busy tone ([`sip_to_osc.py`](./sip_to_osc.py)), or answers it,
   plays a sound clip, and hangs up ([`sip_to_osc_audio.py`](./sip_to_osc_audio.py), see [section 7](#7-play-a-sound-in-the-handset-optional)).

Both scripts use only the Python standard library, so there's nothing to install.

## You'll need

- A Grandstream ATA (HT801, HT802 or similar), an old phone, and an Ethernet cable.
- An Ethernet port on the Mac. Most MacBooks need a USB-C to Ethernet adapter.
- Python 3. Run `python3 --version` in Terminal. If macOS offers to install the Command Line Tools, accept.
  Homebrew's `python3` also works.
- This repo on the Mac, somewhere outside `Desktop`, `Documents` and `Downloads`. macOS restricts
  background access to those folders, which matters if you
  [start the bridge at login](#6-start-the-bridge-at-login-optional).

  ```bash
  mkdir -p ~/Dev && cd ~/Dev
  git clone https://github.com/ctlnorwich/AnaloguePhone-OSC-bridge.git
  ```

  To get updates later, run `git pull` in that folder.

---

## 1. Give the Mac and the ATA fixed addresses

The ATA is plugged straight into the Mac, so nothing on that cable hands out addresses. Give both ends fixed
ones. That way nothing needs to run a DHCP server, and the addresses never change.

### Mac: `192.168.50.1`

1. Plug the Ethernet adapter into the Mac and the ATA into the adapter.
   On a model with separate **WAN** and **LAN** ports (HT812/HT814), use **WAN**.
2. Open **System Settings → Network**, select the adapter (for example *USB 10/100/1000 LAN*), then **Details… → TCP/IP**.
3. Set **Configure IPv4** to *Manually*, **IP Address** to `192.168.50.1`, **Subnet Mask** to `255.255.255.0`,
   and leave **Router** empty. With no router set, the Mac keeps using Wi-Fi for the internet.
4. Click **OK**.

### ATA: `192.168.50.10`

Pick whichever is easiest:

- **From its web page:** if you can already reach it (for example through Internet Sharing, below), go to
  **Basic Settings → IP Address**, choose **Statically configured**, then set IP Address to `192.168.50.10`, Subnet Mask to
  `255.255.255.0`, and Default Router to `192.168.50.1`. Click **Apply**.
- **From the handset:** pick up, dial `***`, then:
  - `01`, then `9` to switch the address mode to static
  - `02`, then the IP as 12 digits: `192168050010`
  - `03`, then the subnet mask: `255255255000`
  - `04`, then the gateway: `192168050001`

  Option numbers and digit entry vary a little between models, so listen to the voice prompts. Hang up when done.

Reboot the ATA (unplug the power and plug it back in), then check from Terminal on the Mac:

```bash
ping -c 3 192.168.50.10
```

> **Rather not use a fixed IP on the ATA?** Turn on **System Settings → General → Sharing → Internet Sharing**:
> share *Wi-Fi* to the Ethernet adapter. The Mac becomes `192.168.2.1` and gives the ATA an address by DHCP. Find
> it with `arp -a -i bridge100` or by dialling `***` then `02`. Then use `192.168.2.1` as the **Primary SIP Server** in
> step 3 instead of `192.168.50.1`. Managed or work Macs often have Internet Sharing turned off.

## 2. Open the ATA's web page

Browse to <http://192.168.50.10>. Include `http://`, because the ATA has no HTTPS. The login is `admin`, with
either `admin` (older units) or the password printed on the sticker underneath the ATA.

If the page won't load but `ping` works, web access may be switched off (common on ATAs that have been used
before). Pick up, dial `***`, then `12`. The ATA says whether web access is on. Press `9` to switch it on, then
hang up.

## 3. Configure the ATA

The menu names below come from the HT801/HT802 firmware. Other models use very similar names. Under
**FXS Port** (or **Profile 1**):

| Setting | Value | Why |
|---|---|---|
| Account Active | Yes | |
| Primary SIP Server | `192.168.50.1` | The Mac |
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
| Preferred Vocoder, choice 1 | PCMU | The audio format the clips in [section 7](#7-play-a-sound-in-the-handset-optional) use |

Click **Apply**, then reboot the ATA. Check **Status → Port Status** to see that the account is active. On older
firmware some names differ slightly.

## 4. Run the bridge

```bash
cd ~/Dev/AnaloguePhone-OSC-bridge
python3 sip_to_osc.py
```

TouchDesigner is on the same Mac, so the defaults (`--td-host 127.0.0.1 --td-port 7000`) are already right.

The first time, macOS asks whether **python3** may *accept incoming network connections*. Click **Allow**. That's
the ATA reaching the script over Ethernet. If you clicked Deny by mistake, fix it under **System Settings →
Network → Firewall → Options**.

Pick up the phone and dial `1234#`. You should see:

```
listening for SIP on UDP 5060, sending OSC to ('127.0.0.1', 7000)
ATA registered from 192.168.50.10
dialled: 1234
```

The handset then plays a busy tone. That's expected, because the call is deliberately rejected after the code is read.
Stop the script with **Ctrl+C**.

Options: `--osc-address /phone/dial`, `--reply "486 Busy Here"` (try `"404 Not Found"` or `"603 Decline"` for
a different tone), and `--test 1234`, which sends one OSC message and exits.

## 5. TouchDesigner

1. Add an **OSC In DAT** and set **Network Port** to `7000`.
2. In Terminal, run `python3 sip_to_osc.py --test 1234`. A `/phone/dial 1234` row should show up in the DAT.
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

## 6. Start the bridge at login (optional)

For an installation, have macOS start the bridge when you log in and restart it if it stops. Find your Python
first:

```bash
which python3        # e.g. /opt/homebrew/bin/python3 or /usr/bin/python3
```

Create `~/Library/LaunchAgents/com.analoguephone.sip-to-osc.plist`, replacing the Python path and
`YOURNAME`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.analoguephone.sip-to-osc</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/python3</string>
    <string>/Users/YOURNAME/Dev/AnaloguePhone-OSC-bridge/sip_to_osc.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/sip-to-osc.log</string>
  <key>StandardErrorPath</key><string>/tmp/sip-to-osc.log</string>
</dict>
</plist>
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.analoguephone.sip-to-osc.plist   # start now and at every login
tail -f /tmp/sip-to-osc.log                                                                  # watch the live log
launchctl kickstart -k gui/$(id -u)/com.analoguephone.sip-to-osc                             # restart (e.g. after git pull)
launchctl bootout gui/$(id -u)/com.analoguephone.sip-to-osc                                  # stop and disable
```

Don't run it from Terminal while the login item is running, because both would need port 5060. To use the
audio version, change `sip_to_osc.py` to `sip_to_osc_audio.py` in the plist, then run the `bootout` and
`bootstrap` commands again.

Also stop the Mac from sleeping during a show. Sleep pauses the bridge. Turn on **System Settings → Battery →
Options… → Prevent automatic sleeping on power adapter when the display is off**, or run the bridge as
`caffeinate -i python3 sip_to_osc.py`.

## 7. Play a sound in the handset (optional)

[`sip_to_osc_audio.py`](./sip_to_osc_audio.py) works the same way as `sip_to_osc.py`, except that after sending
the code to TouchDesigner it **answers the call, plays a sound clip in the handset, and hangs up**. It also only
uses the Python standard library. Keep it in the same folder as `sip_to_osc.py`, because it reuses code from
that file.

Put the clips in the [`sounds/`](./sounds) folder, named after the code:

| File | Played when |
|---|---|
| `sounds/1234.wav` | `1234` is dialled |
| `sounds/default.wav` | any code that has no clip of its own (optional) |

Codes with no clip, and no `default.wav`, still get the busy tone (or whatever `--reply` is set to).
The script looks for clips on every call, so you can add or change them without restarting it.

Clips must be **8000 Hz mono WAV**, as either μ-law or 16-bit PCM (phone lines can't carry better quality
anyway). Convert any audio file with `ffmpeg` (`brew install ffmpeg`):

```bash
cd ~/Dev/AnaloguePhone-OSC-bridge
ffmpeg -i ~/Downloads/code-accepted.mp3 -ar 8000 -ac 1 -c:a pcm_mulaw sounds/1234.wav
```

### First test

1. Make a test beep for code `1234`:

   ```bash
   ffmpeg -f lavfi -i "sine=frequency=880:duration=1" -ar 8000 -ac 1 -c:a pcm_mulaw sounds/1234.wav
   ```

   Without `ffmpeg`, you can make an 8 kHz mono WAV with macOS's text-to-speech instead:
   `say -o sounds/1234.wav --data-format=LEI16@8000 "Code accepted"`.

2. Stop `sip_to_osc.py` if it's running (**Ctrl+C**, or `bootout` if it runs at login). Only one bridge can use port 5060.
3. Start the audio version:

   ```bash
   python3 sip_to_osc_audio.py
   ```

4. Pick up the phone and dial `1234#`. You should hear the beep, and the script should print:

   ```
   clips in /Users/you/Dev/AnaloguePhone-OSC-bridge/sounds: 1234.wav
   ATA registered from 192.168.50.10
   dialled: 1234
   playing 1234.wav (1.0 s)
   ```

5. Dial a code with no clip, such as `999#`. You should hear the busy tone as before.

After the clip the bridge hangs up. What you hear next (silence, a busy tone, or a dial tone) depends on the
ATA. Hanging up during a clip stops it.

`sip_to_osc_audio.py` has the same `--td-host`, `--td-port`, `--osc-address` and `--reply` options, plus
`--sounds /some/folder` to keep the clips somewhere else.

## Troubleshooting

| Symptom | Check |
|---|---|
| No dial tone | ATA powered and phone in the **Phone/FXS** port. Wait ~1 min after boot. |
| `ping 192.168.50.10` fails | Adapter set to `192.168.50.1` / `255.255.255.0` in **System Settings → Network**. ATA rebooted after its IP change. Dial `***` `02` to hear the ATA's actual address. |
| Script prints nothing when you dial | Firewall allows **python3** (section 4). Watch traffic with `sudo tcpdump -i any -n -A port 5060`. Check that **Primary SIP Server** is `192.168.50.1` and **Account Active** is Yes. |
| `dialled:` appears but nothing reaches TouchDesigner | OSC In DAT is on port `7000` and active. Try `--test 1234`. |
| Code arrives several seconds late | Dial `#` at the end, or lower **No Key Entry Timeout**. |
| `*` codes don't arrive | Set **Enable Call Features = No**, and check the Dial Plan allows them. |
| Rotary phone digits not recognised | **Enable Pulse Dialing = Yes**. Some older Grandstream models (HT70x) don't support pulse dialing. |
| `Address already in use` | The bridge is already running (Terminal or the login item). Find it with `sudo lsof -nP -iUDP:5060`. |
| Mac loses internet after plugging in the ATA | **Router** must be empty on the Ethernet adapter, so Wi-Fi stays the default route. |
| `playing …` appears but the handset is silent | Set **Preferred Vocoder choice 1 = PCMU** on the ATA (section 3). Check that the firewall allows **python3**. Watch the audio with `sudo tcpdump -i any -n udp and not port 5060`. |
| The phone rings out or hangs up straight away instead of playing | Watch the SIP messages with `sudo tcpdump -i any -n -A port 5060` and check the ATA's reply to the bridge's `200 OK`. |
| `can't play …wav` | The clip isn't 8000 Hz mono. Convert it with the `ffmpeg` command in [section 7](#7-play-a-sound-in-the-handset-optional). |

## Going further: Asterisk

These scripts cover one code per call, with an optional clip. For any of the following you'd need a full phone
system such as [Asterisk](https://www.asterisk.org/):

- **Interactive audio**: voice menus, recording messages, or text-to-speech generated during the call.
- **Live digits**: answering the call, then sending each key press to TouchDesigner as it's dialled (DTMF).
- **Ringing the phone** from TouchDesigner (an incoming call to the ATA).
- **Several phones/ATAs**, or calling between them.

Asterisk doesn't run well on macOS. It's usually run on a small Linux computer such as a Raspberry Pi, with the
ATA plugged into that and the Pi sending OSC to the Mac over the network. The ATA settings stay the same,
except that the Primary SIP Server becomes the Pi, and you'd add a matching user and password in Asterisk.
