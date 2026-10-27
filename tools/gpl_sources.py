"""Source code of the (L)GPL libraries in the builds, for the release page:
python tools/gpl_sources.py [out_dir]

PyAV ships FFmpeg and its codecs (x264, x265, LAME, GnuTLS...) as built by the
pyav-ffmpeg project. This downloads that project's build scripts and patches at
the tag the installed PyAV uses, plus every source archive its package list
names (checked against the SHA-256 sums in that list), into
ddp-streamer-third-party-sources-<tag>.tar. Upload that file with the release;
THIRD-PARTY-LICENSES.txt points to it.
"""
import hashlib, io, json, os, re, ssl, sys, tarfile, time, urllib.error, urllib.request

PYAV_REPO = "https://raw.githubusercontent.com/PyAV-Org/PyAV"
FFMPEG_REPO = "PyAV-Org/pyav-ffmpeg"

# GitHub archives whose sum changed although the tag didn't: pkg.py sum -> (sum now, why).
# Checked by hand; only exactly this new sum is accepted.
RENAMED = {
    "81839b16fdc401e20b73389d05f32a7656008f43bcb960feff290d6f08fc109e": (
        "506d8a6e855f59151a5407d848557f9ac10acad6ff61718c7b0b3441221bfe72",
        "basswood-io/lamer was renamed to bwlame on 2026-10-26; tag v3.101.0 is unchanged\n"
        "(commit 3ffd14f5531e0e5078e31eba4b2fb6ccc277b843), only the archive's top folder\n"
        "is now bwlame-3.101.0/ instead of lamer-3.101.0/, which changes the SHA-256."),
}


def get(url, checked=False):
    """Download url. checked=True: the caller verifies a SHA-256 sum, so a server
    with an incomplete certificate chain may be retried without the TLS check."""
    req = urllib.request.Request(url, headers={"User-Agent": "ddp-streamer-gpl-sources"})
    context = None
    for attempt in range(4):                    # servers sometimes drop a connection
        try:
            with urllib.request.urlopen(req, timeout=120, context=context) as r:
                return r.read()
        except urllib.error.URLError as e:
            if checked and context is None and isinstance(e.reason, ssl.SSLCertVerificationError):
                print(f"  (certificate check failed for {url.split('/')[2]}; the SHA-256 sum still checks the file)")
                context = ssl._create_unverified_context()
            elif attempt == 3:
                raise
            else:
                time.sleep(5 * (attempt + 1))
        except (ConnectionError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))


def pyav_ffmpeg_tag():
    """The pyav-ffmpeg release the installed PyAV was built with."""
    import av
    pin = json.loads(get(f"{PYAV_REPO}/v{av.__version__}/scripts/ffmpeg-latest.json"))
    return re.search(r"/download/([^/]+)/", pin["url"]).group(1)


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "dist"
    tag = pyav_ffmpeg_tag()
    print("pyav-ffmpeg", tag)
    pkg = get(f"https://raw.githubusercontent.com/{FFMPEG_REPO}/{tag}/scripts/pkg.py").decode()
    # Every Package(...) of every platform (a superset is fine): name, source_url, sha256
    sources = []
    for block in re.findall(r"Package\((.*?)\n\s*\)", pkg, re.S):
        field = {k: re.search(k + r'\s*=\s*"([^"]*)"', block) for k in ("name", "source_url", "sha256")}
        if all(field.values()):
            sources.append(tuple(field[k].group(1) for k in ("name", "source_url", "sha256")))
    if len(sources) != pkg.count("Package("):
        raise SystemExit(f"read {len(sources)} of {pkg.count('Package(')} packages from pkg.py")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"ddp-streamer-third-party-sources-{tag}.tar")
    with tarfile.open(out, "w") as tar:
        def add(name, data):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        add(f"pyav-ffmpeg-{tag}.tar.gz", get(f"https://github.com/{FFMPEG_REPO}/archive/refs/tags/{tag}.tar.gz"))
        notes = [f"Sources of FFmpeg and the libraries PyAV builds into it (pyav-ffmpeg {tag}).",
                 f"pyav-ffmpeg-{tag}.tar.gz has the build scripts and patches; the other files",
                 "are the original source archives it builds from (SHA-256 checked).", ""]
        for pkg_name, url, sha in sources:
            name = url.rstrip("/").split("/")[-1]
            if pkg_name.lower().replace("lib", "") not in name.lower():
                name = f"{pkg_name}-{name}"             # v1.17.0.tar.gz -> vpx-v1.17.0.tar.gz
            data = get(url, checked=True)
            got = hashlib.sha256(data).hexdigest()
            if got != sha and RENAMED.get(sha, ("",))[0] != got:
                raise SystemExit(f"checksum mismatch: {url}")
            add(f"sources/{name}", data)
            notes.append(f"{name}  {got}  {url}")
            if got != sha:
                notes.append(f"  (pkg.py lists {sha}:\n  " + RENAMED[sha][1].replace("\n", "\n  ") + ")")
            print(f"  {name}  {len(data) / 1e6:.1f} MB")
        add("README.txt", ("\n".join(notes) + "\n").encode())
    print(f"{out}: {os.path.getsize(out) / 1e6:.0f} MB, {len(sources)} source archives")


if __name__ == "__main__":
    main()
