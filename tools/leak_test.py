"""Leak test: open, play a little of and close many videos the way the player
does (Source + AudioTrack), and check the process's threads and memory stay
flat. Catches decoders that are not freed (2026-10-23: ~17 FFmpeg threads and
~70 MB were left behind per video, ~5,000 threads after 2.7 days).

Usage: python tools/leak_test.py [videos...] [--items N]
With no videos, a short test clip with sound is made in a temp folder.
Exit code 0 = pass, 1 = threads or memory grew.
"""
import argparse, os, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import numpy as np
import ddp_streamer as D

av = D.av
THREAD_SLACK = 4        # threads may come and go; more than this after warm-up = leak
RAM_SLACK_MB = 60


def thread_count():
    """Native threads of this process (FFmpeg's included)."""
    if sys.platform == "linux":
        return len(os.listdir("/proc/self/task"))
    if os.name == "nt":
        import ctypes, ctypes.wintypes as wt

        class TE(ctypes.Structure):
            _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("tid", wt.DWORD), ("pid", wt.DWORD),
                        ("b", wt.LONG), ("d", wt.LONG), ("f", wt.DWORD)]
        k32 = ctypes.WinDLL("kernel32")
        k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
        snap = k32.CreateToolhelp32Snapshot(4, 0)
        te = TE(); te.dwSize = ctypes.sizeof(te)
        me, n = k32.GetCurrentProcessId(), 0
        ok = k32.Thread32First(snap, ctypes.byref(te))
        while ok:
            n += te.pid == me
            ok = k32.Thread32Next(snap, ctypes.byref(te))
        k32.CloseHandle(snap)
        return n
    try:
        import psutil
        return psutil.Process().num_threads()
    except ImportError:
        return None


def ram_mb():
    if os.name == "nt":
        import ctypes, ctypes.wintypes as wt

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD)] + \
                       [(n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize",
                        "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                        "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        pmc = PMC(); pmc.cb = ctypes.sizeof(pmc)
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wt.HANDLE
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.WorkingSetSize / 2**20
    if sys.platform == "linux":
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20
    try:
        import psutil
        return psutil.Process().memory_info().rss / 2**20
    except ImportError:
        return None


def make_clip(path, secs=2):
    """A small H.264 + AAC clip, so the test needs no media files."""
    with av.open(path, "w") as out:
        v = out.add_stream("h264", rate=30)
        v.width, v.height, v.pix_fmt = 320, 180, "yuv420p"
        a = out.add_stream("aac", rate=48000)
        a.layout = "stereo"
        for i in range(secs * 30):
            img = np.zeros((180, 320, 3), np.uint8)
            img[:, :, 0] = (i * 8) % 256
            for p in v.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                out.mux(p)
        n = 1024
        for i in range(secs * 48000 // n):
            t = (np.arange(n) + i * n) / 48000
            s = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            fr = av.AudioFrame.from_ndarray(np.vstack([s, s]), format="fltp", layout="stereo")
            fr.sample_rate, fr.pts = 48000, i * n
            for p in a.encode(fr):
                out.mux(p)
        for st in (v, a):
            for p in st.encode():
                out.mux(p)


def one_item(path):
    """What the player does for one playlist item: picture + sound, then close."""
    src = D.Source(path, (256, 128))
    for k in range(15):
        src.frame_at(k / 30)
    track = None
    try:
        track = D.AudioTrack(path)
        time.sleep(0.2)
    except Exception:
        pass                    # no sound in this file
    src.close()
    if track:
        track.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="*")
    ap.add_argument("--items", type=int, default=40)
    args = ap.parse_args()
    if av is None:
        print("PyAV is not installed: nothing to test")
        return 0
    videos = args.videos
    if not videos:
        tmp = tempfile.mkdtemp(prefix="leaktest_")
        videos = [os.path.join(tmp, "clip.mp4")]
        make_clip(videos[0])
    for _ in range(3):                  # warm-up: libraries start their thread pools once
        one_item(videos[0])
    time.sleep(1.0)
    t0, m0 = thread_count(), ram_mb()
    for i in range(args.items):
        one_item(videos[i % len(videos)])
    time.sleep(1.5)                     # audio decode threads finish and free their decoder
    t1, m1 = thread_count(), ram_mb()
    print(f"{args.items} items: threads {t0} -> {t1}, RAM {m0:.0f} -> {m1:.0f} MB")
    bad = []
    if t0 is not None and t1 - t0 > THREAD_SLACK:
        bad.append(f"threads grew by {t1 - t0}")
    if m0 is not None and m1 - m0 > RAM_SLACK_MB:
        bad.append(f"RAM grew by {m1 - m0:.0f} MB")
    print("FAIL: " + ", ".join(bad) if bad else "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
