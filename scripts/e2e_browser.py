"""Browser end-to-end journey through the real site (SPEC-06, website testing).

Drives the statically-exported frontend served by the app, against the
deterministic stub LLM (evals/stub_llm.py), the way an author would:

  sign in -> create a book with a synopsis -> paste a source -> review and
  commit the extracted canon -> see the cast in the Canon tab -> add a chapter
  -> generate a scene from the cast picker -> read the scene -> generate an
  outline (staged job), confirm its premise, promote it to chapters -> run a continuity check -> export the book.

Then the rest of the surface: each character's Voice holds only their own
words (SPEC-17), trash and restore a character, restore an earlier
synopsis, regenerate the outline with an EDITED premise (and prove the edit
reached the model), plot threads, edit a scene and load an earlier revision,
the editor's unsaved-changes guard, local draft and revision dialog (SPEC-16),
reorder and delete a chapter and restore it from the Trash, generate all of a
promoted chapter's planned scenes in order, a clean Markdown manuscript with
scene breaks and a chapter range, Add Source's accepted types and its .doc
refusal (SPEC-21), an ensemble request
the free tier runs as prose (and says so), a scene paused by forced 429s that
shows on the Usage page and is cancelled, the legacy pages' redirects, the
dashboard, a second author who registers with an invite and cannot see the
first author's book, and the book page at phone width.

Wave 2 (SPEC-23 to SPEC-27): add a person the outline names from the synopsis
("Prava") to the cast with one click, and cast her in a scene; every planned
scene's cast is its beat's cast plus the POV, and no tone is defaulted to
"neutral"; revise a selection with AI and discard it (byte-identical), then
accept one (one more revision), and a stale view is refused with the 409
message (and a hand-edited beat cannot be redone); the import review shows
name mentions and ranks Milo above Zara (SPEC-26); a new book's
getting-started checklist (0/4, then 1/4 after Add source); and the
dashboard's progress and "Resume" link.

Each step asserts what the author should see, and saves a screenshot to
$E2E_SHOTS (default ./e2e-shots) so a failure is diagnosable from CI artifacts.

    BASE_URL=http://localhost:8000 ADMIN_EMAIL=... ADMIN_PASSWORD=... \
      python scripts/e2e_browser.py

Chromium: uses PLAYWRIGHT_CHROMIUM if set (e.g. a preinstalled headless shell),
else Playwright's own browser.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import Page, async_playwright, expect

BASE = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
EMAIL = os.getenv("ADMIN_EMAIL", "admin@e2e.test")
PASSWORD = os.getenv("ADMIN_PASSWORD", "admin-pass-12345")
SHOTS = Path(os.getenv("E2E_SHOTS", "e2e-shots"))
STUB = os.getenv("STUB_URL", "http://127.0.0.1:8099").rstrip("/")
TIMEOUT = 90_000
STATE: dict = {}
# Native dialogs (confirm, beforeunload) are accepted, like an author clicking
# OK, unless a step switches to dismissing them; every one is recorded.
DIALOGS: dict = {"accept": True, "seen": []}
# The server's 415 for an old Word file, which the Add Source dialog also shows
# before uploading (SPEC-21c).
DOC_UNSUPPORTED = (
    "Old Word .doc files aren't supported. Save as .docx and upload again."
)

SYNOPSIS = (
    "Bored to Undeath. Milo Voss, a lich systems analyst, is retained by Aeon "
    "Holdings and onboarded into Reconciliations, where Zara Okafor, an "
    "uncontrollable sorceress, begins recruiting him. The Chief Executive Lich, "
    "Edric Thane, was once a clerk himself. On his first day, Prava, the intake "
    "caseworker, processes his file."
)
# A person the synopsis names who is not in the cast (the source never names
# her): the outline may use her, and offers her as a suggested character.
SECONDARY = "Prava"
SOURCE = """Milo Voss opened his eyes in the intake atrium.

"You've been retained," the caseworker said. Milo Voss nodded slowly.

Zara Okafor, at the next desk, did not look up. "Don't thank her," Zara Okafor said.

Milo Voss read the appendices. Zara Okafor watched him read them.

Edric Thane signed the memo. Edric Thane had signed ten thousand memos.
"""

_step = 0


async def shot(page: Page, name: str) -> None:
    global _step
    _step += 1
    SHOTS.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(SHOTS / f"{_step:02d}-{name}.png"), full_page=True)


async def step(name: str, coro) -> None:
    t = time.monotonic()
    print(f"-> {name}", flush=True)
    await coro
    print(f"   ok ({time.monotonic() - t:.1f}s)", flush=True)


def chapter_row(page: Page, chapter_id: str):
    """The chapter row of `chapter_id`: rows are located by id or position,
    never by title, which two chapters may share (SPEC-32)."""
    return page.locator(f'[data-testid=chapter-row][data-chapter-id="{chapter_id}"]')


def assert_clean_headings(markdown: str) -> list[str]:
    """No chapter heading repeats "Chapter" ("## Chapter 2: Chapter 1: Edric
    Thane" was B9); returns the chapter headings."""
    headings = [line for line in markdown.splitlines() if line.startswith("## ")]
    doubled = [h for h in headings if len(re.findall(r"\bChapter\b", h)) > 1]
    assert not doubled, f"headings repeat 'Chapter': {doubled}"
    bad = [h for h in headings if not re.match(r"^## Chapter \d+(: \S.*)?$", h)]
    assert not bad, f"headings are not 'Chapter N: Title': {bad}"
    return headings


class dismissing_dialogs:
    """Within the block, native dialogs are dismissed (an author clicking Cancel)."""

    def __enter__(self):
        DIALOGS["accept"] = False

    def __exit__(self, *exc):
        DIALOGS["accept"] = True


async def journey(page: Page) -> None:
    page.set_default_timeout(TIMEOUT)

    async def api_headers() -> dict:
        """A bearer token for the admin author, for the API-level checks."""
        if "token" not in STATE:
            login = await page.request.post(
                f"{BASE}/api/v1/auth/login",
                form={"username": EMAIL, "password": PASSWORD},
            )
            assert login.ok, f"API login failed: {login.status}"
            STATE["token"] = (await login.json())["access_token"]
        return {"Authorization": f"Bearer {STATE['token']}"}

    async def api_get(path: str, **params):
        r = await page.request.get(
            f"{BASE}/api/v1{path}", params=params, headers=await api_headers()
        )
        return r

    async def sign_in(p=None, email=EMAIL, password=PASSWORD):
        p = p or page
        await p.goto(f"{BASE}/auth/login/")
        await p.fill("input[name=email]", email)
        await p.fill("input[name=password]", password)
        await p.get_by_role("button", name="Sign In").click()
        await p.wait_for_url(re.compile(r"/(dashboard|books)"))
        await shot(p, "signed-in")

    async def create_book():
        await page.goto(f"{BASE}/books/")
        await page.get_by_role("button", name="New Book").first.click()
        await page.get_by_placeholder("The working title of your book").fill(
            "Bored to Undeath"
        )
        await page.get_by_placeholder(re.compile("short premise")).fill(SYNOPSIS)
        await page.get_by_role("button", name="Create Book").click()
        await page.wait_for_url(re.compile(r"/books/detail"))
        await expect(page.get_by_text("Bored to Undeath").first).to_be_visible()
        STATE["book_url"] = page.url
        STATE["book_id"] = re.search(r"id=([0-9a-f-]+)", page.url).group(1)
        await shot(page, "book-created")

    async def paste_source_and_review():
        await page.get_by_role("button", name="Sources").click()
        await page.get_by_role("button", name="Add source").first.click()
        await page.get_by_role("button", name="Text").click()
        await page.get_by_placeholder("Enter a title for your source").fill(
            "Opening chapter"
        )
        await page.get_by_placeholder(re.compile("Paste your notes")).fill(SOURCE)
        await shot(page, "source-modal")
        await page.get_by_role(
            "button", name=re.compile("^(Add|Save|Extract)")
        ).last.click()
        # Extraction runs as a job; the source surfaces a review when proposals land.
        await page.wait_for_url(re.compile(r"/sources/review"), timeout=TIMEOUT)
        await expect(page.get_by_text("Review extracted canon")).to_be_visible()
        await expect(page.get_by_text("Milo Voss").first).to_be_visible(timeout=TIMEOUT)
        await shot(page, "review")
        # The book already has a synopsis, so the extracted one must NOT be
        # pre-approved — it would silently replace the author's own.
        await expect(page.get_by_test_id("synopsis-replaces")).to_be_visible()
        await page.get_by_role(
            "button", name=re.compile("Commit|Approve|Save to")
        ).first.click()
        await page.wait_for_url(re.compile(r"/books/detail"))
        await expect(page.get_by_text("a lich systems analyst").first).to_be_visible()

    async def canon_has_cast():
        await page.get_by_role("button", name="Canon").click()
        for name in ("Milo Voss", "Zara Okafor"):
            await expect(page.get_by_text(name).first).to_be_visible()
        await shot(page, "canon")

    async def open_voice(name: str):
        """Open `name`'s Voice section on the Canon tab; returns its panel."""
        # The Voice toggle sits beside the character's named History button.
        actions = page.get_by_role("button", name=f"History of {name}", exact=True)
        await actions.locator("xpath=..").get_by_role(
            "button", name="Voice", exact=True
        ).click()
        # The panel opens inside that character's own row (one at a time).
        panel = actions.locator("xpath=../..").get_by_test_id("voice-panel")
        await expect(panel).to_have_count(1)
        await expect(page.get_by_test_id("voice-panel")).to_have_count(1)
        await expect(panel.get_by_text("Loading samples…")).to_have_count(0)
        await expect(
            panel.get_by_role("heading", name=re.compile(r"^Voice samples \(\d+\)$"))
        ).to_be_visible()
        return panel

    async def open_voice_with(name: str, has_text: str = ""):
        """Open `name`'s Voice once the background voice indexing (a job that
        runs after the commit) has given them a sample containing `has_text`.
        The panel loads on open, so it is closed and reopened until then."""
        deadline = time.monotonic() + TIMEOUT / 1000
        while True:
            panel = await open_voice(name)
            chunks = panel.get_by_test_id("voice-chunk").filter(has_text=has_text)
            if await chunks.count() or time.monotonic() > deadline:
                await expect(chunks).not_to_have_count(0)
                return panel
            await page.locator("button[aria-expanded=true]", has_text="Voice").click()
            await page.wait_for_timeout(1000)

    async def voice_is_their_own_words():
        # SPEC-17: a quote is credited only from a tag beside it. Zara's own
        # line is hers...
        await open_voice_with("Zara Okafor", "Don't thank her")
        await shot(page, "voice-zara")
        # ...and neither her line nor the caseworker's is Milo's (who has
        # samples of his own, so this is not vacuous).
        milo = await open_voice_with("Milo Voss")
        samples = await milo.get_by_test_id("voice-chunk").all_inner_texts()
        for line in ("Don't thank her", "You've been retained"):
            wrong = [s for s in samples if line in s]
            assert not wrong, f"Milo Voss's voice has {line!r}: {wrong}"
        await shot(page, "voice-milo")
        await page.locator("button[aria-expanded=true]", has_text="Voice").click()
        await expect(page.get_by_test_id("voice-panel")).to_have_count(0)

    async def add_chapter():
        await page.get_by_role("button", name="Chapters").click()
        await page.get_by_role("button", name="Add Chapter").first.click()
        await page.get_by_placeholder("Chapter title").fill("Onboarding")
        await page.get_by_placeholder(re.compile("What this chapter covers")).fill(
            "Milo's first day in Reconciliations."
        )
        await page.get_by_role("button", name="Add Chapter").last.click()
        row = page.get_by_test_id("chapter-row").first
        await expect(row).to_contain_text("Onboarding")
        STATE["onboarding"] = await row.get_attribute("data-chapter-id")
        await shot(page, "chapter")

    async def generate_scene():
        await page.get_by_role("button", name="Scenes").click()
        await page.get_by_role("button", name="Generate scene").first.click()
        picker = page.get_by_test_id("cast-picker")
        await expect(picker).to_be_visible()
        await picker.get_by_role("button", name=re.compile("Milo Voss")).click()
        await picker.get_by_role("button", name=re.compile("Zara Okafor")).click()
        await page.get_by_placeholder("What happens in this scene...").fill(
            "Zara tells Milo to read the appendices before thanking anyone."
        )
        await page.get_by_placeholder(re.compile("dimly lit tavern")).fill(
            "the intake atrium"
        )
        await shot(page, "scene-modal")
        await page.get_by_role("button", name="Generate", exact=True).click()
        # The scene appears, generates in the background, and completes.
        await expect(
            page.get_by_text(re.compile("completed", re.I)).first
        ).to_be_visible(timeout=TIMEOUT)
        await shot(page, "scene-done")

    async def generate_outline():
        await page.get_by_role("button", name="Outline").click()
        await page.get_by_role("button", name=re.compile("Generate")).first.click()
        # On the free tier the staged outline stops after the skeleton so the
        # author can confirm the premise before chapters are built (SPEC-10).
        checkpoint = page.get_by_test_id("premise-checkpoint")
        promote = page.get_by_text(re.compile("Promote to chapter")).first
        await expect(checkpoint.or_(promote)).to_be_visible(timeout=TIMEOUT)
        if await checkpoint.is_visible():
            await shot(page, "premise")
            await checkpoint.get_by_role("button", name="Looks right").click()
        await expect(promote).to_be_visible(timeout=TIMEOUT)
        body = await page.inner_text("body")
        assert "Milo" in body, "outline does not name the protagonist"
        await shot(page, "outline")
        # Promote all is idempotent: a second click creates nothing new.
        promote_all = page.get_by_role("button", name="Promote all to chapters")
        await promote_all.click()
        await expect(
            page.get_by_text(re.compile(r"\d+ chapters? created from the outline"))
        ).to_be_visible()
        await promote_all.click()
        await expect(
            page.get_by_text("Every outline node already has a chapter")
        ).to_be_visible()
        # SPEC-23: promoted chapters carry their beats as data, a summary with
        # no "Planned beats:" text, and a title with no "Chapter N:" prefix.
        book = await (await api_get(f"/books/{STATE['book_id']}")).json()
        promoted = [c for c in book["chapters"] if c.get("plan_node_id")]
        assert promoted, "promote created no chapters"
        for c in promoted:
            assert "Planned beats:" not in (c.get("summary") or ""), c
            assert not re.match(r"^Chapter\s+\d+\s*[:.\-–]", c["title"]), c
        assert any(c.get("planned_beats") for c in promoted), "no planned_beats"

    async def continuity():
        """SPEC-31: the stub's finding quotes the first scene; "Open in editor"
        opens the editor on that scene."""
        await page.get_by_role("button", name="Continuity").click()
        await page.get_by_role("button", name="Run Check").click()
        link = page.get_by_role("link", name="Open in editor").first
        await expect(link).to_be_visible(timeout=TIMEOUT)
        await shot(page, "continuity")
        scene = parse_qs(urlsplit(await link.get_attribute("href")).query)["scene"][0]
        await link.click()
        await page.wait_for_url(re.compile(rf"/books/editor/?\?.*scene={scene}"))
        await expect(page.get_by_label("Scene text")).not_to_have_value("")
        await page.goto(STATE["book_url"])
        await page.wait_for_load_state("networkidle")

    async def export_md(name: str, include_synopsis: bool = False) -> str:
        """Export through the dialog (B9) and return the Markdown."""
        await page.get_by_role("button", name="Export", exact=True).click()
        dialog = page.get_by_test_id("export-dialog")
        await expect(dialog).to_be_visible()
        await dialog.get_by_role("radio", name=re.compile("Markdown")).check()
        # The defaults an author gets: no synopsis, empty chapters skipped.
        synopsis = dialog.get_by_role("checkbox", name=re.compile("Include synopsis"))
        await expect(synopsis).not_to_be_checked()
        await expect(
            dialog.get_by_role("checkbox", name=re.compile("Skip empty chapters"))
        ).to_be_checked()
        if include_synopsis:
            await synopsis.check()
        await shot(page, f"export-dialog-{name}")
        async with page.expect_download() as dl:
            await dialog.get_by_role("button", name="Download").click()
        download = await dl.value
        await expect(dialog).to_have_count(0)
        path = SHOTS / f"{name}-{download.suggested_filename}"
        await download.save_as(path)
        return path.read_text(encoding="utf-8", errors="replace")

    async def export():
        text = await export_md("default")
        assert "Onboarding" in text, "export is missing the chapter"
        assert "a lich systems analyst" not in text, "synopsis exported by default"
        assert_clean_headings(text)
        text = await export_md("with-synopsis", include_synopsis=True)
        assert "a lich systems analyst" in text, "synopsis missing when asked for"

    async def tab(name: str):
        await page.get_by_role("button", name=name, exact=True).first.click()

    async def toast(text):
        await expect(page.get_by_text(text).first).to_be_visible()

    async def trash_and_restore_character():
        await tab("Canon")
        await page.get_by_role("button", name="Move Zara Okafor to the Trash").click()
        await toast("Character moved to the Trash")
        await tab("Trash")
        await expect(page.get_by_text("Zara Okafor").first).to_be_visible()
        await shot(page, "trash")
        await page.get_by_role("button", name="Restore Zara Okafor").click()
        await toast('Restored "Zara Okafor"')
        await expect(page.get_by_text("The Trash is empty.")).to_be_visible()
        await tab("Canon")
        await expect(page.get_by_text("Zara Okafor").first).to_be_visible()

    async def restore_earlier_synopsis():
        await tab("Canon")
        # The Synopsis card is the first on the Canon tab: its Edit and History
        # are the first of their names there.
        await page.get_by_role("button", name="Edit", exact=True).first.click()
        await page.locator("textarea").first.fill("A heist in a monastery.")
        await page.get_by_role("button", name="Save", exact=True).click()
        # The toast appears only once the save has returned, so the history
        # opened next lists the edit's version (the text alone can show sooner).
        await toast("Synopsis saved")
        await expect(page.get_by_text("A heist in a monastery.").first).to_be_visible()
        await page.get_by_role("button", name="History", exact=True).first.click()
        history = page.get_by_role("dialog")
        await expect(history.get_by_text("History — Synopsis")).to_be_visible()
        await shot(page, "synopsis-history")
        # Restore the version before the edit (the newest is current).
        await history.get_by_role("button", name="Restore").first.click()
        await page.keyboard.press("Escape")
        await expect(page.get_by_text("a lich systems analyst").first).to_be_visible()
        await expect(page.get_by_text("A heist in a monastery.")).to_have_count(0)

    async def regenerate_outline_with_edited_premise():
        await tab("Outline")
        await page.get_by_role("button", name=re.compile("Generate outline")).click()
        checkpoint = page.get_by_test_id("premise-checkpoint")
        await expect(checkpoint).to_be_visible(timeout=TIMEOUT)
        await checkpoint.get_by_role("button", name="Edit and continue").click()
        edited = "Milo Voss audits the afterlife and finds Aeon Holdings insolvent."
        await checkpoint.get_by_label("Premise").fill(edited)
        await shot(page, "premise-edit")
        await checkpoint.get_by_role("button", name="Continue with my edits").click()
        await expect(
            page.get_by_text(re.compile("Promote to chapter")).first
        ).to_be_visible(timeout=TIMEOUT)
        # The author's premise, not the model's, steered the chapters stage.
        r = await page.request.get(f"{STUB}/__stub/requests")
        prompts = " ".join(str(x) for x in (await r.json()).get("requests", []))
        assert edited in prompts, "the edited premise never reached the model"

    async def plot_threads():
        await tab("Threads")
        await page.get_by_placeholder("Thread name, e.g. The stolen locket").fill(
            "The appendices"
        )
        await page.get_by_placeholder("Description (optional)").fill(
            "What Milo finds in them."
        )
        await page.get_by_role("button", name="Add Thread").click()
        await expect(page.get_by_text("The appendices").first).to_be_visible()
        await page.get_by_placeholder("Add an event note...").fill(
            "Zara tells him to read them."
        )
        await page.get_by_role("button", name="Add", exact=True).click()
        await expect(page.get_by_text("Zara tells him to read them.")).to_be_visible()
        await page.get_by_role("button", name="Resolve").click()
        await expect(page.get_by_role("button", name="Reopen")).to_be_visible()
        await shot(page, "threads")

    async def edit_scene_and_load_revision():
        await tab("Chapters")
        row = chapter_row(page, STATE["onboarding"])
        await row.get_by_role("button", name=re.compile("^Expand chapter")).click()
        await row.get_by_role("button", name="Edit", exact=True).first.click()
        await page.wait_for_url(re.compile(r"/books/editor"))
        STATE["editor_url"] = page.url
        STATE["scene_id"] = re.search(r"scene=([0-9a-f-]+)", page.url).group(1)
        editor = page.get_by_placeholder("Write your scene...")
        await expect(editor).not_to_be_empty()
        original = await editor.input_value()
        await editor.fill("Milo closed the appendices. Nobody thanked anyone.")
        await page.get_by_role("button", name="Save").click()
        await expect(page.get_by_text(re.compile("saved"))).to_be_visible()
        await page.get_by_role("button", name=re.compile("^Revision 1")).click()
        await page.get_by_role("button", name="Restore").click()
        await expect(editor).to_have_value(original)
        await shot(page, "editor")
        await page.get_by_role("button", name="Back to book").click()
        await page.wait_for_url(re.compile(r"/books/detail"))

    async def revision_count() -> int:
        r = await api_get(f"/books/scenes/{STATE['scene_id']}/revisions")
        assert r.ok, f"revisions: {r.status}"
        return len((await r.json())["revisions"])

    async def open_editor():
        await page.goto(STATE["editor_url"])
        await page.wait_for_load_state("networkidle")
        editor = page.get_by_label("Scene text")
        await expect(editor).not_to_be_empty()
        return editor

    async def asks_and_stays(leave, editor, text: str):
        """`leave` raises the unsaved-changes confirm; Cancel keeps the editor."""
        seen = len(DIALOGS["seen"])
        with dismissing_dialogs():
            async with page.expect_event("dialog"):
                await leave()
            await page.wait_for_load_state("networkidle")
        kind, message = DIALOGS["seen"][seen]
        assert "unsaved changes" in message, f"unexpected {kind} dialog: {message!r}"
        assert "/books/editor" in page.url, f"left the editor: {page.url}"
        await expect(page.get_by_role("heading", name="Scene Editor")).to_be_visible()
        await expect(editor).to_have_value(text)

    async def editor_safety_net():
        """SPEC-16: unsaved text is guarded, kept as a local draft, offered back."""
        banner = page.get_by_text(re.compile(r"Restore unsaved draft from \d\d:\d\d\?"))
        editor = await open_editor()
        # The last step left with the loaded revision unsaved (and said OK):
        # that text is offered back. Discarding it leaves the saved text.
        await expect(banner).to_be_visible()
        saved = await editor.input_value()
        await page.get_by_role("button", name="Discard draft", exact=True).click()
        await expect(banner).to_have_count(0)
        await expect(editor).to_have_value(saved)

        typed = "Milo reread the appendices twice. The second time, they read him."
        await editor.click()  # a real gesture: Back must stop at the guard's entry
        await editor.fill("")
        await editor.press_sequentially(typed)
        await expect(page.get_by_text("• unsaved changes")).to_be_visible()
        back = page.get_by_role("button", name="Back to book")
        await asks_and_stays(back.click, editor, typed)
        await asks_and_stays(page.go_back, editor, typed)
        await shot(page, "editor-guard")

        # OK leaves, and keeps the draft on this device.
        await back.click()
        await page.wait_for_url(re.compile(r"/books/detail"))
        editor = await open_editor()
        await page.reload()
        await page.wait_for_load_state("networkidle")
        await expect(banner).to_be_visible()
        await expect(editor).to_have_value(saved)  # nothing restored unasked
        await shot(page, "editor-draft-banner")
        await page.get_by_role("button", name="Restore draft", exact=True).click()
        await expect(banner).to_have_count(0)
        await expect(editor).to_have_value(typed)

        # Saving makes exactly one revision, persists, and drops the draft.
        before = await revision_count()
        await page.get_by_role("button", name="Save", exact=True).click()
        await toast("Scene saved")
        await page.reload()
        await page.wait_for_load_state("networkidle")
        await expect(editor).to_have_value(typed)
        await expect(banner).to_have_count(0)
        after = await revision_count()
        assert after == before + 1, f"revisions went {before} -> {after}, not +1"

        # The revision preview is a modal dialog: Escape closes it and focus
        # returns to the revision that opened it.
        revision = page.get_by_role("button", name=re.compile(r"^Revision 1(?!\d)"))
        await revision.click()
        preview = page.get_by_role("dialog", name="Revision preview")
        await expect(preview).to_be_visible()
        await shot(page, "revision-preview")
        await page.keyboard.press("Escape")
        await expect(preview).to_have_count(0)
        await expect(revision).to_be_focused()

        # Clean now: leaving asks nothing.
        seen = len(DIALOGS["seen"])
        await back.click()
        await page.wait_for_url(re.compile(r"/books/detail"))
        assert len(DIALOGS["seen"]) == seen, "a saved editor still asked to leave"

    async def reorder_delete_and_restore_chapter():
        await tab("Chapters")
        rows = page.get_by_test_id("chapter-row")
        first = await rows.first.get_attribute("data-chapter-id")
        await rows.first.get_by_role(
            "button", name=re.compile(r"^Move chapter 1, .* down$")
        ).click()
        await expect(rows.nth(1)).to_have_attribute("data-chapter-id", first)
        # Delete the last chapter (a promoted one, no scenes) and restore it.
        count = await rows.count()
        last_id = await rows.last.get_attribute("data-chapter-id")
        # The Trash names it by its stored title (the row's title attribute).
        last = await rows.last.get_by_test_id("chapter-title").get_attribute("title")
        await page.get_by_role(
            "button", name=re.compile(rf"^Delete chapter {count}, ")
        ).click()
        await page.get_by_role("dialog").get_by_role(
            "button", name="Delete chapter", exact=True
        ).click()
        await expect(rows).to_have_count(count - 1)
        await tab("Trash")
        await page.get_by_role("button", name=f"Restore {last}").click()
        await toast(f'Restored "{last}"')
        await tab("Chapters")
        await expect(rows).to_have_count(count)
        await expect(chapter_row(page, last_id)).to_have_count(1)
        await shot(page, "chapters")

    async def generate_all_planned_scenes():
        """Beat 1 already has its scene (written from the modal, linked by
        id), so the batch writes beat 2 only and skips beat 1: one scene per
        beat, no duplicate (SPEC-23, review B3)."""
        await tab("Chapters")
        button_name = "Generate all planned scenes"
        row = chapter_row(page, STATE["planned_chapter"])
        await row.get_by_role("button", name=button_name).click()
        await toast("Writing 1 planned scene in order (1 already written, skipped).")
        # The scenes are a chain: each is created when the one before completes.
        edits = row.get_by_role("button", name="Edit", exact=True)
        await expect(edits).to_have_count(2, timeout=TIMEOUT)
        await expect(page.get_by_test_id("job-status")).to_have_count(
            0, timeout=TIMEOUT
        )
        # Clicking again writes nothing: every beat already has its scene.
        await row.get_by_role("button", name=button_name).click()
        await toast("Every planned beat in this chapter already has a scene.")
        await expect(edits).to_have_count(2)
        book = await (await api_get(f"/books/{STATE['book_id']}")).json()
        chapter = next(
            c for c in book["chapters"] if c["id"] == STATE["planned_chapter"]
        )
        detail = await (await api_get(f"/books/chapters/{chapter['id']}")).json()
        linked = []
        for summary in detail["scenes"]:
            scene = await (await api_get(f"/scenes/{summary['id']}")).json()
            linked.append((scene.get("scene_request") or {}).get("planned_beat_id"))
        ids = [b["node_id"] for b in chapter["planned_beats"]]
        assert sorted(x for x in linked if x) == sorted(ids), (linked, ids)
        await shot(page, "planned-scenes")

    async def export_markdown(**params) -> str:
        r = await api_get(f"/books/{STATE['book_id']}/export", format="md", **params)
        assert r.ok, f"export {params}: {r.status} {await r.text()}"
        return await r.text()

    async def export_is_a_clean_manuscript():
        """SPEC-21a over the API: clean headings numbered by book position, a
        scene break between a chapter's scenes, and a chapter range."""
        book = await (await api_get(f"/books/{STATE['book_id']}")).json()
        ids = [c["id"] for c in book["chapters"]]
        n = ids.index(STATE["planned_chapter"]) + 1  # 1-based book position
        text = await export_markdown()
        headings = assert_clean_headings(text)
        assert len(headings) >= 2, f"expected the written chapters: {headings}"
        assert "\n* * *\n" in text, "no scene break between a chapter's scenes"
        assert not re.search(r"## [^\n]*\n+\* \* \*", text), "a break opens a chapter"
        # Only the planned chapter, still numbered by its place in the book.
        part = await export_markdown(from_chapter=n, to_chapter=n)
        only = assert_clean_headings(part)
        assert len(only) == 1 and only[0].startswith(
            f"## Chapter {n}"
        ), f"range {n}-{n} exported {only}"
        assert "\n* * *\n" in part, "the ranged chapter lost its scene break"
        assert len(part) < len(text), "the range exported the whole book"
        bad = await api_get(
            f"/books/{STATE['book_id']}/export",
            format="md",
            from_chapter=n + 1,
            to_chapter=n,
        )
        assert bad.status == 422, f"from > to answered {bad.status}"
        (SHOTS / f"export-chapter-{n}.md").write_text(part, encoding="utf-8")

    async def add_source_refuses_doc():
        """SPEC-21c: the upload picker accepts what the server accepts, and an
        old .doc is turned away with the fix, in the dialog and by the API."""
        config = await (await page.request.get(f"{BASE}/api/v1/sources/config")).json()
        accept = ",".join(config["accepted_extensions"])
        assert ".doc" not in config["accepted_extensions"], config
        await tab("Sources")
        await page.get_by_role("button", name="Add source").first.click()
        modal = page.get_by_role("dialog", name="Add Source")
        await expect(modal).to_be_visible()
        picker = modal.locator("input[type=file]")
        await expect(picker).to_have_attribute("accept", accept)
        await modal.get_by_placeholder("Enter a title for your source").fill(
            "Old draft"
        )
        await picker.set_input_files(
            files=[
                {
                    "name": "old-draft.doc",
                    "mimeType": "application/msword",
                    "buffer": b"\xd0\xcf\x11\xe0 not really a Word file",
                }
            ]
        )
        await expect(modal.get_by_text(DOC_UNSUPPORTED)).to_be_visible()
        await expect(
            modal.get_by_role("button", name="Upload", exact=True)
        ).to_be_disabled()
        await shot(page, "add-source-doc")
        await modal.get_by_role("button", name="Cancel").click()
        await expect(modal).to_have_count(0)
        r = await page.request.post(
            f"{BASE}/api/v1/sources/upload",
            params={"book_id": STATE["book_id"], "title": "Old draft"},
            multipart={
                "file": {
                    "name": "old-draft.doc",
                    "mimeType": "application/msword",
                    "buffer": b"\xd0\xcf\x11\xe0 not really a Word file",
                }
            },
            headers=await api_headers(),
        )
        assert r.status == 415, f".doc upload answered {r.status}"
        assert (await r.json())["detail"] == DOC_UNSUPPORTED, await r.text()

    async def open_scene_modal():
        await tab("Scenes")
        await page.get_by_role("button", name="Generate scene").first.click()
        picker = page.get_by_test_id("cast-picker")
        milo = picker.get_by_role("button", name=re.compile("Milo Voss"))
        # A promoted chapter pre-selects its cast (SPEC-10): select, don't toggle.
        await expect(milo).to_be_visible()
        if await milo.get_attribute("aria-pressed") != "true":
            await milo.click()
        await expect(milo).to_have_attribute("aria-pressed", "true")
        await page.get_by_placeholder("What happens in this scene...").fill(
            "Milo files his first reconciliation and it balances."
        )
        await page.get_by_placeholder(re.compile("dimly lit tavern")).fill(
            "Reconciliations"
        )

    async def ensemble_runs_as_prose_and_says_so():
        await open_scene_modal()
        await page.get_by_label(re.compile("^Ensemble")).check()
        await expect(page.get_by_test_id("estimate")).to_contain_text("runs as prose")
        await shot(page, "ensemble-estimate")
        await page.get_by_role("button", name="Generate", exact=True).click()
        await toast(re.compile("Ensemble is not available on the free tier"))
        # It still writes the scene: wait for it, so the next step's quota storm
        # pauses only the scene it starts.
        await expect(page.get_by_test_id("job-status")).to_have_count(
            0, timeout=TIMEOUT
        )

    async def paused_scene_on_usage_then_cancel():
        # Every model call now answers 429: the scene's job pauses for quota.
        # A long Retry-After keeps the job paused (not flapping between paused
        # and resumed every ~2 s), so the badge's poll reliably sees "paused".
        await page.request.post(
            f"{STUB}/__stub/config", data={"fail_429": 10_000, "retry_after": 30}
        )
        try:
            await open_scene_modal()
            await page.get_by_role("button", name="Generate", exact=True).click()
            badge = page.get_by_test_id("job-status").first
            await expect(badge).to_contain_text("paused", timeout=TIMEOUT)
            # The row says why it is waiting, not "Generating…".
            await expect(
                page.get_by_text(re.compile("Paused — daily AI quota reached")).first
            ).to_be_visible()
            await shot(page, "paused")
            await page.get_by_role("link", name="Usage").first.click()
            await expect(page.get_by_test_id("usage-page")).to_be_visible()
            await expect(page.get_by_test_id("active-jobs")).to_be_visible()
            await expect(page.get_by_text("Calls").first).to_be_visible()
            await shot(page, "usage")
            await page.goto(STATE["book_url"])
            await tab("Scenes")
            badge = page.get_by_test_id("job-status").first
            await expect(page.get_by_test_id("job-status")).to_have_count(1)
            await badge.get_by_role("button", name="Cancel").click()
            await expect(page.get_by_test_id("job-status")).to_have_count(
                0, timeout=TIMEOUT
            )
            await expect(
                page.get_by_text("cancelled", exact=True).first
            ).to_be_visible()
        finally:
            await page.request.post(f"{STUB}/__stub/config", data={"fail_429": 0})

    async def legacy_pages_redirect_and_dashboard():
        for path in ("characters", "generate", "sources", "sources/detail"):
            await page.goto(f"{BASE}/{path}/")
            await page.wait_for_url(re.compile(r"/books/?$"))
        await page.goto(f"{BASE}/dashboard/")
        await expect(page.get_by_role("heading", name="Dashboard")).to_be_visible()
        await expect(page.get_by_text("Bored to Undeath").first).to_be_visible()

    async def second_author_is_isolated():
        login = await page.request.post(
            f"{BASE}/api/v1/auth/login",
            form={"username": EMAIL, "password": PASSWORD},
        )
        token = (await login.json())["access_token"]
        invite = await page.request.post(
            f"{BASE}/api/v1/auth/invites",
            data={"max_uses": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        code = (await invite.json())["code"]
        other = await page.context.browser.new_context()
        p2 = await other.new_page()
        try:
            await p2.goto(f"{BASE}/auth/register/")
            await p2.fill("input[name=fullName]", "Second Author")
            await p2.fill("input[name=email]", "second-author@example.com")
            await p2.fill("input[name=password]", "second-pass-12345")
            await p2.fill("input[name=confirmPassword]", "second-pass-12345")
            await p2.fill("input[name=inviteCode]", code)
            await p2.get_by_role(
                "button", name=re.compile("Create|Register|Sign up")
            ).click()
            await p2.wait_for_url(re.compile(r"/dashboard"))
            await p2.goto(f"{BASE}/books/")
            await expect(p2.get_by_text("Bored to Undeath")).to_have_count(0)
            await p2.goto(STATE["book_url"])
            await expect(p2.get_by_text("Book not found").first).to_be_visible()
            await p2.screenshot(path=str(SHOTS / "second-author.png"), full_page=True)
        except Exception:
            await p2.screenshot(path=str(SHOTS / "second-author-FAILED.png"))
            print((await p2.inner_text("body"))[:1500], file=sys.stderr)
            raise
        finally:
            await other.close()

    async def a_batch_over_budget_writes_what_fits():
        """SPEC-34: the second author (no spend yet; registering a third would
        pass the per-IP limit) plans a four-beat chapter on a long synopsis."""
        who = ("second-author@example.com", "second-pass-12345")
        ctx = await page.context.browser.new_context()
        p4 = await ctx.new_page()
        try:
            login = await p4.request.post(
                f"{BASE}/api/v1/auth/login",
                form={"username": who[0], "password": who[1]},
            )
            auth = {"Authorization": f"Bearer {(await login.json())['access_token']}"}

            async def call(method, path, body):
                r = await p4.request.fetch(
                    f"{BASE}/api/v1{path}", method=method, data=body, headers=auth
                )
                assert r.ok, f"{path}: {r.status} {await r.text()}"
                return await r.json()

            book = await call(
                "POST", "/books/", {"title": "L", "synopsis": SYNOPSIS * 180}
            )
            milo = await call(
                "POST", "/characters/", {"name": "Milo", "book_id": book["id"]}
            )
            ch = await call("POST", f"/books/{book['id']}/chapters", {"title": "Q4"})
            beats = [
                {
                    "node_id": f"00000000-0000-4000-8000-00000000000{i}",
                    "title": f"Beat {i}",
                    "character_ids": [milo["id"]],
                }
                for i in range(4)
            ]
            await call("PATCH", f"/books/chapters/{ch['id']}", {"planned_beats": beats})
            await sign_in(p4, *who)
            await p4.goto(f"{BASE}/books/detail?id={book['id']}")
            await p4.get_by_role("button", name="Chapters", exact=True).first.click()
            async with p4.expect_response(re.compile("generate-planned$")) as resp:
                await chapter_row(p4, ch["id"]).get_by_role(
                    "button", name="Generate all planned scenes"
                ).click()
            started = await (await resp.value).json()
            fit = re.compile(r"\d of 4 scenes fit your budget now")
            await expect(p4.get_by_text(fit).first).to_be_visible()
            assert started["deferred"] >= 1, started
        finally:
            await ctx.close()

    async def book_page_at_phone_width():
        # Its own sign-in, not a copy of this page's cookies: refreshing a
        # copied refresh token rotates it and signs the main page out.
        phone = await page.context.browser.new_context(
            viewport={"width": 390, "height": 844}
        )
        p3 = await phone.new_page()
        try:
            await sign_in(p3)
            await p3.goto(STATE["book_url"])
            await expect(p3.get_by_text("Bored to Undeath").first).to_be_visible()
            # The tab strip says it scrolls (B4).
            await expect(p3.get_by_test_id("tabs-more-right")).to_be_visible()
            await chapter_row(p3, STATE["onboarding"]).get_by_role(
                "button", name=re.compile("^Expand chapter")
            ).click()
            await expect(p3.get_by_test_id("scene-row").first).to_be_visible()
            await shot(p3, "phone-chapters")
            problems = await p3.evaluate(PHONE_CHAPTER_CHECK)
            assert not problems, "phone chapter rows:\n  " + "\n  ".join(problems)
            # Secondary row actions live in a "⋯" menu that fits on screen.
            row = p3.get_by_test_id("chapter-row").last
            await row.get_by_role(
                "button", name=re.compile("^More actions for")
            ).click()
            menu = p3.get_by_role("menu")
            await expect(menu.get_by_role("menuitem").first).to_be_focused()
            await expect(
                menu.get_by_role("menuitem", name="Delete chapter")
            ).to_be_visible()
            box = await menu.bounding_box()
            assert (
                box and box["x"] >= 0 and box["x"] + box["width"] <= 390 + 1
            ), f"row menu is off screen: {box}"
            await shot(p3, "phone-row-menu")
            await p3.keyboard.press("Escape")
            await expect(menu).to_have_count(0)

            tabs = ("Chapters", "Scenes", "Sources", "Canon", "Outline", "Threads")
            for name in (*tabs, "Continuity", "Trash", "Usage"):
                if name == "Usage":
                    await p3.goto(f"{BASE}/usage/")
                    await expect(p3.get_by_test_id("usage-page")).to_be_visible()
                else:
                    await p3.get_by_role("button", name=name, exact=True).first.click()
                    await assert_active_tab_in_view(p3, name)
                overflow = await p3.evaluate(
                    "document.documentElement.scrollWidth - window.innerWidth"
                )
                assert overflow <= 1, f"{name}: page scrolls sideways by {overflow}px"
            # A deep link to the last tab opens with that tab scrolled into view.
            await p3.goto(STATE["book_url"] + "&tab=trash")
            await expect(p3.get_by_text("Bored to Undeath").first).to_be_visible()
            await assert_active_tab_in_view(p3, "Trash")
            await expect(p3.get_by_test_id("tabs-more-left")).to_be_visible()
            await p3.screenshot(path=str(SHOTS / "phone.png"), full_page=True)
        finally:
            await phone.close()

    async def add_suggested_character():
        """SPEC-24/27: the outline names Prava from the synopsis; one click adds
        her to the cast as a minor character, and scenes can cast her."""
        await tab("Outline")
        chip = page.get_by_role("button", name=f"Add {SECONDARY} to cast")
        await expect(chip).to_be_visible()
        await shot(page, "suggested-cast")
        await chip.click()
        await toast(f"{SECONDARY} added — now castable")
        # Suggestions are computed at read time: she is cast now, so no chip.
        await expect(chip).to_have_count(0)
        cast = await (await api_get("/characters/", book_id=STATE["book_id"])).json()
        prava = [c for c in cast["characters"] if c["name"] == SECONDARY]
        assert prava, f"{SECONDARY} is not in the cast"
        assert prava[0].get("role") == "minor", prava[0]
        assert "intake caseworker" in (prava[0].get("description") or ""), prava[0]
        await tab("Scenes")
        await page.get_by_role("button", name="Generate scene").first.click()
        picker = page.get_by_test_id("cast-picker")
        await expect(
            picker.get_by_role("button", name=re.compile(SECONDARY))
        ).to_be_visible()
        await shot(page, "suggested-cast-castable")
        await page.get_by_role("button", name="Cancel", exact=True).click()
        await expect(picker).to_have_count(0)

    async def planned_scenes_follow_their_beats():
        """SPEC-23/24: each planned scene was asked for with its own beat's cast
        (else the chapter's) plus the POV character, and no tone was defaulted
        to "neutral"."""
        book = await (await api_get(f"/books/{STATE['book_id']}")).json()
        chapter = next(
            c for c in book["chapters"] if c["id"] == STATE["planned_chapter"]
        )
        beats = {b["node_id"]: b for b in chapter.get("planned_beats") or []}
        assert beats, f"chapter {chapter['title']!r} has no planned_beats"
        cast = (await (await api_get("/characters/", book_id=STATE["book_id"])).json())[
            "characters"
        ]
        live = {c["id"] for c in cast}
        by_name = {c["name"].casefold(): c["id"] for c in cast}
        detail = await (await api_get(f"/books/chapters/{chapter['id']}")).json()
        scenes = [s for s in detail["scenes"] if s["status"] == "completed"]
        assert len(scenes) >= 2, f"planned scenes: {scenes}"
        titles = []
        for summary in scenes:
            scene = await (await api_get(f"/scenes/{summary['id']}")).json()
            req = scene.get("scene_request") or {}
            beat = beats.get(req.get("planned_beat_id"))
            if beat is None:
                continue  # a scene written by hand in this chapter
            titles.append(beat["title"])
            want = set(beat.get("character_ids") or [])
            want |= {
                by_name[n.casefold()]
                for n in beat.get("secondary_names") or []
                if n.casefold() in by_name
            }
            want = (want or set(chapter.get("character_ids") or [])) & live
            if chapter.get("pov_character_id") in live:
                want.add(chapter["pov_character_id"])
            got = set(req.get("character_ids") or [])
            assert got == want, (
                f"beat {beat['title']!r}: cast {sorted(got)} != beat cast + POV "
                f"{sorted(want)}"
            )
            assert (req.get("emotional_tone") or "").lower() != "neutral", req
        assert len(titles) >= 2, f"scenes not tied to their beats: {scenes}"
        r = await page.request.get(f"{STUB}/__stub/requests", params={"limit": 200})
        prompts = [x.get("prompt", "") for x in (await r.json())["requests"]]
        mine = [p for p in prompts if any(t in p for t in titles)]
        assert mine, "no prompt carried a planned beat"
        neutral = [
            p for p in mine if re.search(r"(?im)^\s*Emotional tone:\s*neutral", p)
        ]
        assert not neutral, f"{len(neutral)} planned-scene prompts defaulted to neutral"

    async def scene_from_a_planned_beat():
        """SPEC-23 (review S7): the single-scene modal picks beat 1 with its
        tone and cast, and the scene is linked to the beat by its id."""
        await tab("Chapters")
        rows = page.get_by_test_id("chapter-row")
        planned = rows.filter(
            has=page.get_by_role("button", name="Generate all planned scenes")
        ).first
        STATE["planned_chapter"] = await planned.get_attribute("data-chapter-id")
        book = await (await api_get(f"/books/{STATE['book_id']}")).json()
        chapter = next(
            c for c in book["chapters"] if c["id"] == STATE["planned_chapter"]
        )
        assert len(chapter["planned_beats"]) == 2, chapter["planned_beats"]
        beat = chapter["planned_beats"][0]
        cast = (await (await api_get("/characters/", book_id=STATE["book_id"])).json())[
            "characters"
        ]
        names = {c["id"]: c["name"] for c in cast}
        # The server publishes the beat's cast (SPEC-32): the modal shows it
        # and sends exactly it.
        want = beat["cast_ids"]
        assert want and set(want) <= set(names), (want, names)
        detail = await (await api_get(f"/books/chapters/{chapter['id']}")).json()
        before = {s["id"] for s in detail["scenes"]}

        await tab("Chapters")
        row = chapter_row(page, STATE["planned_chapter"])
        await row.get_by_role("button", name="Generate scene").click()
        picker = page.get_by_test_id("cast-picker")
        await expect(picker).to_be_visible()
        await page.get_by_test_id("planned-beats").get_by_role("button").first.click()
        tone = page.locator("select").filter(has=page.locator('option[value="tense"]'))
        assert await tone.input_value() == (
            beat.get("tone") or ""
        ), f"tone {await tone.input_value()!r} is not beat 1's {beat.get('tone')!r}"
        for i in want:
            await expect(
                picker.get_by_role("button", name=re.compile(re.escape(names[i])))
            ).to_have_attribute("aria-pressed", "true")
        setting = page.get_by_placeholder(re.compile("dimly lit tavern"))
        if not (await setting.input_value()).strip():
            await setting.fill("Reconciliations")
        await shot(page, "scene-from-beat")
        await page.get_by_role("button", name="Generate", exact=True).click()
        await expect(picker).to_have_count(0)

        new: list = []
        deadline = time.monotonic() + TIMEOUT / 1000
        while not new and time.monotonic() < deadline:
            detail = await (await api_get(f"/books/chapters/{chapter['id']}")).json()
            new = [s for s in detail["scenes"] if s["id"] not in before]
            if not new:
                await asyncio.sleep(0.5)
        assert new, "the modal created no scene"
        await expect(page.get_by_test_id("job-status")).to_have_count(
            0, timeout=TIMEOUT
        )
        scene = await (await api_get(f"/scenes/{new[0]['id']}")).json()
        req = scene.get("scene_request") or {}
        assert (
            req.get("planned_beat_id") == beat["node_id"]
        ), f"scene request {req} is not linked to beat {beat['node_id']}"
        if beat.get("tone"):
            assert req.get("emotional_tone") == beat["tone"], req
        assert req.get("character_ids") == want, (req, want)

    async def redo_a_planned_beat():
        """SPEC-25 / review eval finding 7: "Redo beat 2" on a planned scene
        asks for a revision of that beat and opens a proposal."""
        book = await (await api_get(f"/books/{STATE['book_id']}")).json()
        chapter = next(
            c for c in book["chapters"] if c["id"] == STATE["planned_chapter"]
        )
        detail = await (await api_get(f"/books/chapters/{chapter['id']}")).json()
        scene_id = None
        for summary in detail["scenes"]:
            scene = await (await api_get(f"/scenes/{summary['id']}")).json()
            if (scene.get("scene_request") or {}).get("planned_beat_id"):
                scene_id = summary["id"]
                break
        assert scene_id, "no planned scene to redo a beat of"
        await page.goto(f"{BASE}/books/editor/?scene={scene_id}")
        await page.wait_for_load_state("networkidle")
        panel = page.get_by_test_id("revise-panel")
        review = panel.get_by_test_id("revise-review")
        await panel.get_by_role("button", name="Redo beat 2", exact=True).click()
        await panel.get_by_label("What should change?").fill("Make it colder.")
        await panel.get_by_role("button", name="Ask for a revision").click()
        alert = panel.get_by_role("alert")
        await expect(review.or_(alert)).to_be_visible(timeout=TIMEOUT)
        if await alert.is_visible():
            # The stub may write identical prose for two beats; the server then
            # refuses to guess which copy is beat 2 (SPEC-19), and says so.
            await expect(alert).to_contain_text("appears more than once")
            print("  redo beat 2: refused, the beat's prose appears more than once")
            await panel.get_by_role("button", name="Cancel", exact=True).click()
        else:
            await expect(review).to_contain_text("(beat 2)")
            data = await (await api_get(f"/books/scenes/{scene_id}/proposals")).json()
            assert data["proposals"][0].get("beat_index") == 1, data["proposals"][0]
            # E (eval finding 7): the revise prompt names the beat's plan.
            scene = await (await api_get(f"/scenes/{scene_id}")).json()
            second = sorted(scene["beats"], key=lambda b: b["beat_number"])[1]
            plan = " ".join((second.get("description") or "").split())
            r = await page.request.get(f"{STUB}/__stub/requests", params={"limit": 200})
            prompts = [x.get("prompt", "") for x in (await r.json())["requests"]]
            line = f"Beat 2 of this scene's plan: {plan}"
            assert any(line in p for p in prompts), f"no revise prompt has {line!r}"
            print("  redo beat 2: proposal opened; the prompt names beat 2's plan")
            await shot(page, "redo-beat")
            await review.get_by_role("button", name="Discard").click()
            await expect(review).to_have_count(0)
        await page.goto(STATE["book_url"])
        await page.wait_for_load_state("networkidle")

    # An emoji (two UTF-16 units, one code point) and an em dash sit before the
    # phrase, so a selection offset sent in the wrong unit lands on wrong words.
    REVISE_TEXT = (
        "Milo \U0001f4ce reread the appendices \u2014 twice. "
        "The second time, they read him."
    )
    REVISE_PHRASE = "The second time, they read him."

    async def ask_to_revise(
        editor, panel, instruction: str, click: bool = True, phrase=REVISE_PHRASE
    ):
        """Select `phrase` in the textarea, open "Revise selection…" on it, and
        (unless `click` is False) ask for the revision."""
        await editor.evaluate(
            "(el, p) => { const i = el.value.indexOf(p); el.focus();"
            " el.setSelectionRange(i, i + p.length) }",
            phrase,
        )
        await panel.get_by_role("button", name=re.compile("^Revise selection")).click()
        await panel.get_by_label("What should change?").fill(instruction)
        if click:
            await panel.get_by_role("button", name="Ask for a revision").click()

    async def revise_discard_then_accept():
        """W2-3 (SPEC-25): a selection after an emoji and an em dash is revised
        as a side-by-side diff; Discard leaves the text byte-identical, Accept
        saves the proposal and adds exactly one revision."""
        editor = await open_editor()
        panel = page.get_by_test_id("revise-panel")
        review = panel.get_by_test_id("revise-review")
        await editor.fill(REVISE_TEXT)
        await page.get_by_role("button", name="Save", exact=True).click()
        await expect(page.get_by_text("• unsaved changes")).to_have_count(0)
        await toast("Scene saved")
        before_text = await editor.input_value()
        before = await revision_count()

        await ask_to_revise(editor, panel, "Make it colder.")
        await expect(review).to_be_visible(timeout=TIMEOUT)
        # The stub's revision only drops words, so the diff shows deletions
        # (a real model's rewrite also shows `diff-insert`).
        await expect(review.get_by_test_id("diff-view")).to_be_visible()
        await expect(review.get_by_test_id("diff-delete").first).to_be_visible()
        await shot(page, "revise-diff")
        await review.get_by_role("button", name="Discard").click()
        await expect(review).to_have_count(0)
        assert await editor.input_value() == before_text, "discard changed the text"
        scene = await (await api_get(f"/scenes/{STATE['scene_id']}")).json()
        assert scene["content"] == before_text, "discard changed the saved scene"
        assert await revision_count() == before, "discard made a revision"

        await ask_to_revise(editor, panel, "Make it colder.")
        await expect(review).to_be_visible(timeout=TIMEOUT)
        # The server revised exactly the selected words: its range, in Python
        # code points of the draft, slices to the phrase.
        data = await (
            await api_get(f"/books/scenes/{STATE['scene_id']}/proposals")
        ).json()
        newest = data["proposals"][0]
        start, end = newest["range"]
        assert (
            data["draft"][start:end] == REVISE_PHRASE
        ), f"revised {data['draft'][start:end]!r}, not the selection"
        await review.get_by_role("button", name="Accept").click()
        await expect(review).to_have_count(0)
        accepted = await editor.input_value()
        assert accepted == newest["content"], "the editor is not the proposal"
        assert accepted != before_text, "accept left the text unchanged"
        await expect(page.get_by_text("• unsaved changes")).to_have_count(0)
        scene = await (await api_get(f"/scenes/{STATE['scene_id']}")).json()
        assert scene["content"] == accepted, "accept was not saved"
        after = await revision_count()
        assert after == before + 1, f"revisions went {before} -> {after} on accept"
        await shot(page, "revise-accepted")

    async def revise_stale_view_is_refused():
        """W2-3: a beat whose prose was edited is refused (select the text
        instead), and a draft changed under the editor is a 409 the panel
        explains, rather than revising the wrong words."""
        editor = await open_editor()
        panel = page.get_by_test_id("revise-panel")
        alert = panel.get_by_role("alert")
        redo = panel.get_by_role("button", name=re.compile(r"^Redo beat \d+$"))
        if await redo.count():
            # The scene's text was rewritten by hand above, so no beat's
            # generated prose is still in the draft.
            await redo.first.click()
            await panel.get_by_label("What should change?").fill("Tighten it.")
            await panel.get_by_role("button", name="Ask for a revision").click()
            await expect(alert).to_contain_text(
                "The beat was edited; select the text instead."
            )
            await panel.get_by_role("button", name="Cancel", exact=True).click()
        # The accepted revision rewrote REVISE_PHRASE; "Milo" is still there.
        await ask_to_revise(editor, panel, "Make it warmer.", False, "Milo")
        # Another tab saves a different draft meanwhile.
        r = await page.request.put(
            f"{BASE}/api/v1/books/scenes/{STATE['scene_id']}/content",
            data={"content": "Saved elsewhere. The appendices were never read."},
            headers=await api_headers(),
        )
        assert r.ok, f"saving elsewhere: {r.status}"
        await panel.get_by_role("button", name="Ask for a revision").click()
        await expect(alert).to_contain_text(
            "The scene changed; reselect and try again."
        )
        await shot(page, "revise-stale")
        await page.goto(STATE["book_url"])
        await page.wait_for_load_state("networkidle")

    async def review_ranks_by_mentions():
        """SPEC-26: the import review shows each character's name mentions,
        and ranks the protagonist above the others (a scratch book, so the
        journey's own book is untouched)."""
        headers = await api_headers()
        book = await page.request.post(
            f"{BASE}/api/v1/books/", data={"title": "Review check"}, headers=headers
        )
        assert book.ok, f"scratch book: {book.status}"
        book_id = (await book.json())["id"]
        pasted = await page.request.post(
            f"{BASE}/api/v1/sources/paste",
            data={"title": "Opening", "content_text": SOURCE, "book_id": book_id},
            headers=headers,
        )
        assert pasted.ok, f"paste: {pasted.status} {await pasted.text()}"
        run = (await pasted.json())["extraction_run_id"]
        await page.goto(f"{BASE}/sources/review?book={book_id}&run={run}")
        await expect(page.get_by_text("Review extracted canon")).to_be_visible()
        names = page.get_by_placeholder("Name", exact=True)
        await expect(names.first).to_be_visible(timeout=TIMEOUT)
        await expect(
            page.get_by_text(re.compile(r"· \d+ name mentions")).first
        ).to_be_visible()
        order = [await n.input_value() for n in await names.all()]
        assert "Milo Voss" in order and "Zara Okafor" in order, order
        assert order.index("Milo Voss") < order.index("Zara Okafor"), order
        await shot(page, "review-mentions")
        await page.goto(STATE["book_url"])
        await page.wait_for_load_state("networkidle")

    async def new_book_checklist():
        """B12: a new book shows the getting-started checklist, ticked from
        real data: 0/4, then 1/4 once it has a source."""
        await page.goto(f"{BASE}/books/")
        await page.get_by_role("button", name="New Book").first.click()
        await page.get_by_placeholder("The working title of your book").fill(
            "Empty Shelf"
        )
        await page.get_by_role("button", name="Create Book").click()
        await page.wait_for_url(re.compile(r"/books/detail"))
        url = STATE["empty_url"] = page.url
        checklist = page.get_by_test_id("getting-started")
        progress = page.get_by_test_id("getting-started-progress")
        await expect(progress).to_have_text("0/4 done")
        await shot(page, "getting-started-0")
        await checklist.get_by_role("button", name="Add your first source").click()
        modal = page.get_by_role("dialog", name="Add Source")
        await modal.get_by_role("button", name="Text").click()
        await modal.get_by_placeholder("Enter a title for your source").fill("Notes")
        await modal.get_by_placeholder(re.compile("Paste your notes")).fill(SOURCE)
        await modal.get_by_role(
            "button", name=re.compile("^(Add|Save|Extract)")
        ).last.click()
        await page.wait_for_url(re.compile(r"/sources/review"), timeout=TIMEOUT)
        await page.goto(url)
        await expect(progress).to_have_text("1/4 done")
        await expect(
            checklist.get_by_role("button", name="Add your first source")
        ).to_have_count(0)
        await shot(page, "getting-started-1")

    async def empty_book_rows_and_generate():
        """SPEC-32: Generate is disabled with its reason until the book has a
        chapter and characters; two same-title chapters have distinct labels,
        so the second is deleted by its own."""
        await page.goto(STATE["empty_url"])
        await tab("Scenes")
        generate = page.get_by_role("button", name="Generate scene")
        await expect(generate).to_be_disabled()
        await expect(page.get_by_text("Add a chapter first")).to_be_visible()
        await tab("Chapters")
        for _ in range(2):
            await page.get_by_role("button", name="Add Chapter").first.click()
            await page.get_by_placeholder("Chapter title").fill("Paperwork")
            await page.get_by_role("button", name="Add Chapter").last.click()
        rows = page.get_by_test_id("chapter-row")
        await expect(rows).to_have_count(2)
        await expect(page.get_by_text("Add characters to cast a scene")).to_be_visible()
        await expect(
            rows.first.get_by_role("button", name="Generate scene")
        ).to_be_disabled()
        first = await rows.first.get_attribute("data-chapter-id")
        await page.get_by_role("button", name="Delete chapter 2, Paperwork").click()
        await page.get_by_role("dialog").get_by_role(
            "button", name="Delete chapter", exact=True
        ).click()
        await expect(rows).to_have_count(1)
        await expect(rows.first).to_have_attribute("data-chapter-id", first)
        await shot(page, "empty-book-rows")

    async def dashboard_progress_and_resume():
        """B12: the dashboard card shows the book's progress and resumes the
        last-edited scene."""
        await page.goto(f"{BASE}/dashboard/")
        card = page.get_by_test_id("dashboard-book").filter(has_text="Bored to Undeath")
        await expect(card).to_contain_text(re.compile(r"[\d,]+ words?"))
        await expect(card).to_contain_text(
            re.compile(r"\d+/\d+ planned scenes drafted")
        )
        await shot(page, "dashboard-progress")
        await card.get_by_role("button", name=re.compile("^Resume:")).click()
        await page.wait_for_url(re.compile(r"/books/editor/?\?scene="))
        await expect(page.get_by_role("heading", name="Scene Editor")).to_be_visible()

    await step("sign in", sign_in())
    await step("create a book", create_book())
    await step("paste a source, review, commit", paste_source_and_review())
    await step("the review ranks by name mentions", review_ranks_by_mentions())
    await step("the cast is in the canon", canon_has_cast())
    await step(
        "voice samples are the character's own words", voice_is_their_own_words()
    )
    await step("add a chapter", add_chapter())
    await step("generate a scene from the cast", generate_scene())
    await step("generate a staged outline", generate_outline())
    await step("add a suggested character to the cast", add_suggested_character())
    await step("run a continuity check, open a finding in the editor", continuity())
    await step("export the book", export())
    await step("trash a character and restore it", trash_and_restore_character())
    await step("restore an earlier synopsis", restore_earlier_synopsis())
    await step(
        "regenerate the outline on an edited premise",
        regenerate_outline_with_edited_premise(),
    )
    await step("plot threads", plot_threads())
    await step("edit a scene, load a revision", edit_scene_and_load_revision())
    await step(
        "unsaved edits are guarded, kept as a draft, restored, saved",
        editor_safety_net(),
    )
    await step(
        "revise a selection: discard is byte-identical, accept is one revision",
        revise_discard_then_accept(),
    )
    await step("revise on a stale view is refused", revise_stale_view_is_refused())
    await step(
        "reorder, delete and restore a chapter", reorder_delete_and_restore_chapter()
    )
    await step(
        "a scene from a planned beat: its tone, cast and id",
        scene_from_a_planned_beat(),
    )
    await step(
        "generate all planned scenes: the modal's beat is skipped",
        generate_all_planned_scenes(),
    )
    await step(
        "planned scenes: beat cast plus POV, no default tone",
        planned_scenes_follow_their_beats(),
    )
    await step(
        '"Redo beat 2" on a planned scene opens a proposal', redo_a_planned_beat()
    )
    await step(
        "export: clean headings, scene breaks, a chapter range",
        export_is_a_clean_manuscript(),
    )
    await step(
        "add source: accepted types; a .doc is refused", add_source_refuses_doc()
    )
    await step(
        "ensemble on the free tier runs as prose, and says so",
        ensemble_runs_as_prose_and_says_so(),
    )
    await step(
        "a quota-paused scene shows on Usage and cancels",
        paused_scene_on_usage_then_cancel(),
    )
    await step(
        "legacy pages redirect; dashboard", legacy_pages_redirect_and_dashboard()
    )
    await step("a second author cannot see the book", second_author_is_isolated())
    await step(
        "a batch over budget writes what fits, defers the rest",
        a_batch_over_budget_writes_what_fits(),
    )
    await step("the book at phone width", book_page_at_phone_width())
    await step("a new book's getting-started checklist", new_book_checklist())
    await step(
        "an empty book: Generate disabled, same-title rows",
        empty_book_rows_and_generate(),
    )
    await step("dashboard: progress and resume", dashboard_progress_and_resume())


# Every chapter row at phone width (B4): its title is visible with at least 20
# characters (or all of a shorter title) actually rendered, i.e. not clipped by
# line-clamp, overflow or the card; and every visible button sits inside its
# row and inside the viewport. Returns the problems found ([] when clean).
PHONE_CHAPTER_CHECK = """
() => {
  const vw = window.innerWidth
  const problems = []
  const inside = (r, o) =>
    r.left >= o.left - 1 && r.right <= o.right + 1 &&
    r.top >= o.top - 1 && r.bottom <= o.bottom + 1
  document.querySelectorAll('[data-testid=chapter-row]').forEach((row, i) => {
    const name = `row ${i + 1}`
    const card = row.getBoundingClientRect()
    const title = row.querySelector('[data-testid=chapter-title]')
    if (!title) { problems.push(`${name}: no title element`); return }
    const box = title.getBoundingClientRect()
    if (box.width < 1 || box.height < 1) problems.push(`${name}: title not visible`)
    if (box.left < 0 || box.right > vw + 1) problems.push(`${name}: title off screen`)
    // Count the characters whose glyphs fall inside the title's own box.
    const text = title.textContent || ''
    const walker = document.createTreeWalker(title, NodeFilter.SHOW_TEXT)
    let shown = 0
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      for (let k = 0; k < node.length; k++) {
        if (!node.data[k].trim()) continue
        const range = document.createRange()
        range.setStart(node, k)
        range.setEnd(node, k + 1)
        const r = range.getBoundingClientRect()
        if (r.width > 0 && inside(r, box) && r.right <= vw + 1) shown++
      }
    }
    const want = Math.min(20, text.replace(/\s/g, '').length)
    if (shown < want) problems.push(`${name}: only ${shown} title characters shown`)
    row.querySelectorAll('button').forEach((b) => {
      if (b.getClientRects().length === 0) return  // display:none (other width)
      const r = b.getBoundingClientRect()
      const label = b.getAttribute('aria-label') || b.textContent.trim()
      if (!inside(r, card)) problems.push(`${name}: "${label}" spills out of its row`)
      if (r.left < 0 || r.right > vw + 1) problems.push(`${name}: "${label}" off screen`)
    })
  })
  return problems
}
"""


async def assert_active_tab_in_view(page: Page, name: str) -> None:
    """The active tab is the one named, and it is fully on screen (B4)."""
    active = page.get_by_test_id("book-tabs").locator("[aria-current=page]")
    await expect(active).to_have_text(name)
    # scrollIntoView is smooth on some engines: let it settle.
    await page.wait_for_timeout(300)
    box = await active.bounding_box()
    width = page.viewport_size["width"]
    assert (
        box and box["x"] >= -1 and box["x"] + box["width"] <= width + 1
    ), f"active tab {name!r} is not in view: {box}"


async def main() -> int:
    async with async_playwright() as p:
        exe = os.getenv("PLAYWRIGHT_CHROMIUM")
        browser = await p.chromium.launch(**({"executable_path": exe} if exe else {}))
        page = await browser.new_page(accept_downloads=True)

        # "Move to the Trash?" confirms: an author clicking OK (see DIALOGS).
        def on_dialog(d) -> None:
            DIALOGS["seen"].append((d.type, d.message))
            asyncio.ensure_future(d.accept() if DIALOGS["accept"] else d.dismiss())

        page.on("dialog", on_dialog)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            await journey(page)
        except Exception as e:
            await shot(page, "FAILED")
            print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            print((await page.inner_text("body"))[:2000], file=sys.stderr)
            return 1
        finally:
            await browser.close()
        if errors:
            print("page errors:", *errors, sep="\n  ", file=sys.stderr)
            return 1
        print("browser journey: all steps passed")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
