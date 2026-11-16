"""Tools > Publish Update… (the owner's copy only): send a new version to editors' copies.

Steps, each said in plain English as it runs (ui/publish_ui.py shows them):

1. Check git and GitHub's `gh` tool are installed and signed in.
2. Commit as GitHub's private no-reply address, never a personal email.
3. Set __version__, check no personal file is about to be published, commit.
4. The first time: create the public repository (`gh repo create`).
5. Push, after checking every commit's email is the no-reply address (unpushed commits
   made with another address are re-signed first).
6. Build the editor's package (Pack for Another Editor) and its .sha256.
7. `gh release create v<version>` with both attached. Editors' copies find it from there.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .transfer import editor_zip_name, pack_editor
from .updates import ROOT, parse_version, repo_from_git

DIST = ROOT / "dist-editor"
TOOL_DIRS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]  # an app started from Finder has a short PATH
PRIVATE = ("LESSONS.md", "MAC.md", "EDITOR_EDITION.md", "edition.json", "settings.json", "suggest_profile.json")


class PublishError(Exception):
    """What went wrong and what to do about it, in plain English."""


def find_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for d in TOOL_DIRS:
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


class Publisher:
    def __init__(self, say: Callable[[str], None] = print, root: Path = ROOT):
        self.say = say
        self.root = root
        self.git = self.gh = ""
        self.env = dict(os.environ)
        self.env["PATH"] = os.pathsep.join([self.env.get("PATH", ""), *TOOL_DIRS])
        self.env["GIT_EDITOR"] = "true"  # never wait for an editor (rebase, commit)
        self.env["GH_PROMPT_DISABLED"] = "1"
        self.login = self.noreply = ""

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(list(args), cwd=self.root, env=self.env, capture_output=True, text=True, timeout=600)
        if check and r.returncode != 0:
            detail = (r.stderr or r.stdout).strip().splitlines()[-3:]
            raise PublishError(f"This step failed: {' '.join(Path(args[0]).name if i == 0 else a for i, a in enumerate(args))}"
                               + ("\n\n" + "\n".join(detail) if detail else ""))
        return r

    # -- the steps ------------------------------------------------------------------------------

    def check_tools(self) -> None:
        self.say("Checking git and GitHub's gh tool…")
        self.git = find_tool("git") or ""
        if not self.git or self.run(self.git, "--version", check=False).returncode != 0:
            raise PublishError("Git isn't set up on this Mac. Open Terminal, run:\n\n    xcode-select --install\n\n"
                               "and follow the prompts. Then try Publish Update again.")
        if not (self.root / ".git").is_dir():
            raise PublishError(f"The Mediatheque folder ({self.root}) isn't a git repository, so there's nothing to "
                               "publish from.")
        self.gh = find_tool("gh") or ""
        if not self.gh:
            raise PublishError("GitHub's command-line tool (gh) isn't installed. Download it from "
                               "https://cli.github.com and install it. Then open Terminal, run:\n\n    gh auth login\n\n"
                               "and sign in through your browser. Then try Publish Update again.")
        if self.run(self.gh, "auth", "status", check=False).returncode != 0:
            raise PublishError("The gh tool isn't signed in to GitHub. Open Terminal, run:\n\n    gh auth login\n\n"
                               "choose GitHub.com, then HTTPS, and sign in with your browser. Then try Publish "
                               "Update again.")

    def use_private_email(self) -> None:
        out = self.run(self.gh, "api", "user", "--jq", '.login + " " + (.id|tostring)').stdout.split()
        if len(out) != 2:
            raise PublishError("Couldn't read your GitHub account from the gh tool. Run 'gh auth login' again.")
        self.login, uid = out
        self.noreply = f"{uid}+{self.login}@users.noreply.github.com"
        self.run(self.git, "config", "user.email", self.noreply)
        if not self.run(self.git, "config", "user.name", check=False).stdout.strip():
            self.run(self.git, "config", "user.name", self.login)
        self.say(f"Signed in to GitHub as {self.login}. Changes are signed with GitHub's private address "
                 f"({self.noreply}), not your email.")

    def commit_version(self, version: str, current: str) -> None:
        if parse_version(version) < parse_version(current):
            raise PublishError(f"Version {version} is older than this copy's version ({current}). Choose a higher "
                               "number.")
        on_github = self.run(self.git, "remote", "get-url", "origin", check=False).returncode == 0 and \
            self.run(self.git, "ls-remote", "--tags", "origin", f"v{version}", check=False).stdout.strip()
        if self.run(self.git, "tag", "-l", f"v{version}").stdout.strip() or on_github:
            raise PublishError(f"Version {version} has already been published. Choose a higher number.")
        init = self.root / "mediatheque" / "__init__.py"
        text = init.read_text(encoding="utf-8")
        init.write_text(re.sub(r'__version__\s*=\s*["\'][^"\']*["\']', f'__version__ = "{version}"', text),
                        encoding="utf-8")
        self.run(self.git, "add", "-A")
        tracked = self.run(self.git, "ls-files").stdout.splitlines()
        bad = [f for f in tracked if f.lower().endswith(".csv") or "projects/" in f or f.startswith(("transfer/",
               "transfer-imported", "Credit Generator/", ".venv/")) or f.split("/")[-1] in PRIVATE]
        if bad:
            self.run(self.git, "reset", "-q", check=False)
            raise PublishError("These files would be published, and they look personal:\n\n" + "\n".join(bad[:10])
                               + "\n\nAdd them to .gitignore (or move them out of the Mediatheque folder), then try "
                               "again. Nothing was published.")
        self.run(self.git, "commit", "-q", "--allow-empty", "-m", f"Mediatheque {version}")
        self.say(f"Committed version {version}.")

    def ensure_repo(self, name: str, known: str) -> str:
        repo = known or repo_from_git()
        if repo:
            return repo
        name = re.sub(r"[^\w.-]+", "-", name.strip()) or "mediatheque"
        self.say(f"Creating the public page github.com/{self.login}/{name}…")
        self.run(self.gh, "repo", "create", name, "--public", "--source", str(self.root), "--remote", "origin",
                 "--description", "Mediatheque: a video-essay prep tool (downloads, screenshots, script sources).")
        return f"{self.login}/{name}"

    def push(self) -> None:
        self.run(self.gh, "auth", "setup-git", check=False)  # git uses gh's sign-in
        pushed = bool(self.run(self.git, "ls-remote", "--heads", "origin", "main").stdout.strip())
        if pushed:
            self.run(self.git, "fetch", "-q", "origin", "main")
        new_range = ["origin/main..HEAD"] if pushed else []
        emails = set(self.run(self.git, "log", "--format=%ae%n%ce", *new_range).stdout.split())
        if emails - {self.noreply}:
            self.say("Re-signing unpublished changes with GitHub's private address…")
            base = ["origin/main"] if pushed else ["--root"]
            try:
                self.run(self.git, "rebase", "-q", *base, "--exec",
                         "git commit -q --amend --no-edit --allow-empty --reset-author")
            except PublishError:
                self.run(self.git, "rebase", "--abort", check=False)  # leave the history as it was
                raise
        everyone = set(self.run(self.git, "log", "--format=%ae%n%ce").stdout.split())  # the check before every push
        if everyone - {self.noreply}:
            raise PublishError("Some changes already on GitHub are signed with another address: "
                               + ", ".join(sorted(everyone - {self.noreply})) + ". Nothing new was pushed.")
        self.say("Pushing the code to GitHub…")
        self.run(self.git, "push", "-q", "-u", "origin", "HEAD:main")

    def build(self, version: str, repo: str) -> tuple[Path, Path]:
        self.say("Building the editor's package…")
        DIST.mkdir(exist_ok=True)
        zip_path = DIST / editor_zip_name(version)
        pack_editor(zip_path, repo, version)
        digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        sha_path = zip_path.with_name(zip_path.name + ".sha256")
        sha_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
        return zip_path, sha_path

    def release(self, version: str, notes: str, repo: str, files: tuple[Path, Path]) -> str:
        self.say(f"Publishing version {version} on GitHub…")
        r = self.run(self.gh, "release", "create", f"v{version}", *map(str, files), "--repo", repo,
                     "--title", f"Mediatheque {version}", "--notes", notes.strip() or f"Mediatheque {version}.")
        return (r.stdout.strip().splitlines() or [f"https://github.com/{repo}/releases"])[-1]

    def publish(self, version: str, notes: str, current: str, repo_name: str = "mediatheque",
                known_repo: str = "") -> tuple[str, str]:
        """Everything, in order. Returns (repo, the release's address)."""
        self.check_tools()
        self.use_private_email()
        self.commit_version(version, current)
        repo = self.ensure_repo(repo_name, known_repo)
        self.push()
        files = self.build(version, repo)
        url = self.release(version, notes, repo, files)
        self.say("Done.")
        return repo, url
