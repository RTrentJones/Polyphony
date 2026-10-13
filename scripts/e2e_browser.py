"""Browser end-to-end journey through the real site (SPEC-06, website testing).

Drives the statically-exported frontend served by the app, against the
deterministic stub LLM (evals/stub_llm.py), the way an author would:

  sign in -> create a book with a synopsis -> paste a source -> review and
  commit the extracted canon -> see the cast in the Canon tab -> add a chapter
  -> generate a scene from the cast picker -> read the scene -> generate an
  outline (staged job), confirm its premise, promote it to chapters -> run a continuity check -> export the book.

Then the rest of the surface: trash and restore a character, restore an earlier
synopsis, regenerate the outline with an EDITED premise (and prove the edit
reached the model), plot threads, edit a scene and load an earlier revision,
reorder and delete a chapter and restore it from the Trash, generate all of a
promoted chapter's planned scenes in order, an ensemble request
the free tier runs as prose (and says so), a scene paused by forced 429s that
shows on the Usage page and is cancelled, the legacy pages' redirects, the
dashboard, a second author who registers with an invite and cannot see the
first author's book, and the book page at phone width.

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

from playwright.async_api import Page, async_playwright, expect

BASE = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
EMAIL = os.getenv("ADMIN_EMAIL", "admin@e2e.test")
PASSWORD = os.getenv("ADMIN_PASSWORD", "admin-pass-12345")
SHOTS = Path(os.getenv("E2E_SHOTS", "e2e-shots"))
STUB = os.getenv("STUB_URL", "http://127.0.0.1:8099").rstrip("/")
TIMEOUT = 90_000
STATE: dict = {}

SYNOPSIS = (
    "Bored to Undeath. Milo Voss, a lich systems analyst, is retained by Aeon "
    "Holdings and onboarded into Reconciliations, where Zara Okafor, an "
    "uncontrollable sorceress, begins recruiting him. The Chief Executive Lich, "
    "Edric Thane, was once a clerk himself."
)
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


async def journey(page: Page) -> None:
    page.set_default_timeout(TIMEOUT)

    async def sign_in():
        await page.goto(f"{BASE}/auth/login/")
        await page.fill("input[name=email]", EMAIL)
        await page.fill("input[name=password]", PASSWORD)
        await page.get_by_role("button", name="Sign In").click()
        await page.wait_for_url(re.compile(r"/(dashboard|books)"))
        await shot(page, "signed-in")

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

    async def add_chapter():
        await page.get_by_role("button", name="Chapters").click()
        await page.get_by_role("button", name="Add Chapter").first.click()
        await page.get_by_placeholder("Chapter title").fill("Onboarding")
        await page.get_by_placeholder(re.compile("What this chapter covers")).fill(
            "Milo's first day in Reconciliations."
        )
        await page.get_by_role("button", name="Add Chapter").last.click()
        await expect(page.get_by_text("Onboarding").first).to_be_visible()
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

    async def continuity():
        await page.get_by_role("button", name="Continuity").click()
        await page.get_by_role("button", name="Run Check").click()
        await expect(
            page.get_by_text(re.compile("No contradictions|Checked \\d+ of"))
        ).to_be_visible(timeout=TIMEOUT)
        await shot(page, "continuity")

    async def export():
        async with page.expect_download() as dl:
            await page.get_by_role("button", name="MD", exact=True).click()
        download = await dl.value
        path = SHOTS / download.suggested_filename
        await download.save_as(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "Onboarding" in text, "export is missing the chapter"

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
        await page.get_by_role("button", name="Expand Onboarding").click()
        row = page.get_by_test_id("chapter-row").filter(has_text="Onboarding")
        await row.get_by_role("button", name="Edit", exact=True).first.click()
        await page.wait_for_url(re.compile(r"/books/editor"))
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

    async def reorder_delete_and_restore_chapter():
        await tab("Chapters")
        rows = page.get_by_test_id("chapter-row")

        async def title(row) -> str:
            text = await row.locator("p.font-medium").first.inner_text()
            return text.split(". ", 1)[1]

        first = await title(rows.first)
        await page.get_by_role("button", name=f"Move {first} down").click()
        await expect(rows.nth(1)).to_contain_text(first)
        # Delete the last chapter (a promoted one, no scenes) and restore it.
        count = await rows.count()
        last = await title(rows.last)
        await page.get_by_role("button", name=f"Delete {last}").click()
        await page.get_by_role("dialog").get_by_role(
            "button", name="Delete chapter", exact=True
        ).click()
        await expect(rows).to_have_count(count - 1)
        await tab("Trash")
        await page.get_by_role("button", name=f"Restore {last}").click()
        await toast(f'Restored "{last}"')
        await tab("Chapters")
        await expect(rows).to_have_count(count)
        await expect(rows.filter(has_text=last)).to_have_count(1)
        await shot(page, "chapters")

    async def generate_all_planned_scenes():
        await tab("Chapters")
        rows = page.get_by_test_id("chapter-row")
        button_name = "Generate all planned scenes"
        row = rows.filter(has=page.get_by_role("button", name=button_name)).first
        name = await row.locator("p.font-medium").first.inner_text()
        row = rows.filter(has_text=name)
        await row.get_by_role("button", name=button_name).click()
        await toast(re.compile(r"Writing 2 planned scenes in order"))
        # The scenes are a chain: the second is created when the first completes.
        edits = row.get_by_role("button", name="Edit", exact=True)
        await expect(edits).to_have_count(2, timeout=TIMEOUT)
        await expect(page.get_by_test_id("job-status")).to_have_count(
            0, timeout=TIMEOUT
        )
        # Clicking again writes nothing: every beat already has its scene.
        await row.get_by_role("button", name=button_name).click()
        await toast("Every planned beat in this chapter already has a scene.")
        await expect(edits).to_have_count(2)
        await shot(page, "planned-scenes")

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
        await page.request.post(
            f"{STUB}/__stub/config", data={"fail_429": 10_000, "retry_after": 1}
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

    async def book_page_at_phone_width():
        state = await page.context.storage_state()
        phone = await page.context.browser.new_context(
            storage_state=state, viewport={"width": 390, "height": 844}
        )
        p3 = await phone.new_page()
        try:
            await p3.goto(STATE["book_url"])
            await expect(p3.get_by_text("Bored to Undeath").first).to_be_visible()
            tabs = ("Chapters", "Scenes", "Sources", "Canon", "Outline", "Threads")
            for name in (*tabs, "Continuity", "Trash", "Usage"):
                if name == "Usage":
                    await p3.goto(f"{BASE}/usage/")
                    await expect(p3.get_by_test_id("usage-page")).to_be_visible()
                else:
                    await p3.get_by_role("button", name=name, exact=True).first.click()
                overflow = await p3.evaluate(
                    "document.documentElement.scrollWidth - window.innerWidth"
                )
                assert overflow <= 1, f"{name}: page scrolls sideways by {overflow}px"
            await p3.screenshot(path=str(SHOTS / "phone.png"), full_page=True)
        finally:
            await phone.close()

    await step("sign in", sign_in())
    await step("create a book", create_book())
    await step("paste a source, review, commit", paste_source_and_review())
    await step("the cast is in the canon", canon_has_cast())
    await step("add a chapter", add_chapter())
    await step("generate a scene from the cast", generate_scene())
    await step("generate a staged outline", generate_outline())
    await step("run a continuity check", continuity())
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
        "reorder, delete and restore a chapter", reorder_delete_and_restore_chapter()
    )
    await step("generate all planned scenes", generate_all_planned_scenes())
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
    await step("the book at phone width", book_page_at_phone_width())


async def main() -> int:
    async with async_playwright() as p:
        exe = os.getenv("PLAYWRIGHT_CHROMIUM")
        browser = await p.chromium.launch(**({"executable_path": exe} if exe else {}))
        page = await browser.new_page(accept_downloads=True)
        # "Move to the Trash?" confirms: an author clicking OK.
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
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
