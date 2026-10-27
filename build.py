"""Build the app with PyInstaller and zip it: python build.py

Makes dist/DDP Streamer/ (a folder build) and dist/DDP-Streamer-<system>.zip
(.tar.gz on Linux: keeps the library symlinks), with LICENSE.txt and
THIRD-PARTY-LICENSES.txt next to the program. On Windows also the installer
dist/DDP-Streamer-setup.exe (windows/installer.iss) when Inno Setup 6 is installed.
On a Mac: dist/DDP Streamer.app in dist/DDP-Streamer-mac-<apple-silicon|intel>.dmg
instead of a zip (one build per chip, made on a Mac with that chip).
On Linux: the .tar.gz has install.sh (menu entry + icon), and with appimagetool
installed (see linux/Dockerfile) also dist/DDP-Streamer-x86_64.AppImage.
Needs: pip install -r requirements.txt pyinstaller
"""
import glob
import importlib.metadata as md
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys

NAME = "DDP Streamer"
RELEASES = "https://github.com/bobko69/ddp-streamer/releases"
HERE = os.path.dirname(os.path.abspath(__file__))

# Python packages in the build: their license files come from the installed package
PACKAGES = [
    ("OpenCV (opencv-python)", "opencv-python", "https://github.com/opencv/opencv"),
    ("OpenCV (opencv-python-headless)", "opencv-python-headless", "https://github.com/opencv/opencv"),
    ("NumPy (with OpenBLAS)", "numpy", "https://github.com/numpy/numpy"),
    ("Pillow", "pillow", "https://github.com/python-pillow/Pillow"),
    ("PyAV", "av", "https://github.com/PyAV-Org/PyAV"),
    ("sounddevice", "sounddevice", "https://github.com/spatialaudio/python-sounddevice"),
    ("CFFI", "cffi", "https://github.com/python-cffi/cffi"),
    ("mss", "mss", "https://github.com/BoboTiG/python-mss"),
    ("certifi (Mozilla's CA certificates)", "certifi", "https://github.com/certifi/python-certifi"),
    ("Sun Valley ttk theme (sv-ttk)", "sv-ttk", "https://github.com/rdbende/Sun-Valley-ttk-theme"),
    ("PyInstaller bootloader", "pyinstaller", "https://github.com/pyinstaller/pyinstaller",
     "GPL-2.0-or-later with the PyInstaller bootloader exception"),
]
# Libraries inside those packages that ship without their license text: (name, license,
# source, files in licenses/[, only on these systems]). FFmpeg and the codecs are PyAV's
# build of them; PyAV's Linux build brings GnuTLS & co. On Linux the libraries PyInstaller
# copies from the build system (Tk, X11, PortAudio...) are found with dpkg instead.
LIBRARIES = [
    ("FFmpeg {ffmpeg}", "LGPL-3.0-or-later", "https://ffmpeg.org/download.html", ["LGPL-3.0.txt"]),
    ("x264", "GPL-2.0-or-later", "https://code.videolan.org/videolan/x264", ["GPL-2.0.txt"]),
    ("x265", "GPL-2.0-or-later", "https://bitbucket.org/multicoreware/x265_git", ["GPL-2.0.txt"]),
    ("LAME (the lamer fork)", "LGPL-2.0-or-later", "https://github.com/basswood-io/lamer", ["lame.txt"],
     "Windows Linux"),
    ("libiconv", "LGPL-2.1-or-later", "https://www.gnu.org/software/libiconv/", ["LGPL-2.1.txt"], "Windows"),
    ("dav1d", "BSD-2-Clause", "https://code.videolan.org/videolan/dav1d", ["dav1d.txt"]),
    ("libvpx", "BSD-3-Clause", "https://chromium.googlesource.com/webm/libvpx",
     ["libvpx.txt", "libvpx-PATENTS.txt"]),
    ("Opus", "BSD-3-Clause", "https://github.com/xiph/opus", ["opus.txt"]),
    ("libwebp", "BSD-3-Clause", "https://chromium.googlesource.com/webm/libwebp",
     ["libwebp.txt", "libwebp-PATENTS.txt"]),
    ("SVT-AV1", "BSD-3-Clause-Clear", "https://gitlab.com/AOMediaCodec/SVT-AV1",
     ["SVT-AV1.txt", "SVT-AV1-PATENTS.txt"]),
    ("VMAF", "BSD-2-Clause-Patent", "https://github.com/Netflix/vmaf", ["vmaf.txt"], "Windows Darwin"),
    ("Intel VPL", "MIT", "https://github.com/intel/libvpl", ["libvpl.txt"], "Windows Linux"),
    ("zlib", "Zlib", "https://zlib.net", ["zlib.txt"], "Windows"),
    ("GCC runtime (libgcc, libstdc++)", "GPL-3.0-or-later with the GCC Runtime Library Exception",
     "https://gcc.gnu.org", ["GCC-runtime-exception-3.1.txt"], "Windows Linux"),
    ("mingw-w64 winpthreads", "MIT", "https://www.mingw-w64.org", ["winpthreads.txt"], "Windows"),
    ("PortAudio", "MIT", "https://github.com/PortAudio/portaudio", ["portaudio.txt"], "Windows Darwin"),
    ("GnuTLS", "LGPL-2.1-or-later", "https://gnutls.org", ["LGPL-2.1.txt"], "Linux"),
    ("Nettle", "LGPL-3.0-or-later or GPL-2.0-or-later", "https://www.lysator.liu.se/~nisse/nettle/",
     ["LGPL-3.0.txt", "GPL-2.0.txt"], "Linux"),
    ("GMP", "LGPL-3.0-or-later or GPL-2.0-or-later", "https://gmplib.org",
     ["LGPL-3.0.txt", "GPL-2.0.txt"], "Linux"),
    ("libunistring", "LGPL-3.0-or-later or GPL-2.0-or-later", "https://www.gnu.org/software/libunistring/",
     ["LGPL-3.0.txt", "GPL-2.0.txt"], "Linux"),
    ("OpenCORE AMR", "Apache-2.0", "https://sourceforge.net/projects/opencore-amr/", ["Apache-2.0.txt"], "Linux"),
    ("ALSA library", "LGPL-2.1-or-later", "https://github.com/alsa-project/alsa-lib", ["LGPL-2.1.txt"], "Linux"),
    ("libxcb", "MIT", "https://gitlab.freedesktop.org/xorg/lib/libxcb", ["libxcb.txt"], "Linux"),
    ("libXau", "MIT", "https://gitlab.freedesktop.org/xorg/lib/libxau", ["libXau.txt"], "Linux"),
    ("libdrm", "MIT", "https://gitlab.freedesktop.org/mesa/drm", ["libdrm.txt"], "Linux"),
]


def _python_license():
    for p in (os.path.join(sys.base_prefix, "LICENSE.txt"),
              os.path.join(sys.base_prefix, "lib", f"python{sys.version_info[0]}.{sys.version_info[1]}",
                           "LICENSE.txt")):
        if os.path.isfile(p):
            return p
    return None


def _package_licenses(dist_name):
    """(version, license, [(file name, text)]) of an installed package, or None."""
    try:
        d = md.distribution(dist_name)
    except md.PackageNotFoundError:
        return None
    lic = d.metadata.get("License-Expression") or (d.metadata.get("License") or "").split("\n")[0]
    texts = []
    for f in d.files or []:
        n = os.path.basename(str(f)).upper()
        if f.suffix in (".py", ".pyc") or not any(k in n for k in ("LICENSE", "LICENCE", "COPYING", "NOTICE")):
            continue
        try:
            texts.append((str(f), f.locate().read_text(encoding="utf-8", errors="replace")))
        except OSError:
            pass
    return d.version, lic, texts


def _system_packages(app):
    """Linux: the distribution packages of the libraries PyInstaller copied from the
    build system -> [(row, copyright file title, text)]. Libraries from pip wheels
    have hashed names (libfoo-1a2b3c4d.so) and dpkg doesn't know them."""
    lib_dir = os.path.join(app, "_internal")
    if not shutil.which("dpkg") or not os.path.isdir(lib_dir):
        return []
    names = {f for f in os.listdir(lib_dir) if ".so" in f and not os.path.islink(os.path.join(lib_dir, f))}
    out = subprocess.run(["dpkg", "-S"] + sorted(names), capture_output=True, text=True).stdout
    pkgs = set()
    for line in out.splitlines():
        pkg, _, path = line.partition(": ")
        if os.path.basename(path.strip()) in names:
            pkgs.update(p.strip().split(":")[0] for p in pkg.split(","))
    osr = {}
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            osr = dict(l.strip().split("=", 1) for l in f if "=" in l)
    except OSError:
        pass
    distro, codename = osr.get("ID", "").strip('"'), osr.get("VERSION_CODENAME", "").strip('"')
    site = {"ubuntu": "https://packages.ubuntu.com", "debian": "https://packages.debian.org"}.get(distro)
    result = []
    for pkg in sorted(pkgs):
        ver = subprocess.run(["dpkg-query", "-W", "-f", "${Version}", pkg], capture_output=True, text=True).stdout
        try:
            with open(f"/usr/share/doc/{pkg}/copyright", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            text = None
        url = f"{site}/{codename}/{pkg}" if site else f"{distro} package {pkg}"
        result.append(((f"{pkg} {ver} ({distro} {codename} package)", "see its copyright file below", url),
                       f"{pkg}: /usr/share/doc/{pkg}/copyright", text))
    return result


def third_party(app):
    """LICENSE.txt and THIRD-PARTY-LICENSES.txt next to the program."""
    import av
    shutil.copy("LICENSE", os.path.join(app, "LICENSE.txt"))
    rows, texts = [], []
    py = _python_license()
    rows.append((f"Python {platform.python_version()} (with OpenSSL, Tcl/Tk, libffi, bzip2, zstd...)",
                 "PSF-2.0 and others", "https://www.python.org/downloads/source/"))
    if py:
        texts.append(("Python and the libraries it bundles", open(py, encoding="utf-8", errors="replace").read()))
    seen = set()
    for title, dist, url, *lic_name in PACKAGES:
        got = _package_licenses(dist)
        if got is None:
            continue
        ver, lic, files = got
        rows.append((f"{title} {ver}", lic_name[0] if lic_name else lic, url))
        for fname, text in files:
            if text.strip() not in seen:          # packages often carry the same file twice
                seen.add(text.strip())
                texts.append((f"{title}: {fname}", text))
    ffmpeg = getattr(av, "ffmpeg_version_info", "")
    done = set()
    for title, lic, url, files, *only in LIBRARIES:
        if only and platform.system() not in only[0].split():
            continue
        rows.append((title.format(ffmpeg=ffmpeg), lic, url))
        for f in files:
            if f not in done:
                done.add(f)
                texts.append((f, open(os.path.join("licenses", f), encoding="utf-8").read()))
    rows.append(("Fonts (Archivo Black, Anton, Inter, Noto Sans, Barlow, JetBrains Mono, Comic Neue, "
                 "Press Start 2P, DejaVu Sans)", "SIL Open Font License 1.1, DejaVu Fonts License",
                 "_internal/fonts/licenses"))
    if platform.system() == "Windows":
        rows.append(("Microsoft Visual C++ runtime", "Microsoft redistributable", "https://visualstudio.microsoft.com"))
    for row, title, text in _system_packages(app):
        rows.append(row)
        if text and text.strip() not in seen:
            seen.add(text.strip())
            texts.append((title, text))

    out = [f"{NAME}: third-party software",
           "=" * 40, "",
           f"{NAME} is free software under the GNU General Public License v3 (LICENSE.txt).",
           "It includes the software below, each under its own license. The full",
           "license texts follow the list. The source code of every part is available",
           "from the address given for it. The exact sources of FFmpeg and the",
           "libraries built into it (x264, x265, LAME, GnuTLS...) are also on the",
           "release page of this version, as ddp-streamer-third-party-sources-*.tar:",
           f"{RELEASES}", ""]
    for name, lic, url in rows:
        out += [f"- {name}", f"    License: {lic}", f"    Source:  {url}"]
    for title, text in texts:
        out += ["", "", "-" * 78, title, "-" * 78, "", text.strip()]
    with open(os.path.join(app, "THIRD-PARTY-LICENSES.txt"), "w", encoding="utf-8",
              newline="\r\n" if platform.system() == "Windows" else "\n") as f:
        f.write("\n".join(out) + "\n")
    print(f"THIRD-PARTY-LICENSES.txt: {len(rows)} parts, {len(texts)} license texts")


def app_version():
    with open("ddp_streamer.py", encoding="utf-8") as f:
        return re.search(r'^APP_VERSION = "([^"]+)"', f.read(), re.M).group(1)


def installer():
    """Windows: dist/DDP-Streamer-setup.exe with Inno Setup, or None without it."""
    places = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),   # per-user install
              os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", "")]
    iscc = shutil.which("ISCC") or next((p for p in (os.path.join(d, "Inno Setup 6", "ISCC.exe") for d in places)
                                         if os.path.isfile(p)), None)
    if not iscc:
        print("Inno Setup 6 not found: no installer (winget install JRSoftware.InnoSetup)")
        return None
    subprocess.run([iscc, "/Q", f"/DAppVersion={app_version()}", os.path.join("windows", "installer.iss")],
                   check=True)
    return os.path.join("dist", "DDP-Streamer-setup.exe")


# Mac: what the app may ask for, and no App Nap (it would slow the stream down
# while the window is in the background or minimized)
MAC_PLIST = {
    "CFBundleIdentifier": "com.bong69.ddpstreamer",
    "NSCameraUsageDescription": "DDP Streamer sends camera pictures to your LED display.",
    "NSLocalNetworkUsageDescription": "DDP Streamer finds LED displays on your network and streams to them.",
    "NSAppSleepDisabled": True,
    "NSHighResolutionCapable": True,
    # OpenCV is built for macOS 13 (tools/mac_opencv.py); Apple Silicon stays at 14, as tested
    "LSMinimumSystemVersion": "14.0" if platform.machine() == "arm64" else "13.0",
}


def mac_finish(bundle):
    """Info.plist entries, then sign again (ad hoc: changing the bundle breaks
    PyInstaller's signature, and Apple Silicon won't start an unsigned app)."""
    plist = os.path.join(bundle, "Contents", "Info.plist")
    with open(plist, "rb") as f:
        info = plistlib.load(f)
    info.update(MAC_PLIST, CFBundleShortVersionString=app_version(), CFBundleVersion=app_version())
    with open(plist, "wb") as f:
        plistlib.dump(info, f)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", bundle], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", bundle], check=True)


def mac_dmg(bundle):
    """dist/DDP-Streamer-mac.dmg: the app, an Applications shortcut to drag it
    to, and the license files."""
    stage = os.path.join("dist", "dmg")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    shutil.move(bundle, stage)
    os.symlink("/Applications", os.path.join(stage, "Applications"))
    third_party(stage)
    chip = "apple-silicon" if platform.machine() == "arm64" else "intel"
    dmg = os.path.join("dist", f"DDP-Streamer-mac-{chip}.dmg")
    if os.path.exists(dmg):
        os.remove(dmg)
    subprocess.run(["hdiutil", "create", "-volname", NAME, "-srcfolder", stage, "-ov",
                    "-format", "UDZO", dmg], check=True)
    return dmg


def linux_extras(app):
    """Linux: menu entry, 256 px icon and install.sh in the program folder."""
    from PIL import Image
    internal = os.path.join(app, "_internal")
    Image.open("bong69.ico").ico.getimage((256, 256)).save(os.path.join(internal, "ddp-streamer.png"))
    shutil.copy(os.path.join("linux", "ddp-streamer.desktop"), internal)
    shutil.copy(os.path.join("linux", "install.sh"), app)
    os.chmod(os.path.join(app, "install.sh"), 0o755)


def appimage(app):
    """dist/DDP-Streamer-x86_64.AppImage, or None without appimagetool.
    APPIMAGE_RUNTIME (set in linux/Dockerfile) = the runtime file, so the build
    doesn't download it; that runtime needs no libfuse2 on the user's PC."""
    tool = shutil.which("appimagetool")
    if not tool:
        print("appimagetool not found: no AppImage (see linux/Dockerfile)")
        return None
    appdir = os.path.join("dist", "AppDir")
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(app, os.path.join(appdir, "usr", "lib", "ddp-streamer"), symlinks=True)
    with open(os.path.join(appdir, "AppRun"), "w", newline="\n") as f:
        f.write('#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
                'exec "$HERE/usr/lib/ddp-streamer/DDP Streamer" "$@"\n')
    os.chmod(os.path.join(appdir, "AppRun"), 0o755)
    shutil.copy(os.path.join("linux", "ddp-streamer.desktop"), appdir)
    shutil.copy(os.path.join(app, "_internal", "ddp-streamer.png"), appdir)
    os.symlink("ddp-streamer.png", os.path.join(appdir, ".DirIcon"))
    out = os.path.join("dist", "DDP-Streamer-x86_64.AppImage")
    cmd = [tool, "--no-appstream", appdir, out]
    if os.environ.get("APPIMAGE_RUNTIME"):
        cmd[1:1] = ["--runtime-file", os.environ["APPIMAGE_RUNTIME"]]
    subprocess.run(cmd, check=True, env=dict(os.environ, ARCH="x86_64"))
    shutil.rmtree(appdir)
    return out


def main():
    os.chdir(HERE)
    if platform.system() == "Darwin":
        # OpenCV without its own FFmpeg and Homebrew libraries (see the tool)
        subprocess.run([sys.executable, os.path.join("tools", "mac_opencv.py")], check=True)
    data = ["bong69.ico", "bong69.png", "fonts", "scripts", "docs"]
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
           "--name", NAME, "--icon", "bong69.ico", "--collect-data", "sv_ttk",
           "--exclude-module", "PIL._avif"]            # AVIF pictures: never opened with Pillow
    for d in data:
        cmd += ["--add-data", f"{d}{os.pathsep}{'.' if os.path.isfile(d) else d}"]
    subprocess.run(cmd + ["ddp_streamer.py"], check=True)

    app = os.path.join("dist", NAME + (".app" if platform.system() == "Darwin" else ""))
    # OpenCV's own FFmpeg: video and sound are decoded by PyAV's FFmpeg instead
    for f in glob.glob(os.path.join(app, "**", "opencv_videoio_ffmpeg*"), recursive=True):
        os.remove(f)
        print("removed", f)
    # PortAudio copies for other machines (and the ASIO ones, under Steinberg's own license)
    keep = {"Windows": "libportaudio64bit.dll", "Darwin": "libportaudio.dylib"}.get(platform.system())
    if platform.machine().lower() in ("arm64", "aarch64") and platform.system() == "Windows":
        keep = "libportaudioarm64.dll"
    for f in glob.glob(os.path.join(app, "**", "portaudio-binaries", "libportaudio*"), recursive=True):
        if os.path.basename(f) != keep:
            os.remove(f)
            print("removed", f)
    size = sum(os.path.getsize(p) for r, _, fs in os.walk(app) for p in (os.path.join(r, f) for f in fs)
               if not os.path.islink(p))
    if platform.system() == "Darwin":
        mac_finish(app)
        dmg = mac_dmg(app)
        print(f"{app}: {size / 1e6:.0f} MB, {dmg}: {os.path.getsize(dmg) / 1e6:.0f} MB")
        return
    third_party(app)
    if platform.system() == "Linux":
        linux_extras(app)

    system = {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(platform.system(), "other")
    # Linux: tar keeps the library symlinks (zip would store each of those files twice)
    archive = shutil.make_archive(os.path.join("dist", f"DDP-Streamer-{system}"),
                                  "gztar" if system == "linux" else "zip", "dist", NAME)
    print(f"{app}: {size / 1e6:.0f} MB, {archive}: {os.path.getsize(archive) / 1e6:.0f} MB")
    if system == "windows":
        setup = installer()
        if setup:
            print(f"{setup}: {os.path.getsize(setup) / 1e6:.0f} MB")
    if system == "linux":
        image = appimage(app)
        if image:
            print(f"{image}: {os.path.getsize(image) / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
