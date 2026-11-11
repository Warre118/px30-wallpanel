#!/usr/bin/env python3
"""
proximity-wake - zet het scherm van het PX30-panel aan bij nadering of touch,
en weer uit na een periode zonder aanwezigheid. Enige eigenaar van de backlight.

- Sensor: stk3310 via IIO (in_proximity_raw, in_illuminance_raw/_scale).
- Instellingen: /data/px30-agent/display.json (geschreven door px30-agent / HA),
  opnieuw gelezen bij SIGHUP. Ontbreekt het bestand: de standaardwaarden hieronder.
- SIGUSR1 = scherm nu aan (zoals een touch), SIGUSR2 = scherm nu uit.
- Live status in /run/px30-display/state.json (alleen bij wijziging):
  {"near": bool, "screen_on": bool, "brightness": %, "lux": x}
Enkel Python-stdlib.
"""
import glob, json, math, os, select, signal, struct, time

POLL_S = 0.2                 # 200 ms, zoals de Android-driver
SETTINGS = "/data/px30-agent/display.json"
STATE = "/run/px30-display/state.json"
TOUCH_NAME = "hynitron"      # deel van de naam van het touch-inputdevice ("Hynitron cst3xx Touchscreen")
DEFAULTS = {
    "brightness": 80,         # % van max_brightness (bij auto: het maximum)
    "auto_brightness": False,
    "brightness_min": 10,     # % ondergrens bij auto
    "screen_timeout": 60,     # s aan na laatste aanwezigheid/touch
    "near_delta": 60,         # zoveel boven de basislijn = "iemand dichtbij"
    "far_delta": 30,          # hysterese: pas onder deze grens terug "ver"
}
LUX_LO, LUX_HI = 1.0, 500.0  # auto: <= LUX_LO -> minimum, >= LUX_HI -> maximum


def find_iio():
    for d in glob.glob("/sys/bus/iio/devices/iio:device*"):
        if os.path.exists(f"{d}/in_proximity_raw"):
            return d
    raise SystemExit("geen IIO-device met in_proximity_raw gevonden (driver stk3310 geladen?)")


def find_backlight():
    bl = sorted(glob.glob("/sys/class/backlight/*"))
    if not bl:
        raise SystemExit("geen backlight in /sys/class/backlight")
    return bl[0]


def find_touch():
    for ev in glob.glob("/sys/class/input/event*"):
        try:
            name = open(f"{ev}/device/name").read().strip().lower()
        except OSError:
            continue
        if TOUCH_NAME in name:
            return "/dev/input/" + os.path.basename(ev)
    return None


def read_num(path, conv=int, default=None):
    try:
        with open(path) as f:
            return conv(f.read().strip())
    except (OSError, ValueError):
        return default


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS) as f:
            raw = json.load(f)
        for k, v in raw.items():
            if k not in s:
                continue
            if isinstance(s[k], bool):
                if isinstance(v, bool):
                    s[k] = v
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                s[k] = v
    except (OSError, ValueError, AttributeError):
        pass
    s["brightness"] = min(100, max(1, int(s["brightness"])))
    s["brightness_min"] = min(s["brightness"], max(1, int(s["brightness_min"])))
    s["screen_timeout"] = max(5, int(s["screen_timeout"]))
    s["far_delta"] = min(int(s["far_delta"]), int(s["near_delta"]) - 1)
    return s


def setup_als(iio):
    """The stk3310 driver starts with a 0.74 ms integration time: indoors that
    always reads 0. 94.7 ms at the finest step (0.1 lux/count, max ~6500 lux)
    gives usable indoor values."""
    for attr, val in (("in_illuminance_integration_time", "0.094720"),
                      ("in_illuminance_scale", "0.1")):
        try:
            with open(f"{iio}/{attr}", "w") as f:
                f.write(val)
        except OSError:
            pass


class Wake:
    def __init__(self):
        self.iio = find_iio()
        setup_als(self.iio)
        self.bl = find_backlight()
        self.max = read_num(f"{self.bl}/max_brightness", int, 255) or 255
        self.cfg = load_settings()
        self.reload = False
        self.force = None            # "on" / "off" from a signal
        self.lux_ema = None
        self.cur_pct = None
        self.last_state = None
        signal.signal(signal.SIGHUP, lambda *_: setattr(self, "reload", True))
        signal.signal(signal.SIGUSR1, lambda *_: setattr(self, "force", "on"))
        signal.signal(signal.SIGUSR2, lambda *_: setattr(self, "force", "off"))

    def lux(self):
        raw = read_num(f"{self.iio}/in_illuminance_raw", float)
        if raw is None:
            return None
        return raw * (read_num(f"{self.iio}/in_illuminance_scale", float, 1.0) or 1.0)

    def target_pct(self):
        c = self.cfg
        if not c["auto_brightness"] or self.lux_ema is None:
            return c["brightness"]
        x = (math.log10(max(self.lux_ema, LUX_LO)) - math.log10(LUX_LO)) / (math.log10(LUX_HI) - math.log10(LUX_LO))
        x = min(1.0, max(0.0, x))
        return round(c["brightness_min"] + x * (c["brightness"] - c["brightness_min"]))

    def set_pct(self, pct):
        if pct == self.cur_pct:
            return
        val = 0 if pct <= 0 else max(1, round(self.max * pct / 100))
        try:
            with open(f"{self.bl}/brightness", "w") as f:
                f.write(str(val))
            self.cur_pct = pct
        except OSError:
            pass

    def write_state(self, near, screen_on, lux):
        st = {"near": near, "screen_on": screen_on,
              "brightness": self.cur_pct if screen_on else 0,
              "lux": None if lux is None else round(lux, 1)}
        if st == self.last_state:
            return
        self.last_state = st
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            tmp = STATE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(st, f)
            os.chmod(tmp, 0o644)
            os.replace(tmp, STATE)
        except OSError:
            pass

    def run(self):
        prox_path = f"{self.iio}/in_proximity_raw"
        touch_dev = find_touch()
        touch_fd = os.open(touch_dev, os.O_RDONLY | os.O_NONBLOCK) if touch_dev else None
        ev_size = struct.calcsize("llHHi")

        baseline = read_num(prox_path, int, 0)
        near = False
        last_presence = time.monotonic()
        screen_on = True
        self.set_pct(self.target_pct())
        lux_t = 0.0

        while True:
            fds = [touch_fd] if touch_fd is not None else []
            try:
                r, _, _ = select.select(fds, [], [], POLL_S)
            except InterruptedError:
                r = []
            now = time.monotonic()
            if self.reload:
                self.reload = False
                self.cfg = load_settings()
                if screen_on:
                    self.set_pct(self.target_pct())
            if r:
                try:
                    while os.read(touch_fd, ev_size * 64):
                        pass
                except BlockingIOError:
                    pass
                last_presence = now

            value = read_num(prox_path, int, baseline)
            if not near and value > baseline + self.cfg["near_delta"]:
                near = True
            elif near and value < baseline + self.cfg["far_delta"]:
                near = False
            if not near:
                # basislijn traag laten meedrijven (temperatuur, stof op het glas)
                baseline = (baseline * 31 + value) // 32
            if near:
                last_presence = now

            if self.force == "on":
                last_presence = now
            elif self.force == "off":
                last_presence = now - self.cfg["screen_timeout"] - 1
            self.force = None

            # licht: elke seconde meten, EMA ~5 s
            lux = None
            if now - lux_t >= 1.0:
                lux_t = now
                lux = self.lux()
                if lux is not None:
                    self.lux_ema = lux if self.lux_ema is None else self.lux_ema * 0.8 + lux * 0.2

            want_on = (now - last_presence) < self.cfg["screen_timeout"]
            if want_on != screen_on:
                screen_on = want_on
                self.cur_pct = None if want_on else self.cur_pct
                self.set_pct(self.target_pct() if want_on else 0)
            elif screen_on and self.cfg["auto_brightness"]:
                t = self.target_pct()
                if self.cur_pct is None or abs(t - self.cur_pct) >= 5:
                    self.set_pct(t)
            self.write_state(near, screen_on, self.lux_ema)


if __name__ == "__main__":
    Wake().run()
