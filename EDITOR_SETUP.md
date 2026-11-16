# Setting up Mediatheque (editor's copy)

Mediatheque is a prep tool for video essays: it downloads videos, screenshots articles and works through a script's sources with you. This page gets it running on a Mac. It takes about 15 minutes, most of it waiting.

## 1. Before you start

- **Python 3.10 or newer.** Download the macOS installer from <https://www.python.org/downloads/macos/> and run it. When it finishes, a Finder window opens on the Python folder: double-click **Install Certificates.command** in it. (Without that step, some downloads fail with a "certificate" error.) If the window didn't open, it's in **Applications > Python 3.x**.
- **Google Chrome or Microsoft Edge.** Mediatheque uses it for screenshots and link previews. It also downloads its own copy of Chrome the first time, but having one installed is a good fallback.
- **Firefox, signed in to YouTube.** Install it from <https://www.firefox.com>, open YouTube in it and sign in. YouTube sometimes asks downloaders to "confirm you're not a bot", and reading your Firefox sign-in is the way through.

## 2. Unzip it into your home folder

1. In Finder, choose **Go > Home** (your home folder is the one with the house icon and your name).
2. Move the zip there, then double-click it. You now have a **Mediatheque** folder in your home folder.

Keep it there, not on the Desktop or in Documents: if those are synced with iCloud, iCloud breaks the private Python environment Mediatheque sets up inside its folder.

## 3. The first launch

1. Open the **Mediatheque** folder and double-click **Mediatheque.command**. A Terminal window opens and sets everything up. **This takes a few minutes the first time**, including a one-time download of Mediatheque's own browser (about 250 MB). Mediatheque opens when it's done.
2. If your Mac says it **"can't be opened"** or that Apple "could not verify" it: that's because it didn't come from the App Store.
   - **macOS 15 (Sequoia) or newer:** click **Done**, then open **System Settings > Privacy & Security**, scroll down to the message about Mediatheque.command and click **Open Anyway**. Enter your password, then click **Open**.
   - **macOS 14 or older:** Control-click (or right-click) **Mediatheque.command**, choose **Open**, then **Open** again.

   You only do this once. After that, Mediatheque clears the warning for everything in its folder.

## 4. Starting Mediatheque from now on

Double-click **Mediatheque** (the app with Mediatheque's icon, in the Mediatheque folder). It opens like any other app, with no Terminal window. Drag it to the Dock to keep it handy. Keep the app inside the Mediatheque folder: it starts the program that's in there, so moving it to Applications won't work.

If it ever won't open, double-click **Mediatheque.command** instead: the Terminal window shows what's going wrong.

## 5. One-time settings inside Mediatheque

- **Cookies:** click **Downloader** in the sidebar and set **Cookies** to **Firefox**. Leave it like that. When a YouTube download says "confirm you're not a bot", check you're signed in to YouTube in Firefox, then click **Retry**.
- **Sign In to Sites:** for paywalled articles (the New York Times, the Wall Street Journal, Bloomberg…), choose **Tools > Sign In to Sites…**, sign in to each site you have an account with, then close that window. Every screenshot after that is signed in.
- **Screen Recording:** the first time you record the screen (press **V** in the capture window), macOS asks for permission. Turn on **Mediatheque** in **System Settings > Privacy & Security > Screen & System Audio Recording**, then quit and reopen Mediatheque. (If you started it with Mediatheque.command, it's **Terminal** that needs the permission.)

**Help > Check Setup…** shows whether all of this is in place, with a fix beside anything that isn't.

## 6. Learning it

**Help > Script Tutorial…** is a five-minute guided tour of the Script tool, using a practice script, so none of your work is touched. Mediatheque offers it the first time it opens. **README.md** in the Mediatheque folder describes every feature in detail.

## 7. Updates

Updates arrive by themselves. When a new version is out, Mediatheque says **Update available** when it opens (it checks once a day) and shows what's new. Click **Update Now**, then **Restart Now**. Your projects, settings and downloads aren't touched.

- **Help > Check for Updates** checks right away.
- **Help > Undo Last Update** goes back to the version you had before, if an update causes trouble.

## 8. Getting help

If something goes wrong, choose **Help > Report a Problem…**. It saves a zip on your Desktop with Mediatheque's logs and its version number. Send that zip to whoever gave you Mediatheque, with a line on what you were doing. It doesn't include your projects, cookies or passwords.

For downloads that stop working, try **Tools > Update Engine** first: sites change often, and the update usually fixes it.
