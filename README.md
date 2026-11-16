# SwiftGet Download Manager

A Windows download accelerator written in Python with Tkinter, using only the standard library.

![icon](swiftget/icon.png)

## Features
- **Multi-connection downloads:** each file is split into up to 32 segments that download in parallel using HTTP Range requests.
- **Dynamic segmentation:** when a connection finishes early, it takes over half of the largest remaining segment, so every connection stays busy until the end.
- **Pause and resume:** progress is saved to disk, so downloads survive app restarts and reboots.
- **Automatic retry:** a dropped connection is retried with exponential back-off.
- **Categories:** downloads are filed as Compressed, Documents, Music, Programs, Video, Images or General. They can go into sub-folders automatically.
- **Download queue:** you choose how many downloads run at once. You can start or stop the queue, or pause and resume everything.
- **Global speed limiter**
- **Clipboard monitoring:** copying a link to a downloadable file opens the download dialog.
- **Batch download:** pastes a list of links from the clipboard.
- **Progress window:** shows speed, time left, resume capability and a live map of every connection.
- **Main list:** sortable columns, filters (Unfinished, Finished, Queued), a right-click menu, Open, Open folder, Redownload and Properties.

## Browser integration
The `extension/` folder holds a browser extension for Chrome, Edge, Brave, Opera and Vivaldi (Manifest V3). It talks to SwiftGet through a local server on `127.0.0.1:9614`.

- **Automatic capture:** when you click a file in the browser (zip, exe, mp4, pdf, iso and so on), the browser's own download stops and SwiftGet takes it over. SwiftGet receives the browser's **cookies, referrer and user agent**, so downloads that need a login still work.
- **Right-click menu:** "Download with SwiftGet" on links, videos, audio and images, and "Download all links with SwiftGet" on pages and selections. The links open in a list where you tick the ones you want.
- **Toolbar popup:** shows the connection status. You can turn capture on or off, capture every file type, ignore small files, skip certain extensions, or grab all links on the page.
- **Fallback:** if SwiftGet isn't running, the browser downloads the file normally.
- **Security:** the local server listens on the loopback address only, needs a custom header, and accepts only browser-extension origins. Ordinary websites can't send it downloads.

**Install it in Chrome or Edge:** in SwiftGet, open **Browser** on the toolbar and click **Copy extension folder**. Open `chrome://extensions` or `edge://extensions`, turn on **Developer mode**, click **Load unpacked** and choose that folder.
**Firefox:** go to `about:debugging` → *This Firefox* → *Load Temporary Add-on* and choose `swiftget-firefox.zip`. Firefox removes unsigned add-ons when it restarts. To keep it installed permanently, the add-on must be signed through addons.mozilla.org.

## Get the .exe
Every push runs the **Build SwiftGet.exe** GitHub Actions workflow on `windows-latest`.
Open the **Actions** tab, choose the latest run, and download the **SwiftGet-windows** artifact.
Each build is also published as a GitHub Release. The newest exe is always at
https://github.com/nayim12/claude/releases/latest/download/SwiftGet.exe

## Build locally (Windows)
```bat
cd swiftget
pip install pyinstaller
pyinstaller --onefile --windowed --name SwiftGet --icon icon.ico --add-data "icon.ico;." --add-data "icon.png;." app.py
dist\SwiftGet.exe
```

## Run from source
```bash
cd swiftget
python app.py
```
Settings and the download list are stored in `%APPDATA%\SwiftGet`.
Files are saved to `Downloads\SwiftGet\<Category>` by default.
