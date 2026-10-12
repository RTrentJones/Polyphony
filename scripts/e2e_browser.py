"""Browser end-to-end journey through the real site (SPEC-06, website testing).

Drives the statically-exported frontend served by the app, against the
deterministic stub LLM (evals/stub_llm.py), the way an author would:

  sign in -> create a book with a synopsis -> paste a source -> review and
  commit the extracted canon -> see the cast in the Canon tab -> add a chapter
  -> generate a scene from the cast picker -> read the scene -> generate an
  outline (staged job), confirm its premise, promote it to chapters -> run a continuity check -> export the book.

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
TIMEOUT = 90_000

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

    await step("sign in", sign_in())
    await step("create a book", create_book())
    await step("paste a source, review, commit", paste_source_and_review())
    await step("the cast is in the canon", canon_has_cast())
    await step("add a chapter", add_chapter())
    await step("generate a scene from the cast", generate_scene())
    await step("generate a staged outline", generate_outline())
    await step("run a continuity check", continuity())
    await step("export the book", export())


async def main() -> int:
    async with async_playwright() as p:
        exe = os.getenv("PLAYWRIGHT_CHROMIUM")
        browser = await p.chromium.launch(**({"executable_path": exe} if exe else {}))
        page = await browser.new_page(accept_downloads=True)
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
