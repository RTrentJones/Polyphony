"""Upload types agree between the UI, the server and the docs (B14, SPEC-21)."""

import json
import re
from pathlib import Path

import pytest

from app.api.sources import _UNSUPPORTED_UPLOAD_HINTS
from app.core.config import settings

pytestmark = pytest.mark.unit

_MODAL = Path(__file__).resolve().parents[2] / "frontend/components/AddSourceModal.tsx"


def _ts_const(name: str) -> str:
    m = re.search(
        rf"export const {name}\s*=\s*([^\n]+(?:\n  [^\n]+)*)", _MODAL.read_text()
    )
    assert m, f"{name} not found in {_MODAL}"
    return m.group(1).strip()


def test_ui_fallback_list_equals_server_allowed_extensions():
    ui = json.loads(_ts_const("ACCEPTED_EXTENSIONS").replace("'", '"'))
    assert ui == list(settings.ALLOWED_EXTENSIONS)
    assert ".doc" not in ui


def test_ui_fallback_size_equals_server_max_upload_size():
    expr = _ts_const("MAX_UPLOAD_BYTES")
    assert re.fullmatch(r"[\d\s*]+", expr), expr
    value = 1
    for factor in expr.split("*"):
        value *= int(factor)
    assert value == settings.MAX_UPLOAD_SIZE


def test_ui_doc_message_matches_the_server_415():
    ui = _ts_const("DOC_UNSUPPORTED_MESSAGE").strip('"')
    assert ui == _UNSUPPORTED_UPLOAD_HINTS[".doc"]
    assert "Save as .docx and upload again" in ui


async def test_config_endpoint_reports_the_server_list(client):
    r = await client.get("/api/v1/sources/config")
    assert r.status_code == 200
    assert r.json() == {
        "accepted_extensions": list(settings.ALLOWED_EXTENSIONS),
        "max_bytes": settings.MAX_UPLOAD_SIZE,
    }


async def test_doc_upload_is_415_with_the_fix(client, auth_headers):
    r = await client.post(
        "/api/v1/sources/upload",
        files={
            "file": ("Draft.DOC", b"\xd0\xcf\x11\xe0 old word", "application/msword")
        },
        headers=auth_headers,
    )
    assert r.status_code == 415
    assert r.json()["detail"] == (
        "Old Word .doc files aren't supported. Save as .docx and upload again."
    )


@pytest.mark.parametrize(
    "name, says",
    [("notes.md", ".md files are"), ("README", "Files without an extension are")],
)
async def test_other_unsupported_types_list_what_is_accepted(
    client, auth_headers, name, says
):
    r = await client.post(
        "/api/v1/sources/upload",
        files={"file": (name, b"# hi", "text/plain")},
        headers=auth_headers,
    )
    assert r.status_code == 415
    detail = r.json()["detail"]
    assert detail.startswith(says)
    assert detail.endswith(", ".join(settings.ALLOWED_EXTENSIONS))
