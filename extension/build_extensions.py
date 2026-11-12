"""Package the extension for Chromium browsers and Firefox.

Output (in ./dist):
  swiftget-chromium/       unpacked extension for Chrome / Edge / Brave / Opera
  swiftget-chromium.zip
  swiftget-firefox.zip     (Firefox uses background "scripts" instead of a service worker)
"""
import json
import os
import shutil
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
FILES = ["manifest.json", "background.js", "popup.html", "popup.js", "icons"]


def copy_to(dst):
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst)
    for f in FILES:
        src = os.path.join(HERE, f)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dst, f))
        else:
            shutil.copy2(src, dst)


def zip_dir(src, out):
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(src):
            for f in files:
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, src))


def main():
    os.makedirs(DIST, exist_ok=True)
    chromium = os.path.join(DIST, "swiftget-chromium")
    copy_to(chromium)
    zip_dir(chromium, os.path.join(DIST, "swiftget-chromium.zip"))

    firefox = os.path.join(DIST, "swiftget-firefox")
    copy_to(firefox)
    mpath = os.path.join(firefox, "manifest.json")
    with open(mpath) as f:
        m = json.load(f)
    m["background"] = {"scripts": ["background.js"]}
    m["browser_specific_settings"] = {"gecko": {"id": "swiftget@swiftget.local",
                                                "strict_min_version": "115.0"}}
    with open(mpath, "w") as f:
        json.dump(m, f, indent=2)
    zip_dir(firefox, os.path.join(DIST, "swiftget-firefox.zip"))
    shutil.rmtree(firefox)
    print("built", os.listdir(DIST))


if __name__ == "__main__":
    main()
