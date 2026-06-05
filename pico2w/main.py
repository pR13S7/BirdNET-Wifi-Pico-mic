import gc
from array import array
import time
import socket
import network
import rp2
import micropython
from machine import I2S, Pin, freq

freq(150_000_000)
rp2.country('UA')

try:
    from lcd import LCD, WHITE, RED, GREEN, YELLOW, GRAY, ORANGE, CYAN
    lcd = LCD()
    HAS_LCD = True
except Exception:
    HAS_LCD = False

btn_a = Pin(15, Pin.IN, Pin.PULL_UP)
screen_on = True
btn_prev = True

WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"

SERVER_IP = "192.168.0.50"
SERVER_PORT = 5005

SAMPLE_RATE = 16000
PACKET_FRAMES = 1024
I2S_BITS = 32
I2S_FMT = I2S.STEREO
RECONNECT_DELAY = 3

NOISE_GATE_THRESHOLD = 0
SLEW_LIMIT = 3000
USE_DSP = True
I2S_SLOT = 2  # 0=left, 1=right, 2=auto

I2S_SCK = 16
I2S_WS = 17
I2S_SD = 18

led = Pin("LED", Pin.OUT)


dsp_state = array('i', [0, 0, 0, 0])  # [hp_prev_x, hp_prev_y, last_good, blank_count]


@micropython.native
def detect_slot(buf32, n, prev_slot):
    left_peak = 0
    right_peak = 0
    scan_n = n
    if scan_n > 1024:
        scan_n = 1024
    for i in range(0, scan_n, 8):
        l = buf32[i + 2] | (buf32[i + 3] << 8)
        if l >= 32768:
            l -= 65536
        if l < 0:
            l = -l
        if l > left_peak:
            left_peak = l

        r = buf32[i + 6] | (buf32[i + 7] << 8)
        if r >= 32768:
            r -= 65536
        if r < 0:
            r = -r
        if r > right_peak:
            right_peak = r

    # Switch only when one side is clearly stronger to avoid flapping.
    if right_peak > left_peak + (left_peak // 2):
        return 1, left_peak, right_peak
    if left_peak > right_peak + (right_peak // 2):
        return 0, left_peak, right_peak
    return prev_slot, left_peak, right_peak


@micropython.native
def extract_slot(buf32, buf16, n, slot):
    off = 2
    if slot:
        off = 6
    j = 0
    for i in range(0, n, 8):
        buf16[j] = buf32[i + off]
        buf16[j + 1] = buf32[i + off + 1]
        j += 2


@micropython.viper
def process_audio(buf_in, buf_out, n: int, st, slot_off: int) -> int:
    inp = ptr8(buf_in)
    out = ptr8(buf_out)
    s = ptr32(st)
    hp_px = s[0]
    hp_py = s[1]
    last_good = s[2]
    blank = s[3]
    peak = 0
    j = 0
    i = 0
    while i < n:
        raw = int(inp[i + slot_off]) | (int(inp[i + slot_off + 1]) << 8)
        if raw >= 32768:
            raw -= 65536
        y = raw - hp_px + (241 * hp_py) // 256
        hp_px = raw
        hp_py = y
        if y > 32767:
            y = 32767
        elif y < -32768:
            y = -32768
        delta = y - last_good
        if delta > 12000 or delta < -12000:
            blank = 2
        if blank > 0:
            y = last_good
            blank -= 1
        else:
            last_good = y
        amp = y if y >= 0 else -y
        if amp > peak:
            peak = amp
        val = y & 0xFFFF
        out[j] = val & 0xFF
        out[j + 1] = (val >> 8) & 0xFF
        j += 2
        i += 8
    s[0] = hp_px
    s[1] = hp_py
    s[2] = last_good
    s[3] = blank
    return peak


stat_kbps = [0]
stat_rssi = [0]
stat_pkts = [0]
stat_mem = [0]


def fmt_uptime(sec):
    d = sec // 86400
    h = (sec % 86400) // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if d > 0:
        return f"{d}d {h:02d}h {m:02d}m {s:02d}s"
    return f"{h}h {m:02d}m {s:02d}s"


def draw_info(wifi_ip, bridge, uptime_s, sent_mb, msg):
    lcd.clear()
    lcd.text("BirdNET Mic", 4, 4, GREEN)
    lcd.hline(4, 16, 232, GRAY)

    if wifi_ip:
        lcd.text(f"WiFi: OK  {stat_rssi[0]}dBm", 4, 24, GREEN)
        lcd.text(wifi_ip, 4, 36, WHITE)
    else:
        lcd.text("WiFi: connecting", 4, 24, YELLOW)

    lcd.hline(4, 50, 232, GRAY)

    if bridge:
        lcd.text("Bridge: Streaming", 4, 58, GREEN)
    else:
        lcd.text("Bridge: Waiting", 4, 58, RED)
    lcd.text(f"{SERVER_IP}:{SERVER_PORT}", 4, 70, GRAY)

    lcd.hline(4, 84, 232, GRAY)

    lcd.text(f"Up: {fmt_uptime(uptime_s)}", 4, 92, WHITE)
    lcd.text(f"Sent: {sent_mb:.1f} MB  {stat_kbps[0]:.0f} KB/s", 4, 104, GREEN)
    lcd.text(f"Pkts: {stat_pkts[0]}  Crash: {crash_count}", 4, 116, CYAN)

    lcd.hline(4, 130, 232, GRAY)

    lcd.text(f"Rate: {SAMPLE_RATE}Hz  Mem: {stat_mem[0]}KB", 4, 138, GRAY)

    if msg:
        lcd.hline(4, 220, 232, GRAY)
        lcd.text(msg, 4, 228, ORANGE)

    lcd.show()


def update_display(wifi_ip, bridge, uptime_s, sent_mb, msg=None):
    if not HAS_LCD or not screen_on:
        return
    draw_info(wifi_ip, bridge, uptime_s, sent_mb, msg)


def connect_wifi():
    if HAS_LCD:
        draw_info(None, False, 0, 0, "Booting...")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.disconnect()
    time.sleep(1)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    print("Connecting to WiFi", end="")
    for _ in range(30):
        s = wlan.status()
        if s == 3:
            break
        if s < 0:
            print(f" status={s}, retrying...", end="")
            wlan.disconnect()
            time.sleep(2)
            wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        print(".", end="")
        time.sleep(1)

    if not wlan.isconnected():
        print("\nWiFi FAILED — blinking LED")
        if HAS_LCD:
            draw_info(None, False, 0, 0, "WiFi FAILED!")
        while True:
            led.toggle()
            time.sleep(0.1)

    ip = wlan.ifconfig()[0]
    print(f"\nConnected! IP: {ip}")
    led.on()
    wlan.config(pm=0xa11140)
    wlan.config(txpower=8)
    if HAS_LCD:
        draw_info(ip, False, 0, 0, "WiFi OK")
    return wlan, ip


def tcp_connect(wifi_ip):
    if HAS_LCD:
        draw_info(wifi_ip, False, 0, 0, "Connecting TCP...")
    for attempt in range(3):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((SERVER_IP, SERVER_PORT))
            print(f"TCP connected to {SERVER_IP}:{SERVER_PORT}")
            led.on()
            return s
        except OSError as e:
            print(f"TCP failed ({e}), retry in {RECONNECT_DELAY}s...")
            led.off()
            try:
                s.close()
            except:
                pass
            time.sleep(RECONNECT_DELAY)
    raise OSError("TCP connect failed 10 times, WiFi likely dead")


def run():
    global screen_on, btn_prev
    wlan, wifi_ip = connect_wifi()

    audio_in = I2S(
        0,
        sck=Pin(I2S_SCK),
        ws=Pin(I2S_WS),
        sd=Pin(I2S_SD),
        mode=I2S.RX,
        bits=I2S_BITS,
        format=I2S_FMT,
        rate=SAMPLE_RATE,
        ibuf=40000,
    )

    print(f"I2S: INMP441 at {SAMPLE_RATE} Hz → {SERVER_IP}:{SERVER_PORT}")

    buf = bytearray(PACKET_FRAMES * 8)
    out = bytearray(PACKET_FRAMES * 2)
    out_mv = memoryview(out)
    silence = bytearray(PACKET_FRAMES * 2)

    dsp_state[0] = 0
    dsp_state[1] = 0
    dsp_state[2] = 0
    slot_sel = 0
    slot_lpk = 0
    slot_rpk = 0

    while True:
        sock = tcp_connect(wifi_ip)
        sock.settimeout(30)
        total_bytes = 0
        start = time.ticks_ms()
        last_log = start

        update_display(wifi_ip, True, 0, 0)

        try:
            while True:
                num_read = audio_in.readinto(buf)
                if num_read == 0:
                    continue

                j = num_read // 4
                if I2S_SLOT == 2:
                    slot_sel, slot_lpk, slot_rpk = detect_slot(buf, num_read, slot_sel)
                else:
                    slot_sel = 1 if I2S_SLOT else 0
                slot_off = 6 if slot_sel else 2

                if USE_DSP:
                    peak = process_audio(buf, out, num_read, dsp_state, slot_off)
                    if NOISE_GATE_THRESHOLD > 0 and peak < NOISE_GATE_THRESHOLD:
                        to_send = memoryview(silence)[:j]
                    else:
                        to_send = out_mv[:j]
                else:
                    extract_slot(buf, out, num_read, slot_sel)
                    to_send = out_mv[:j]
                sent = 0
                while sent < j:
                    n = sock.send(to_send[sent:j])
                    if n == 0:
                        raise OSError("send returned 0")
                    sent += n
                total_bytes += j

                now = time.ticks_ms()

                btn_val = btn_a.value()
                if not btn_val and btn_prev:
                    screen_on = not screen_on
                    if HAS_LCD:
                        lcd.backlight(screen_on)
                        if screen_on:
                            elapsed = time.ticks_diff(now, start) // 1000
                            sent_mb = total_bytes / (1024 * 1024)
                            update_display(wifi_ip, True, elapsed, sent_mb)
                btn_prev = btn_val

                if time.ticks_diff(now, last_log) > 60_000:
                    elapsed = time.ticks_diff(now, start) // 1000
                    sent_mb = total_bytes / (1024 * 1024)
                    stat_kbps[0] = total_bytes / 1024 / max(elapsed, 1)
                    stat_pkts[0] = total_bytes // (PACKET_FRAMES * 2)
                    stat_mem[0] = gc.mem_free() // 1024
                    try:
                        stat_rssi[0] = wlan.status('rssi')
                    except:
                        pass
                    if USE_DSP:
                        slot_name = "R" if slot_sel else "L"
                        slot_info = f"slot=dsp-{slot_name} lpk={slot_lpk} rpk={slot_rpk}"
                    else:
                        slot_name = "R" if slot_sel else "L"
                        slot_info = f"slot={slot_name} lpk={slot_lpk} rpk={slot_rpk}"
                    print(f"[pico] {elapsed}s {stat_kbps[0]:.0f}KB/s sent={sent_mb:.1f}MB rssi={stat_rssi[0]} {slot_info}")
                    update_display(wifi_ip, True, elapsed, sent_mb)
                    last_log = now

        except OSError as e:
            print(f"Connection lost ({e}), reconnecting...")

        try:
            sock.close()
        except:
            pass
        time.sleep(1)


crash_count = 0
while True:
    try:
        run()
    except Exception as e:
        crash_count += 1
        print(f"\n*** CRASH #{crash_count}: {type(e).__name__}: {e} ***")
        if HAS_LCD:
            lcd.clear()
            lcd.text("CRASH - restarting", 4, 100, RED)
            lcd.text(f"#{crash_count}: {type(e).__name__}", 4, 116, ORANGE)
            lcd.text(str(e)[:28], 4, 132, GRAY)
            lcd.show()
        led.off()
        time.sleep(3)
    except KeyboardInterrupt:
        print("Stopped by user")
        led.off()
        if HAS_LCD:
            lcd.clear()
            lcd.text("Stopped", 4, 110, YELLOW)
            lcd.show()
        break
