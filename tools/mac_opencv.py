"""OpenCV for the Mac builds, compiled without FFmpeg: python tools/mac_opencv.py

opencv-python's Mac wheels bring their own FFmpeg plus ~60 Homebrew libraries
(SDL2, X11, GnuTLS, rubberband...). The app doesn't need any of them: video and
sound are decoded by PyAV. So this builds opencv-python-headless from its source
package with only the modules the app uses (core, imgproc, imgcodecs, videoio
with AVFoundation for cameras; features + calib only for the typing stubs) and OpenCV's own copies of the picture libraries
(zlib, libpng, libjpeg-turbo, libtiff, libwebp), for macOS 13 and up on both
Apple Silicon and Intel. The wheel is kept in build/opencv-wheel (the GitHub
workflow caches that folder), then installed and checked.
"""
import glob, os, platform, subprocess, sys

VERSION = "5.0.0.93"          # opencv-python-headless source package
WHEEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build", "opencv-wheel")
CMAKE_ARGS = [
    # (features + calib only so the typing-stub generator, which refers to them, works)
    "-DBUILD_LIST=core,imgproc,imgcodecs,videoio,features,calib,python3",
    "-DWITH_FFMPEG=OFF", "-DWITH_GSTREAMER=OFF", "-DWITH_AVFOUNDATION=ON",
    "-DBUILD_ZLIB=ON", "-DBUILD_PNG=ON", "-DBUILD_JPEG=ON", "-DBUILD_TIFF=ON", "-DBUILD_WEBP=ON",
    "-DWITH_OPENJPEG=OFF", "-DWITH_JASPER=OFF", "-DWITH_OPENEXR=OFF", "-DWITH_AVIF=OFF",
    "-DWITH_JPEGXL=OFF", "-DWITH_SPNG=OFF", "-DWITH_GDAL=OFF", "-DWITH_GDCM=OFF",
    "-DWITH_EIGEN=OFF", "-DWITH_PROTOBUF=OFF", "-DWITH_OPENCL=OFF", "-DWITH_QT=OFF", "-DWITH_VTK=OFF",
    "-DBUILD_TESTS=OFF", "-DBUILD_PERF_TESTS=OFF", "-DBUILD_EXAMPLES=OFF", "-DBUILD_DOCS=OFF",
]


def build_wheel():
    wheels = glob.glob(os.path.join(WHEEL_DIR, f"opencv_python_headless-{VERSION}-*.whl"))
    if wheels:
        return wheels[0]
    os.makedirs(WHEEL_DIR, exist_ok=True)
    env = dict(os.environ, ENABLE_HEADLESS="1", CMAKE_ARGS=" ".join(CMAKE_ARGS),
               MACOSX_DEPLOYMENT_TARGET="13.0", CMAKE_BUILD_PARALLEL_LEVEL=str(os.cpu_count() or 2))
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-binary", "opencv-python-headless",
                    "-w", WHEEL_DIR, f"opencv-python-headless=={VERSION}"], check=True, env=env)
    return glob.glob(os.path.join(WHEEL_DIR, f"opencv_python_headless-{VERSION}-*.whl"))[0]


def check():
    """Fails the build if FFmpeg or a non-system library got in, or a picture
    format or AVFoundation is missing."""
    code = r"""
import cv2, numpy as np, os, subprocess
info = cv2.getBuildInformation()
ff = [l for l in info.splitlines() if 'FFMPEG' in l]
assert all('NO' in l for l in ff), ff
used = '''resize cvtColor fillPoly circle VideoCapture GaussianBlur rectangle warpAffine line imdecode
    getTextSize rotate remap putText polylines morphologyEx imwrite imread getRotationMatrix2D flip dilate
    LUT CAP_PROP_FPS CAP_ANY FONT_HERSHEY_DUPLEX MORPH_GRADIENT BORDER_WRAP'''.split()
missing = [n for n in used if not hasattr(cv2, n)]
assert not missing, missing
names = [cv2.videoio_registry.getBackendName(b) for b in cv2.videoio_registry.getCameraBackends()]
assert 'AVFOUNDATION' in names, names
img = (np.random.rand(16, 16, 3) * 255).astype(np.uint8)
for ext in ('.png', '.jpg', '.bmp', '.tif', '.webp'):
    ok, buf = cv2.imencode(ext, img)
    assert ok and cv2.imdecode(buf, cv2.IMREAD_COLOR).shape == img.shape, ext
libs = [os.path.join(r, f) for r, _, fs in os.walk(os.path.dirname(cv2.__file__)) for f in fs
        if f.endswith(('.so', '.dylib'))]
for lib in libs:
    out = subprocess.run(['otool', '-L', lib], capture_output=True, text=True).stdout.splitlines()[1:]
    bad = [l.split()[0] for l in out if not l.split()[0].startswith(('/usr/lib/', '/System/'))]
    assert not bad, (lib, bad)
print('OpenCV', cv2.__version__, 'ok:', len(libs), 'library file(s), backends', names)
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def main():
    if platform.system() != "Darwin":
        sys.exit("Mac only")
    wheel = build_wheel()
    subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y",
                    "opencv-python", "opencv-python-headless"], check=True)
    # --no-deps: the app's numpy is already installed (and newer than some OpenCV pins allow)
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", wheel], check=True)
    check()


if __name__ == "__main__":
    main()
