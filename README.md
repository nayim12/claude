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

## Get the .exe
Every push runs the **Build SwiftGet.exe** GitHub Actions workflow on `windows-latest`.
Open the **Actions** tab, choose the latest run, and download the **SwiftGet-windows** artifact.
Pushing a tag such as `v1.0.0` also attaches `SwiftGet.exe` to a GitHub Release.

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
