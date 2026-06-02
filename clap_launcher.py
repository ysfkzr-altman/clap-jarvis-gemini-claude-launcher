"""
clap_launcher.py
----------------
Listens to your mic. When it detects TWO claps in quick succession,
it opens Claude (snapped left), Gemini (snapped right), and Welcome to
the Jungle on YouTube in a tab on the Gemini window.

Requirements (install once):
    pip install sounddevice scipy numpy pywin32

Usage:
    python clap_launcher.py
"""

import subprocess
import time
import sys
import numpy as np
import ctypes

try:
    import sounddevice as sd
    from scipy.signal import butter, lfilter
except ImportError:
    print("Missing dependencies. Run:\n  pip install sounddevice scipy numpy pywin32")
    sys.exit(1)

if sys.platform == "win32":
    try:
        import win32gui
        import win32con
        import win32api
    except ImportError:
        print("Missing pywin32. Run:\n  pip install pywin32")
        sys.exit(1)

# ── Config ──────────────────────────────────────────────────────────────────
SAMPLE_RATE       = 44100       # Hz
CHUNK_DURATION    = 0.05        # seconds per audio chunk (50ms)
CLAP_THRESHOLD    = 0.097        # amplitude threshold (0.0–1.0); lower = more sensitive
MIN_CLAP_GAP      = 0.15        # min seconds between two claps
MAX_CLAP_GAP      = 0.8         # max seconds between two claps (double-clap window)
COOLDOWN          = 3.0         # seconds to wait after triggering before listening again
MP3_PATH = r"C:\Users\hp1\jungle.mp3"

CHROME_EXE = "chrome"           # or full path e.g. r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# ── Debug: set to True to print live amplitude on every chunk ────────────────
DEBUG_AMPLITUDE = False

# ── Mic device index (None = system default) ─────────────────────────────────
# Run:  python clap_launcher.py --list-devices   to find your mic's index
# Then set it here, e.g.:  MIC_DEVICE = 2
MIC_DEVICE = 1
# ─────────────────────────────────────────────────────────────────────────────


def list_devices():
    """Print all available input (microphone) devices with their index."""
    print("\n🎙️  Available input devices:\n")
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            marker = " ◀ default" if i == sd.default.device[0] else ""
            print(f"  [{i}]  {dev['name']}{marker}")
    print("\n  Set MIC_DEVICE = <index> at the top of clap_launcher.py\n")


def get_screen_size():
    """Return (width, height) of the primary monitor in pixels."""
    w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
    h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
    return w, h


def find_chrome_windows_after(existing_hwnds, timeout=8):
    """
    Poll for new Chrome top-level windows that weren't in existing_hwnds.
    Returns a list of new HWNDs found, up to `timeout` seconds.
    """
    deadline = time.time() + timeout
    found = []
    while time.time() < deadline:
        def cb(hwnd, _):
            if hwnd not in existing_hwnds and win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                cls   = win32gui.GetClassName(hwnd)
                if cls == "Chrome_WidgetWin_1" and title:
                    found.append(hwnd)
        win32gui.EnumWindows(cb, None)
        if found:
            return found
        time.sleep(0.2)
    return found


def snap_window(hwnd, side, screen_w, screen_h):
    """
    Move and resize hwnd to fill the left or right half of the screen.
    side: 'left' or 'right'
    """
    half_w = screen_w // 2
    x = 0 if side == "left" else half_w
    # Restore first in case it's maximised
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.1)
    win32gui.SetWindowPos(
        hwnd,
        win32con.HWND_TOP,
        x, 0, half_w, screen_h,
        win32con.SWP_SHOWWINDOW,
    )


def existing_chrome_hwnds():
    hwnds = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "Chrome_WidgetWin_1":
            hwnds.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return set(hwnds)


def open_tabs():
    """Open Claude (left half) and Gemini + YouTube (right half)."""
    print("\n  CLAP DETECTED — Welcome to the jungle, baby!\n")

    screen_w, screen_h = get_screen_size()
    before = existing_chrome_hwnds()

    # Play MP3 silently in the background using Windows API
    print("  🎵  Playing background MP3...")
    ctypes.windll.winmm.mciSendStringW(f'play "{MP3_PATH}"', None, 0, None)

    # ── Window 1: Claude (left) ──────────────────────────────────────────────
    print("  Opening Claude...")
    subprocess.Popen(
        f'start {CHROME_EXE} --new-window "https://claude.ai"',
        shell=True
    )
    time.sleep(1.5)

    new1 = find_chrome_windows_after(before, timeout=6)
    if new1:
        claude_hwnd = new1[0]
        snap_window(claude_hwnd, "left", screen_w, screen_h)
        print(f"  Claude snapped to left half  (hwnd={claude_hwnd})")
    else:
        print("    Couldn't find Claude window to snap — opened anyway.")
        claude_hwnd = None

    before2 = existing_chrome_hwnds()

    # ── Window 2: Gemini + YouTube tab (right) ───────────────────────────────
    print("  Opening Gemini + YouTube...")
    subprocess.Popen(
        f'start {CHROME_EXE} --new-window "https://gemini.google.com"',
        shell=True
    )
    time.sleep(1.5)

    new2 = find_chrome_windows_after(before2, timeout=6)
    if new2:
        gemini_hwnd = new2[0]
        snap_window(gemini_hwnd, "right", screen_w, screen_h)
        print(f"   Gemini snapped to right half (hwnd={gemini_hwnd})")
    else:
        print("    Couldn't find Gemini window to snap — opened anyway.")
        gemini_hwnd = None


def calibrate():
    """
    Listen for 5 seconds and report the peak amplitude of claps.
    Suggests a good CLAP_THRESHOLD value based on what it hears.
    """
    chunk_size = int(SAMPLE_RATE * CHUNK_DURATION)
    peaks = []
    deadline = time.time() + 5

    print("\n  CALIBRATION MODE — clap several times in the next 5 seconds...\n")
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32', device=MIC_DEVICE) as stream:
        while time.time() < deadline:
            chunk, _ = stream.read(chunk_size)
            amp = np.max(np.abs(highpass_filter(chunk[:, 0])))
            bar = "█" * int(amp * 60)
            print(f"  Level: {amp:.4f}  {bar}", end="\r")
            if amp > 0.05:   # ignore silence
                peaks.append(amp)

    print("\n")
    if peaks:
        clap_peak = max(peaks)
        suggested = round(clap_peak * 0.5, 3)   # 50% of your loudest clap
        print(f"  Peak amplitude detected : {clap_peak:.4f}")
        print(f"   Suggested threshold  : {suggested}")
        print(f"\n  Open clap_launcher.py and set:\n    CLAP_THRESHOLD = {suggested}\n")
    else:
        print("   No sound detected at all — check your microphone!\n")


def highpass_filter(data, cutoff=1000, fs=SAMPLE_RATE, order=4):
    """High-pass filter to isolate sharp transients (clap frequencies)."""
    nyq = 0.5 * fs
    normal_cutoff = cutoff / nyq
    b, a = butter(order, normal_cutoff, btype='high', analog=False)
    return lfilter(b, a, data)


def is_clap(chunk):
    """Return True if the audio chunk looks like a clap."""
    filtered  = highpass_filter(chunk)
    amplitude = np.max(np.abs(filtered))
    if DEBUG_AMPLITUDE and amplitude > 0.01:
        print(f"  [debug] amplitude = {amplitude:.4f}  (threshold = {CLAP_THRESHOLD})")
    return amplitude > CLAP_THRESHOLD


def main():
    chunk_size   = int(SAMPLE_RATE * CHUNK_DURATION)
    last_clap_t  = 0.0
    last_trigger = 0.0

    if "--list-devices" in sys.argv:
        list_devices()
        return

    if "--calibrate" in sys.argv:
        calibrate()
        return

    print("  Clap Launcher is listening...")
    print(f"    Threshold : {CLAP_THRESHOLD}  |  Double-clap window: {MAX_CLAP_GAP}s")
    print("    Double-clap to open Claude, Gemini & YouTube.")
    print("    Tip: run with --calibrate to find your ideal threshold.\n")
    print("    (Ctrl+C to quit)\n")

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32', device=MIC_DEVICE) as stream:
        while True:
            audio_chunk, _ = stream.read(chunk_size)
            samples = audio_chunk[:, 0]
            now = time.time()

            # Skip if still in cooldown
            if now - last_trigger < COOLDOWN:
                continue

            if is_clap(samples):
                gap = now - last_clap_t

                if MIN_CLAP_GAP < gap < MAX_CLAP_GAP:
                    #  Second clap within the window — fire!
                    open_tabs()
                    break
                    last_trigger = now
                    last_clap_t  = 0.0   # reset
                    print(f" Cooling down for {COOLDOWN}s...\n")
                    time.sleep(COOLDOWN)
                else:
                    # First clap (or too fast/slow — reset)
                    print(f"   Clap 1 detected at {time.strftime('%H:%M:%S')} — clap again!")
                    last_clap_t = now

    print("\n Microphone is now OFF. Listening stopped.")
    input(" Press ENTER in this window to stop the music and close the launcher...\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nLauncher stopped.")
