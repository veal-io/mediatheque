"""The Script Tutorial's steps (Help > Script Tutorial…). Edit the wording here.

Each step is a dict:

    title    The step's heading.
    text     What the coach panel says. <b>bold</b> works. A blank line starts a new paragraph.
             {mod} becomes Cmd on a Mac and Ctrl on Windows; {folder} the practice project's folder.
    target   What to outline, one name or a list (the panel sits beside the first). Names:
               import_btn, trim_btn, script_combo, project_folder, folder_links, script_view,
               highlight_btn, exec_videos, exec_shots, saved_label,
               tab:highlights, tab:downloads, tab:added      (the tabs above the cards)
               card:first, card:video, card:x, card:files    (a highlight's card)
               para:N                                        (paragraph N of the practice script)
    tab      Optional: show this tab first: "highlights", "downloads" or "added".
    try_it   Optional: something to do. The step moves on by itself once it's done
             (Next always skips it).
    wait     What "done" means for try_it. One of:
               practice_open     the practice script is open
               note_added        a highlight got a note
               highlight_added   you made a highlight of your own
               combined          two highlights became one
               downloads_started Execute Video Downloads started
               capture_started   Execute Screenshotter opened the capture window
               kind_switched     an X or Reddit post was switched Video <-> Article
               added_tab         the Added Sources tab is showing
    action   Optional: a button on the panel, as (label, what it does). What it does is one of:
               import_practice   copy the practice script into {folder} and import it
               delete_practice   delete the practice project (folder and project file)
"""

STEPS = [
    {
        "title": "Welcome to the Script tool",
        "text": "This short tour walks through the Script tool with a practice script, so none of your "
                "real work is touched. It takes about five minutes.\n\n"
                "Each step outlines the part of the window it's talking about. Click <b>Next</b> to go on, "
                "<b>Back</b> to go back, or <b>Exit Tour</b> to stop at any time. You can drag this panel by "
                "its title bar if it's in the way.",
        "target": "script_combo",
    },
    {
        "title": "1. Importing a script",
        "text": "Each video starts with its script. In Google Docs, choose <b>File > Download > Microsoft "
                "Word (.docx)</b>, then click <b>Import Script…</b> here (or drag the file onto the window).\n\n"
                "First, a window called <b>Choose What to Import</b> lists every paragraph. Mediatheque guesses "
                "where the script ends, usually at \"(sign off)\", and unticks the research notes after it, so "
                "they're never highlighted, downloaded or captured. <b>Trim Script…</b> reopens that list later.",
        "target": ["import_btn", "trim_btn"],
        "try_it": "Click <b>Import the Practice Script</b> below. In Choose What to Import, see that the research "
                  "notes at the end are unticked, then click <b>Import</b>.",
        "wait": "practice_open",
        "action": ("Import the Practice Script", "import_practice"),
    },
    {
        "title": "2. Automatic highlights",
        "text": "Every bracketed link in the script, like <b>[source]</b> or <b>[video]</b>, became a yellow "
                "highlight on the sentence it follows. Each one has a number, in the margin and on its card.\n\n"
                "Numbers simply follow the script until you first run <b>Execute Video Downloads</b> or "
                "<b>Execute Screenshotter</b>. From then on they're fixed, because they're in the file names: "
                "a highlight added later takes the number before it plus a letter, like <b>7a</b>.",
        "target": "script_view",
    },
    {
        "title": "3. A highlight's card",
        "text": "Each highlight has a card on the right: its number and passage, then one row per link. "
                "<b>Video</b> links are downloaded and <b>Article</b> links are screenshotted. Click a link to "
                "open it. Clicking a card jumps the script to its passage, and clicking a highlight in the "
                "script brings its card into view.\n\n"
                "<b>Note…</b> adds a note, such as what to cut on. It shows on the card and in the capture "
                "window, and the number in the margin gets a yellow corner.",
        "target": "card:first",
        "tab": "highlights",
        "try_it": "Click <b>Note…</b> on the first card and type a note.",
        "wait": "note_added",
    },
    {
        "title": "4. Your own highlight",
        "text": "You can highlight any words yourself: select them in the script, then click "
                "<b>Highlight Selection</b>. Links inside your selection are offered one at a time with a "
                "preview: <b>Attach</b>, <b>Skip</b> or <b>Attach All</b>.\n\n"
                "For a link that isn't in the script, click <b>Add Links…</b> on the card and paste it.",
        "target": ["para:2", "highlight_btn"],
        "try_it": "Select the sentence that starts \"Most clips are made from galvanized steel wire\", click "
                  "<b>Highlight Selection</b>, and Attach or Skip its two links.",
        "wait": "highlight_added",
    },
    {
        "title": "5. Adjust and Combine",
        "text": "Select words that overlap a highlight and the button becomes <b>Adjust Highlight</b>: it moves "
                "that highlight onto your selection and keeps everything attached to it.\n\n"
                "Select across two highlights in the same paragraph and it becomes <b>Combine Highlights</b>: "
                "they merge into the first one, with all their links, files and notes.",
        "target": ["para:3", "highlight_btn"],
        "try_it": "The Norway paragraph has two highlights. Select from the middle of the first to the middle of "
                  "the second, then click <b>Combine Highlights</b>.",
        "wait": "combined",
    },
    {
        "title": "6. The project folder",
        "text": "Each script belongs to a video's project folder. Mediatheque finds it by looking up from the "
                "script, which normally sits in <b>Project Files</b>. For this practice script it's "
                "<b>{folder}</b>.\n\n"
                "Everything goes into the standard folders inside it: videos to <b>BRoll</b>, screenshots to "
                "<b>Screen Captures</b>, screen recordings to <b>Screen Records</b> and your own images to "
                "<b>Images</b>. The links under the folder open each one.",
        "target": ["project_folder", "folder_links"],
    },
    {
        "title": "7. Execute Video Downloads",
        "text": "<b>Execute Video Downloads</b> downloads every video link in the script, using the "
                "Downloader's Quality and Cookies settings. Each file starts with its highlight's number, "
                "like <b>7_Title - Channel.mp4</b>.\n\n"
                "It first checks any X or Reddit posts, a second or two each, to see whether they hold a video.",
        "target": "exec_videos",
        "try_it": "Click <b>Execute Video Downloads</b> to download the practice clip (it's tiny).",
        "wait": "downloads_started",
    },
    {
        "title": "Video Downloads tab",
        "text": "Progress and any failures show on the <b>Video Downloads</b> tab, with <b>Retry</b> for "
                "anything that failed. You can keep working while it runs.\n\n"
                "If YouTube asks you to \"confirm you're not a bot\", sign in to YouTube in Firefox, set the "
                "Downloader's <b>Cookies</b> to <b>Firefox</b>, and Retry.",
        "target": "tab:downloads",
        "tab": "downloads",
    },
    {
        "title": "8. Execute Screenshotter",
        "text": "<b>Execute Screenshotter</b> opens the capture window on each article in turn. Move the box over "
                "what you want and <b>click</b> to snap it. Then press <b>Y</b> to keep it and go to the next "
                "article, <b>A</b> to keep it and take another here, or <b>N</b> to retake.\n\n"
                "A yellow panel shows the highlight's passage and note, so you know what the shot needs. "
                "<b>H</b> hides it. It never appears in the picture.\n\n"
                "<b>V</b> records the screen instead (into Screen Records). The first time, your Mac asks for "
                "Screen Recording permission.",
        "target": "exec_shots",
        "try_it": "Optional: click <b>Execute Screenshotter</b>, take a shot or two, and press <b>Esc</b> to finish.",
        "wait": "capture_started",
    },
    {
        "title": "9. Files on a card",
        "text": "Everything a highlight produces is listed on its card. To bring files into Premiere, drag them "
                "onto the Project panel, a bin or the timeline: drag a file's row (grab it anywhere), or press "
                "and drag <b>Drag All</b> for all of them.\n\n"
                "<b>Copy Source</b> on a video copies its on-screen credit, like \"Title | Channel\". "
                "<b>Re-Take</b> on a captured article opens the capture window again, keeping the shots you "
                "already have.",
        "target": "card:files",
        "tab": "highlights",
    },
    {
        "title": "10. X and Reddit posts",
        "text": "An X or Reddit address doesn't say whether the post holds a video or just text, so Execute "
                "checks each one first. On these rows the <b>Video</b> / <b>Article</b> label is a button: "
                "click it to switch a post yourself, for example to screenshot a post that has a video. "
                "Your choice is never overwritten.",
        "target": "card:x",
        "tab": "highlights",
        "try_it": "Click the label on the X post's row to switch it.",
        "wait": "kind_switched",
    },
    {
        "title": "11. Added Sources",
        "text": "The <b>Added Sources</b> tab lists every article attached to a highlight that the script itself "
                "doesn't link to, such as ones added with <b>Add Links…</b>. It's for the sources document at "
                "the end of a project.\n\n"
                "Each link has its own <b>Copy</b> button. <b>Copy All Links</b> copies them all, and "
                "<b>Copy All with Highlights</b> adds each one's number and passage.",
        "target": "tab:added",
        "try_it": "Click the <b>Added Sources</b> tab.",
        "wait": "added_tab",
    },
    {
        "title": "12. Saving and switching scripts",
        "text": "Every change saves by itself, and the time shows under the cards. {mod}+S saves right away.\n\n"
                "The <b>Script</b> dropdown lists your projects: pick one to switch. Downloads and captures "
                "that are still running keep saving into their own project. <b>Manage Projects…</b>, at the "
                "bottom of the dropdown, shows every project's progress and can restore an earlier version.",
        "target": ["script_combo", "saved_label"],
    },
    {
        "title": "That's the tour",
        "text": "You've seen the whole Script tool. <b>Help > Script Tutorial…</b> runs this tour again, and "
                "README.md has the details of every feature.\n\n"
                "If something goes wrong, <b>Help > Report a Problem…</b> makes a zip of Mediatheque's logs to "
                "send. It doesn't include your projects.\n\n"
                "You can delete the practice project now, or keep it to look at later "
                "(<b>Help > Delete Practice Project</b> removes it any time).",
        "target": "script_combo",
        "action": ("Delete the Practice Project", "delete_practice"),
    },
]
