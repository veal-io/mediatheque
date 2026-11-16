# Mediatheque

One desktop app for a video essay editor's prep work: downloading YouTube, TikTok, Instagram, Facebook, Reddit and X videos, screenshotting articles, working through a script's sources, and building patron credits.

## Run it

Double-click **`Mediatheque.bat`**. The first launch sets up a private Python environment, which takes about a minute. After that it opens straight away.

To create (or re-create) the Start menu and desktop shortcuts, for example after moving the project folder, run:

```
.venv\Scripts\python -m mediatheque.shortcuts
```

**On a Mac:** double-click **Mediatheque** (the app in the Mediatheque folder). See **On a Mac** below, and **MAC.md** for moving your projects between computers.

## On a Mac

Tested on a MacBook Pro (Apple silicon, macOS 15) with Python 3.15 and Google Chrome.

**Starting it:** double-click **Mediatheque** (the app with Mediatheque's icon in the Mediatheque folder). You can drag it to the Dock. It opens like any other app, with no Terminal window. Keep it inside the Mediatheque folder: it's a launcher for the code there, not a copy, so moving it to Applications won't work (put it in the Dock instead).
- `Mediatheque.command` still works and makes the app if it's missing. Use it to see what's happening as Mediatheque starts, if it ever won't open. The very first setup on a new Mac is also easiest with it.
- What used to show in Terminal goes to `~/Library/Application Support/Mediatheque/logs/launch.log`. If Mediatheque can't start, an alert says so.
- After a Python update, the app updates itself the next time it opens.

**One-time permissions:**
- **Screen recording:** the first time you record, macOS asks. Turn on **Mediatheque** in System Settings > Privacy & Security > Screen & System Audio Recording, then reopen Mediatheque. (If you started it with `Mediatheque.command`, it's **Terminal** that needs the permission instead.) Until then, the recorder says so instead of recording.
- **Keychain:** your Claude API key (Tools > Claude API Key…) is kept in the Mac's Keychain. macOS may ask once whether Python can use it. Choose **Always Allow**. The first time you open Mediatheque with the app, it may ask again, once.

**What's different from Windows:**
- **Shortcuts use Cmd:** Cmd+1–4 switch tools, Cmd+F finds in the script and Cmd+S saves. Quit with Cmd+Q or File > Exit.
- **The menus stay in the window,** under the title bar, as on Windows. The Mac's own menu bar only has **Mediatheque > Quit**.
- **Moved projects:** when a project opens and its folder isn't where it was saved (it was on Windows, or on another drive), Mediatheque looks for a folder with the same name on your drives and in your home folder, and offers to reconnect the project to it.
- **Show in folder / Open** use Finder. **View Log** opens the log in the Console app.
- **Mediatheque has its own browser on a Mac,** "Google Chrome for Testing", for the capture window, Sign In and the card previews. `Mediatheque.command` downloads it the first time (about 250 MB). macOS treats it as a separate app, so your own Chrome opens normally while Mediatheque runs. (If it's missing, Mediatheque falls back to Google Chrome. Chrome then won't open on its own while Mediatheque is running, unless it was already open.) Its sign-ins are separate from Windows and from your Chrome, so sign in with Tools > Sign In to Sites….
- **Cookies:** set the Downloader's Cookies to **Firefox** and sign in to YouTube in Firefox, as on Windows.
- **Resizing the capture box:** **Option+Wheel** sets the height and **Cmd+Wheel** the width. Ctrl+Wheel works too, unless macOS's zoom (Accessibility > Zoom > "Use scroll gesture with modifier keys") is turned on, because it takes Ctrl+scroll first. On a trackpad the box follows how far you swipe.
- **Recording:** V takes the capture window full screen with the control bar at the bottom. macOS would put that bar in the video, so on a Mac it **hides while recording**: click **Start Recording**, and press **Control+S** to end. The bar comes back to save or discard. Recordings are the whole screen at full Retina size, 60 fps, H.264 with HD (Rec. 709) colour.
- **Conversions** (VP9/AV1 downloads, recordings) use Apple's hardware H.264 encoder.
- **Files** are in `~/Library/Application Support/Mediatheque` (settings, logs, projects), not `%APPDATA%`.

## Using it

1. Click **Downloader** in the sidebar (or press Ctrl+2). It's one page for every platform.
2. Paste a YouTube, TikTok, Instagram, Facebook, Reddit or X link and press **Enter** (or click **Download**). You can paste several links at once, in any mix of platforms. Each row says which platform it's from, and downloads from different platforms run side by side. Links that aren't videos, such as articles, are left in the box.
3. **Save to** is the dropdown of your download folders. Choose **Choose another folder…** to add a folder. Right-click the dropdown to remove a folder from the list.
4. **Quality** applies to every platform: **Best quality**, **4K**, **1440p**, **1080p H.264**, **720p H.264**, or audio only. The resolutions are YouTube sizes. TikTok uses its H.264 version for the H.264 choices, and Instagram, Facebook, Reddit and X always download their best version. Every choice gives you files that open in Premiere Pro:
   - **Automatic conversion:** YouTube above 1080p, and Instagram above 720p, only come as VP9 or AV1, which Premiere can't read. Those files are converted to H.264 automatically, on the NVIDIA GPU if there is one, and the row says "converted to H.264 for Premiere".
   - **No conversion otherwise:** when a native H.264 version exists at the same quality, it's downloaded directly.
   - **TikTok:** *Best quality* is sometimes H.265, which Premiere can read. *H.264 MP4* trades resolution for maximum compatibility.
   - **Audio:** **Audio only** gives M4A or WAV.
5. **Cookies** (one setting for every platform): leave it on **None**. Public YouTube, TikTok and Instagram links all download without signing in. You only need cookies for age-restricted, private or members-only content:
   - **For YouTube's "confirm you're not a bot", use Firefox:** sign in to YouTube in Firefox, set Cookies to **Firefox**, and Retry. This is the setup that's known to work.
   - **Mediatheque Browser** uses the sign-ins from **Tools > Sign In to Sites…**, the same browser the Screenshotter uses. Known issue: a YouTube sign-in there hasn't been enough to clear the bot check, so use Firefox for YouTube. Choosing it offers to open the sign-in window if you aren't signed in yet. It can't be read while that browser is open (the Sign In window or a capture). The download then goes ahead without cookies and says so on its row.
   - **Firefox** reads your Firefox sign-in directly.
   - **cookies.txt file** is the option for Chrome and Edge users. Chrome encrypts its cookies on Windows so no other program can read them. Instead, install the [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) extension, open the site while signed in, export, and pick that file.
   - If the cookies can't be read, the download carries on without them, and the row says so.

Each finished download has its own **Copy Source** button, which copies that video's on-screen credit: "Title | Channel" for YouTube, the @handle for TikTok, Instagram and X, "u/name · r/subreddit" for Reddit, or the poster's name for Facebook. **Copy Finished Names** copies the names of every finished download, one per line, with TikTok, Instagram and X as @handles. **View Log** has a log for each platform.

**Facebook:** the Downloader takes Facebook videos and reels (`/watch`, `/reel/`, `/videos/`, `fb.watch`, `/share/v/` and `/share/r/` links). Written posts aren't videos, so in a script they go to the Screenshotter instead.

**Reddit:** paste a post's link (from the address bar or Share > Copy link), a `redd.it` short link or a `v.redd.it` link. Reddit only hands out videos to signed-in accounts, so sign in to Reddit in Firefox and set Cookies to **Firefox**. Files are named `Title - u_name [id].mp4`. In the Script tool, Execute checks each Reddit post for a video and sorts it (see **X and Reddit posts** under Script). On the Screenshots page, imported Reddit posts count as articles, and only `v.redd.it` links count as videos.

**X (Twitter):** paste a post's link (`x.com/…/status/…`, or twitter.com, or an fxtwitter / vxtwitter / fixupx share link). Public videos download without signing in. For posts X only shows to signed-in accounts, sign in to X in Firefox and set Cookies to **Firefox**. A post with several videos downloads them all. In the Script tool, Execute checks each X post for a video and sorts it (see **X and Reddit posts** under Script). On the Screenshots page, imported X posts count as articles.

**File names:** TikTok, Instagram and X downloads are named after the creator's handle: `@user.mp4`. More videos from the same creator become `@user (2).mp4`, `@user (3).mp4` and so on. YouTube downloads are named `Title - Channel.mp4`. To change a naming scheme, edit `rename` in that platform's file in `mediatheque/platforms/`.

## Article Screenshots

Click **Screenshots** in the sidebar (or press Ctrl+3), choose **Save to**, then:

- **Import Script…** (or drag a PDF or Word script onto the window) queues every article it links to. TikTok, Instagram and YouTube links are set aside, and **Send Video Links to Downloader** queues them in the Downloader.
- **Link** + Enter adds a late addition. If the capture window is already open, it joins the queue.

A capture window (Microsoft Edge) opens and shows each article in turn. If the link points at a quote (made with "Copy link to highlight"), the page opens on that passage and briefly flashes it yellow. The flash fades and never appears in a shot.

| Do this | To |
|---|---|
| Click | Snap what's inside the box |
| **Y** / **A** / **N** | Keep and go to the next article / keep and take another here / retake |
| Wheel, arrow keys, Page Up/Down | Scroll the page |
| Alt+Wheel / Ctrl+Wheel | Make the box taller or shorter / wider or narrower (it starts 4:1) |
| Hold Shift | Box fills the whole screen |
| **B** | Browse mode: use the page normally, to sign in, close a popup or pass an age gate. **B** again to go back to capturing |
| **Record Screen** button or **V** | Record the screen instead (see below) |
| **C** | Hide or show sticky headers and floating bars |
| **H** | Hide or show the script passage panel (captures started from a script) |
| **R** | Reset the page: reload it, return to the quote and replay the highlight. In review, also throws away the shot |
| **S** / **Esc** | Skip this article / finish the session |

**Recording the screen:** click **● Record Screen** in the capture window's bar, or press **V**, from the Screenshots page or the Script tool's Execute Screenshotter. The article goes full screen and works like Browse mode, so you can scroll and click normally. A small control bar at the bottom of the screen holds the buttons. You can see it, but it doesn't appear in the video.
- **● Start Recording** becomes clickable after a few seconds, once Edge's full-screen notice has faded. **Back** returns to the Screenshotter without recording.
- **■ End Recording** finishes the video. Then choose **Save, Next Article**, **Save, Record Another**, or **Discard**, which deletes it and returns to the Screenshotter on that page. **Watch** opens it in your video player first.
- **Where they go:** a `Screen Records` folder in the project folder, beside `Screen Captures`. From the Screenshots page, that's inside the **Save to** folder, or beside it if **Save to** is a `Screen Captures` folder. Files are named like shots, as `7_NYT_Headline Words.mp4`, with `(2)` and so on.
- **The video:** the whole screen, cursor included, as H.264 at a locked 60 fps with HD color, so Premiere can open it. There's no sound. On a second monitor it uses a slower capture method, about 25 fps at 4K.

Shots are saved at 3× resolution as `NYT_Headline Words.png`, with `(2)`, `(3)` and so on for more shots from the same article. Short names for common outlets are in `SITE_PREFIXES` in `mediatheque/screenshots/naming.py`. Other sites use their own name.

**Sign In to Sites…** (bottom of the Screenshots page, or **Tools > Sign In to Sites…**) opens the Screenshotter's own browser as a normal Edge window, with links to Fortune, the NYT, WSJ, Bloomberg and other paywalled sites. Sign in there, then close it (or click **Done Signing In**). Every capture after that is signed in. Sites that normally sign you out when the browser closes are kept signed in for 30 days. Use this rather than signing in inside the capture window, where sign-in pop-ups can break. Only one of the two windows can be open at a time, so a capture waits until the Sign In window is closed.

Sign-ins and age gates you clear in the capture window are remembered for next time. Articles you skip, or don't reach before finishing, can be revisited with **Resume**.

## Script

Click **Script** at the top of the sidebar (or press Ctrl+1, or **File > Open Script Project…**). In Google Docs, download the script as **Word (.docx)**, then **Import Script…** (or drag it onto the window).

- **Choose What to Import:** when you import a script, Mediatheque lists its paragraphs and suggests where the script ends, usually at "(sign off)". The research notes after it are unticked. Adjust the ticks (**Leave Out Selected**, **Keep Selected**, **Leave Out From Here to End**), then **Import**. Left-out paragraphs aren't shown, highlighted, downloaded or captured. **Trim Script…** reopens the list at any time. Text you bring back gets its highlights numbered like `31a`, so nothing existing is renumbered. (Word files don't record pages, which is why this works by paragraph.)
- **Automatic highlights:** every bracketed link (`[source]`, `[video]`, `[tiktok]`…) is attached automatically to the sentence it follows. Markers in the middle of a sentence split it at that point, and markers that open a paragraph attach to the end of the previous one.
- **Your own highlights:** select words in the script and click **Highlight Selection**. Any links embedded in your selection are offered one at a time with a preview: **Attach**, **Skip** or **Attach All**. **Add Links…** on a highlight's card attaches any TikTok, Instagram, YouTube or article link by pasting.
- **Numbers:** every highlight is numbered, both in the margin beside the script and on its card. Everything it downloads starts with that number: `7_@user.mp4`, `7_Title - Channel.mp4`, `7_NYT_Headline Words.png`. Highlights are numbered 1, 2, 3… in script order, and simply renumber as you add or remove them, until you first run Execute Video Downloads or Execute Screenshotter. From then on numbers are fixed, because they're on files: a highlight added later takes the number before it plus a letter (`7a`, `7b`), and numbers of removed highlights aren't reused.
- **Adjusting a highlight:** select words that overlap an existing highlight and the button becomes **Adjust Highlight N**. It moves that highlight onto your selection and keeps its number, links, files, images and note. New embedded links in the extra words get the usual Attach / Skip.
- **Combining highlights:** select words that touch two or more highlights in the same paragraph and the button becomes **Combine Highlights 2 & 3**. They merge into the first one, which keeps its number and covers all their text. Links (with their files), images, video leads and notes from the others move onto it. Before anything has run, the highlights after it renumber as usual. After that, the merged numbers are retired, and files already downloaded keep their old numbers.
- **Editing the script's text:** double-click a paragraph to change its words, including adding a line. Edits are saved in the project only (the Word file isn't touched), and highlights and links in that paragraph move with their words. **Revert to Original** undoes your edits to that paragraph.
- **Finding:** Ctrl+F opens a find bar over the script. Enter goes to the next match, Shift+Enter to the previous one, and Esc closes it.
- **Clicking a highlight** in the script brings its card into the middle of the Highlights list, with its files marked in blue. Clicking a card jumps the script to its passage.
- **Re-Take:** a captured article's row has **Re-Take**, which opens the capture window for just that article. Your existing shots are kept, and new ones are numbered on with the usual naming (`15_NYT_Headline (2).png`) and listed with the others. If you skip or close the window, nothing changes.
- **Skipping an article:** **Skip** on an article link's row leaves it out of Execute Screenshotter. The link stays on the highlight, marked "Left out", and **Include** puts it back.
- **Suggest (Claude):** **Suggest** on a highlight's card (or right-click the highlighted text) asks Claude to research sources for that passage: **Suggest** for both, **Suggest Articles**, or **Suggest Videos**. First a small box asks for any pointers ("US sources only", "find the 2023 Bloomberg piece", "the CEO's interview, not the ad"). Leave it empty to just ask. **Enter** asks, **Shift+Enter** adds a line, and the box remembers what you typed for that highlight. It runs in the background (about a minute) and opens a window with what it found:
  - **Articles** have a preview, why they fit, and the sentence to capture. **Attach** adds the link with that sentence built in, so the Screenshotter opens on it and flashes it. Claude can only offer pages that came up in its own web searches, so links aren't made up.
  - **Videos** are always leads, never links: the platform, what to type into its search, and what clip to look for. **Search** opens that search in your browser; **Keep Lead** saves it on the card. Find the clip, then add it with **Add Links…**.
  - **Learning:** every link attached to any highlight in any project (by hand, from brackets or from Suggest) teaches Suggest what you pick, and so does every suggestion you **Skip**. A short sourcing profile is rewritten from them as projects grow, and each request includes your past passages most like this one.
  - **Cost:** it uses your own Anthropic API key (**Tools > Claude API Key…**, kept in Windows Credential Manager) and Claude Opus 5.5. The window shows roughly what each request cost, typically 10–30¢. The script's text is sent to Anthropic.
- **Removing a link:** the **✕** at the end of a link's row removes it from the highlight. Anything it already downloaded stays on disk. It's greyed out while that link is downloading or being captured.
- **Notes:** **Note…** on a highlight's card adds a note, such as what to cut on or which clip to favor. It shows on the card. The highlight's number in the margin gets a yellow corner.
- **Images:** attach your own images (Photoshop exports, pictures from the web) to a highlight with **Add Images…** on its card, or by dropping them onto the card or onto the highlighted text in the script. Image files from elsewhere are copied into the project's `Images` folder, and an image dragged straight off a web page is downloaded there. File names are left as they are. **Detach** removes an image from the highlight but leaves the file in place, and **Copy All** includes images.
- **Project folder:** the video's project folder (the one containing `BRoll`, `Screen Captures` and `Images`). Mediatheque finds it on import by looking up from the script, which normally sits in `<project>\Project Files`. Videos go to `BRoll`, screenshots to `Screen Captures`, screen recordings to `Screen Records` and images to `Images`; the links under the picker open each one. Each script project remembers its own folder. The subfolder names are set in `SUBFOLDERS` in `mediatheque/script/project.py`.
- **X and Reddit posts:** a post's address doesn't say whether it holds a video or just text, so clicking either Execute button first checks each one, a second or two per post. Posts with a video go to Execute Video Downloads, and text or image posts go to Execute Screenshotter. Each result is saved, so a post is only checked once.
  - **Video / Article button:** on these rows, the label is a button. Click it to switch a post, for example to screenshot a post that has a video. Your choice is never overwritten.
  - **Article?** means the check failed, so the post counts as an article for now and is checked again on the next Execute. Hover over it for the reason. Reddit needs you signed in to Reddit in Firefox with Cookies set to Firefox.
  - **Deleted X posts:** X reports these the same way as text posts, so they go to the Screenshotter.
- **Added Sources:** the **Added Sources** tab, beside Highlights and Video Downloads, lists every article attached to a highlight that the script itself doesn't link to, such as ones from **Add Links…** or **Suggest**. It's meant for the sources document at the end of a project. Each entry shows its highlight number and passage, and the list keeps itself up to date. Links in left-out paragraphs, like research notes, count as already in the script, and the same page with a different quote counts once. Each link has its own **Copy** button, and clicking a link opens it. **Copy All Links** copies them all, one per line, and **Copy All with Highlights** adds each one's number and passage. Quote fragments are left off.
- **Execute Video Downloads:** downloads every video link, using the Downloader's Quality and Cookies settings. Progress and failures are on the **Video Downloads** tab, with Retry.
- **Execute Screenshotter:** opens the capture window for every article link. A yellow panel shows the highlight's number and its full passage from the script, with your note beside it, so you know what the shot has to show. It moves to the bottom when the box reaches the top, and **H** hides or shows it. Like the box and the control bar, it's removed before every shot and never appears in the picture.
- **Files:** each highlight's card lists the files it produced. To bring them into Premiere, drag them onto the Project panel, a bin or the timeline:
  - **One file:** drag its row (grab it anywhere, the ⠿ grip marks it).
  - **All of a highlight's files:** press and drag **Drag All**.
  - **Copy Source** on a video's row copies its on-screen credit, like the Downloader's button: "Title | Channel" for YouTube, the @handle for TikTok and Instagram, the uploader's name for Facebook. YouTube credits are saved when the video downloads. Older downloads are looked up once through YouTube's public oEmbed link, which needs no sign-in, then remembered.
  - **Copy** (screenshots and images) and **Copy All** put files on the clipboard for pasting into Explorer. Premiere doesn't accept pasted files.

**Saving and switching scripts:**
- **Autosave:** every change saves immediately, and the indicator under the Highlights list shows when ("Saved 3:42 PM"). Ctrl+S saves on demand.
- **Switching:** the **Script** dropdown lists your projects, so pick one to switch. Downloads and captures that are still running keep saving into their own project after you switch away.
- **Moved or renamed scripts:** each project keeps a copy of its script's text, so it still opens. **Locate Script…** reconnects it to the Word file's new location.
- **Manage Projects…** (in the dropdown) lists every project with its progress. From there you can open one, **Restore Earlier Version…** (backups are kept automatically, at most every 10 minutes, latest 15), or **Remove from List** (the project file is moved to the backups folder, never deleted).
- **Where they live:** projects are saved under `%APPDATA%\Mediatheque\projects` (on a Mac, `~/Library/Application Support/Mediatheque/projects`), backups in its `backups` folder. Re-importing an edited version of the same script picks up where you left off.

## Script Tutorial

**Help > Script Tutorial…** is a guided tour of the Script tool for someone new to Mediatheque, about five minutes. On the first launch (of an editor's copy, or any copy with no projects yet), Mediatheque offers it once: **Start**, **Not now** (asks again next launch) or **Don't ask again**.

- **Practice material:** it copies a sample script (`mediatheque/assets/tutorial/Sample Script.docx`) into `~/Mediatheque Tutorial/Project Files/` and imports it as a normal project, so real work is never touched. The sample has `[source]` and `[video]` links to Wikipedia, the first YouTube video and the first post on X, and research notes after "(sign off)" for the import step to leave out.
- **The coach panel** sits beside the part of the window it's talking about, which gets a red and yellow outline. Hands-on steps ("click Note… and type a note") move on by themselves once it's done, and **Next** always skips. Drag the panel by its title bar if it's in the way.
- **Afterwards:** at the end, or on **Exit Tour**, it offers to delete the practice project (the folder and its project file), then goes back to the script you had open. **Help > Delete Practice Project** does the same later.
- **Changing the wording:** every step's title, text, what it outlines and what it waits for is in `mediatheque/tutorial_steps.py`, with notes at the top on what each field does.

## Credit Generator

Click **Credits** in the sidebar (or press Ctrl+4):

1. Click **Import CSV…** and pick your Patreon members export, or drag the CSV onto the window.
2. The names from the `Name` column are split into three lists, in file order.
3. Click **Copy List 1/2/3** and paste each list into the credits MOGRT in Premiere.

The sidebar on the left switches between tools: Ctrl+1 Script, Ctrl+2 Downloader, Ctrl+3 Screenshots, Ctrl+4 Credits.

## Editions: your copy and an editor's copy

The same code runs as two editions, chosen by one small file, `edition.json`, next to `Mediatheque.command`:

- **Your copy has no `edition.json`.** Everything is on, including Suggest, and it never updates itself.
- **An editor's copy** (made with **Tools > Pack for Another Editor…**) has `{"suggest": false, "updates": "owner/repo"}`. Suggest is gone: no **Suggest** button on cards, no Suggest items when you right-click the script, no **Lead** rows, no **Tools > Claude API Key…**, and the `anthropic` and `keyring` packages aren't loaded. Video leads, skipped suggestions and pointers saved in a project you send them are kept as they are, just not shown, so the project comes back to you intact.

The switch is read in `mediatheque/edition.py`. Improvements you make to your copy reach the editor's copy unchanged, with Suggest still switched off.

## When something breaks

Platforms change their sites often. In order:

1. Choose **Tools > Update Engine**. This pulls the latest [yt-dlp](https://github.com/yt-dlp/yt-dlp), which usually fixes it within a day or two of a platform change.
2. Try **Cookies**.
3. Click **View Log** in the Downloader and pick the platform. Each platform logs to its own file in `%APPDATA%\Mediatheque\logs\` (on a Mac, `~/Library/Application Support/Mediatheque/logs`).
4. **Help > Check Setup…** lists what Mediatheque needs on this computer, with a fix beside anything missing: Python 3.10 or newer, Edge or Chrome, Firefox, ffmpeg and which H.264 encoder works, Screen Recording permission (on a Mac), and how old the download engine is (over four weeks suggests **Tools > Update Engine**).
5. **Help > Report a Problem…** saves a zip on the Desktop and shows it in Finder or Explorer, ready to send: the logs folder, plus a note of Mediatheque's version, the system's, Python's and yt-dlp's, and optionally what you were doing. No projects, cookies, keys or settings go in it.

The version number is `__version__` in `mediatheque/__init__.py`, shown in **Help > About**.

## Layout

```
mediatheque/
  platforms/       one file per platform: link matching, quality presets,
    youtube.py     filename template, and plain-English error hints.
    tiktok.py      A platform-specific fix goes here and only here.
    instagram.py
  engine.py        download queue: one background worker per platform
  osutil.py        the Windows / Mac differences (opening files, Finder vs Explorer, capture exclusion)
  transfer.py      Tools > Pack for Another Computer, and importing that on the other computer
  credits.py       Credit Generator: reads the CSV and splits the names into three lists
  script/          Script projects: .docx reading and automatic highlights (docx.py),
                   saved projects (project.py), link previews (previews.py) and
                   Claude's source suggestions (suggest.py; window in ui/suggest_ui.py)
  screenshots/     Article Screenshots: script link extraction (links.py), file naming
                   (naming.py), the capture window (session.py) and its in-page box and
                   quote flash (overlay.js, quote.js)
  macapp.py        builds Mediatheque.app, the Mac launcher (no Terminal window)
  ui/              window, tool pages (downloader_page.py is the Downloader), folder dropdown
    theme.py       the Windows 98 look: hand-drawn bevels, pixel-art icons
    chrome.py      the classic title bar and min/max/close buttons
```

The bundled ffmpeg (`imageio-ffmpeg`) merges video with audio, and the bundled Deno runtime unlocks YouTube's full set of formats. Neither needs a separate install.
