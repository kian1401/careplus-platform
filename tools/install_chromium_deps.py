#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install_chromium_deps.py — نصب کتابخانه‌های سیستمی لازم برای Chromium در محیط بدون root.

روش: دانلود بسته‌های .deb از مخزن (apt-get download که نیازی به root ندارد، یا
دانلود مستقیم از فهرست pool) و استخراج فایل‌های .so به یک پوشه محلی؛ سپس ساخت
LD_LIBRARY_PATH. خروجی: مسیر پوشه کتابخانه‌ها (برای استفاده در build_pdf.py).

اجرا:  python3 tools/install_chromium_deps.py [--browser-headless-shell]
"""
import os, re, subprocess, sys, glob, shutil, tarfile, io, urllib.request

HOME = os.path.expanduser("~")
LIBDIR = os.path.join(HOME, ".cache", "chromium-libs")
DEBDIR = os.path.join(HOME, ".cache", "chromium-debs")
MIRROR = "http://deb.debian.org/debian"

# soname -> بسته‌ای که معمولاً آن را فراهم می‌کند
SONAME_TO_PKG = {
    "libnspr4.so": "libnspr4", "libnss3.so": "libnss3", "libnssutil3.so": "libnss3",
    "libsmime3.so": "libnss3", "libssl3.so": "libnss3", "libsoftokn3.so": "libnss3",
    "libatk-1.0.so.0": "libatk1.0-0", "libatk-bridge-2.0.so.0": "libatk-bridge2.0-0",
    "libatspi.so.0": "libatspi2.0-0", "libasound.so.2": "libasound2",
    "libxdamage.so.1": "libxdamage1", "libxkbcommon.so.0": "libxkbcommon0",
    "libcairo.so.2": "libcairo2", "libcairo-gobject.so.2": "libcairo-gobject2",
    "libpango-1.0.so.0": "libpango-1.0-0", "libpangocairo-1.0.so.0": "libpangocairo-1.0-0",
    "libpangoft2-1.0.so.0": "libpangoft2-1.0-0", "libharfbuzz.so.0": "libharfbuzz0b",
    "libglib-2.0.so.0": "libglib2.0-0", "libgobject-2.0.so.0": "libglib2.0-0",
    "libgio-2.0.so.0": "libglib2.0-0", "libgmodule-2.0.so.0": "libglib2.0-0",
    "libdbus-1.so.3": "libdbus-1-3", "libxcomposite.so.1": "libxcomposite1",
    "libxfixes.so.3": "libxfixes3", "libxrandr.so.2": "libxrandr2",
    "libxext.so.6": "libxext6", "libx11.so.6": "libx11-6", "libxcb.so.1": "libxcb1",
    "libgbm.so.1": "libgbm1", "libcups.so.2": "libcups2", "libexpat.so.1": "libexpat1",
    "libudev.so.1": "libudev1", "libsystemd.so.0": "libsystemd0", "libffi.so.8": "libffi8",
    "libmount.so.1": "libmount1", "libcap.so.2": "libcap2", "libgcrypt.so.20": "libgcrypt20",
    "libgpg-error.so.0": "libgpg-error0", "libgraphite2.so.3": "libgraphite2-3",
    "libthai.so.0": "libthai0", "libdatrie.so.1": "libdatrie1", "libfribidi.so.0": "libfribidi0",
    "libpixman-1.so.0": "libpixman-1-0", "libpng16.so.16": "libpng16-16",
    "libfontconfig.so.1": "libfontconfig1", "libfreetype.so.6": "libfreetype6",
    "libxrender.so.1": "libxrender1", "libxi.so.6": "libxi6", "libsm.so.6": "libsm6",
    "libice.so.6": "libice6", "libwayland-client.so.0": "libwayland-client0",
    "libwayland-server.so.0": "libwayland-server0", "libwayland-cursor.so.0": "libwayland-cursor0",
    "libwayland-egl.so.1": "libwayland-egl1", "libegl.so.1": "libegl1", "libgles2.so": "libgles2",
    "libglvnd.so.0": "libglvnd0", "libGLX.so.0": "libglx0", "libOpenGL.so.0": "libopengl0",
    "libxcb-render.so.0": "libxcb-render0", "libxcb-shm.so.0": "libxcb-shm0",
    "libxcb-randr.so.0": "libxcb-randr0", "libxcb-xfixes.so.0": "libxcb-xfixes0",
    "libxcb-shape.so.0": "libxcb-shape0", "libxcb-sync.so.1": "libxcb-sync1",
    "libxcb-present.so.0": "libxcb-present0", "libxcb-dri3.so.0": "libxcb-dri3-0",
    "libxshmfence.so.1": "libxshmfence1", "libdrm.so.2": "libdrm2", "libblkid.so.1": "libblkid1",
    "libselinux.so.1": "libselinux1", "libsepol.so.2": "libsepol2",
    "libpcre2-8.so.0": "libpcre2-8-0", "libzstd.so.1": "libzstd1",
    "libbz2.so.1.0": "libbz2-1.0", "liblzma.so.5": "liblzma5", "libxml2.so.2": "libxml2",
    "libuuid.so.1": "libuuid1", "libtinfo.so.6": "libtinfo6", "libxau.so.6": "libxau6",
    "libxdmcp.so.6": "libxdmcp6", "libbsd.so.0": "libbsd0", "libmd.so.0": "libmd0",
    "libXinerama.so.1": "libxinerama1", "libXcursor.so.1": "libxcursor1",
}

# نام‌های جایگزین بسته در نسخه‌های جدید دبیان (t64)
PKG_ALIASES = {"libatk1.0-0": ["libatk1.0-0", "libatk1.0-0t64", "libatk1.0-0t64"],
               "libasound2": ["libasound2", "libasound2t64"],
               "libcups2": ["libcups2", "libcups2t64"],
               "libglib2.0-0": ["libglib2.0-0", "libglib2.0-0t64"],
               "libpango-1.0-0": ["libpango-1.0-0", "libpango-1.0-0t64"],
               "libpangoft2-1.0-0": ["libpangoft2-1.0-0", "libpangoft2-1.0-0t64"],
               "libpangocairo-1.0-0": ["libpangocairo-1.0-0", "libpangocairo-1.0-0t64"]}


def run(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def pkg_dir_letter(pkg):
    return pkg[0]


def pool_candidates(pkg):
    """فهرست بسته‌های amd64 موجود در pool برای یک نام بسته."""
    url = f"{MIRROR}/pool/main/{pkg_dir_letter(pkg)}/{pkg}/"
    try:
        html = urllib.request.urlopen(url, timeout=25).read().decode("utf-8", "ignore")
    except Exception:
        return []
    files = re.findall(r'href="([^"]+_amd64\.deb)"', html)

    def vkey(f):
        m = re.search(r'_([^_]+)_amd64\.deb$', f)
        return m.group(1) if m else f
    return [url + f for f in sorted(files, key=vkey)[::-1]]


def download_pkg(pkg):
    """ابتدا apt-get download، سپس دانلود مستقیم از pool."""
    os.makedirs(DEBDIR, exist_ok=True)
    if glob.glob(os.path.join(DEBDIR, f"{pkg}_*.deb")):
        return True
    r = run(f"cd {DEBDIR} && apt-get download {pkg}")
    if glob.glob(os.path.join(DEBDIR, f"{pkg}_*.deb")):
        return True
    for u in pool_candidates(pkg)[:2]:
        name = os.path.basename(u).replace("%3a", ":")
        try:
            data = urllib.request.urlopen(u, timeout=40).read()
            open(os.path.join(DEBDIR, name.replace(":", "%3a")), "wb").write(data)
            return True
        except Exception:
            continue
    return False


def extract_deb(path, dest):
    """استخراج .deb بدون نیاز به dpkg-deb (تحلیل ar + tar)."""
    with open(path, "rb") as f:
        magic = f.read(8)
        if not magic.startswith(b"!<arch>"):
            return False
        data_objs = []
        while True:
            hdr = f.read(60)
            if len(hdr) < 60:
                break
            name = hdr[0:16].decode().strip()
            size = int(hdr[48:58].decode().strip())
            body = f.read(size)
            if size % 2:
                f.read(1)
            if name.startswith("data.tar"):
                data_objs.append(body)
    if not data_objs:
        return False
    buf = io.BytesIO(data_objs[-1])
    try:
        tf = tarfile.open(fileobj=buf)          # zstd/gz/xz پشتیبانی خودکار
    except tarfile.ReadError:
        return False
    for m in tf.getmembers():
        if not m.isfile():
            continue
        p = m.name
        if p.startswith("./"):
            p = p[2:]
        if p.startswith("usr/lib/") or p.startswith("lib/"):
            base = os.path.basename(p)
            if ".so" in base:
                target = os.path.join(dest, base)
                if not os.path.exists(target):
                    with open(target, "wb") as out:
                        out.write(tf.extractfile(m).read())
                # پیوندهای نمادین نسخه‌دار
                m2 = re.match(r"(.*\.so(?:\.\d+)*)$", base)
                if m2:
                    real = os.path.join(dest, base)
                    for alias in {base.rsplit(".so", 1)[0] + ".so",
                                  base.rsplit(".so", 1)[0] + ".so." + base.rsplit(".so.", 1)[-1].split(".")[0]}:
                        link = os.path.join(dest, alias)
                        if alias != base and not os.path.exists(link):
                            try:
                                os.symlink(real, link)
                            except OSError:
                                pass
    return True


def missing_libs(binary, libdirs):
    env = "LD_LIBRARY_PATH=" + ":".join(libdirs)
    r = run(f"{env} ldd {binary} 2>/dev/null | grep 'not found'")
    return sorted({l.split()[0] for l in r.stdout.strip().splitlines() if l.strip()})


def latest_chrome():
    pats = [os.path.join(HOME, ".cache", "ms-playwright", "chromium_headless_shell-*",
                         "chrome-headless-shell-linux64", "chrome-headless-shell"),
            os.path.join(HOME, ".cache", "ms-playwright", "chromium-*",
                         "chrome-linux", "chrome")]
    for p in pats:
        hits = sorted(glob.glob(p))
        if hits:
            return hits[-1]
    raise SystemExit("Chromium یافت نشد. ابتدا: python3 -m playwright install chromium")


def main():
    os.makedirs(LIBDIR, exist_ok=True)
    binary = latest_chrome()
    print("مرورگر:", binary)
    queue = ["libnspr4", "libnss3", "libatk1.0-0", "libatk-bridge2.0-0", "libatspi2.0-0",
             "libasound2", "libxdamage1", "libxkbcommon0"]
    done_pkgs, done_libs = set(), set()
    for round_no in range(1, 9):
        while queue:
            pkg = queue.pop(0)
            if pkg in done_pkgs:
                continue
            if download_pkg(pkg):
                for f in glob.glob(os.path.join(DEBDIR, f"{pkg}_*.deb")):
                    extract_deb(f, LIBDIR)
                done_pkgs.add(pkg)
            else:
                for alt in PKG_ALIASES.get(pkg, []):
                    if alt in done_pkgs:
                        continue
                    if download_pkg(alt):
                        for f in glob.glob(os.path.join(DEBDIR, f"{alt}_*.deb")):
                            extract_deb(f, LIBDIR)
                        done_pkgs.add(alt)
                        break
        miss = missing_libs(binary, [LIBDIR])
        print(f"[دور {round_no}] کتابخانه‌های ناموجود: {miss or '— هیچ —'}")
        new = [m for m in miss if m not in done_libs]
        if not miss:
            break
        for m in new:
            done_libs.add(m)
            for pkg in [SONAME_TO_PKG.get(m)] if SONAME_TO_PKG.get(m) else []:
                queue.append(pkg)
        if not queue:
            # تلاش برای یافتن بسته از روی نام soname
            for m in new:
                guess = re.sub(r"\.so.*$", "", m).lower().replace("lib", "lib", 1)
                queue.append(guess)
    print("\nکتابخانه‌ها در:", LIBDIR)
    print("تعداد فایل:", len(os.listdir(LIBDIR)))
    print("LD_LIBRARY_PATH=" + LIBDIR)
    return 0 if not missing_libs(binary, [LIBDIR]) else 1


if __name__ == "__main__":
    sys.exit(main())
