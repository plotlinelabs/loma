#!/usr/bin/env python3
"""Opt-in live verification using YOUR Google connection and a disposable Doc.

Never operates on a caller-supplied document. Creates one test document, exercises
formatting/tab/revision guards, then trashes that document even if assertions fail.
No skill records are created. Auth arguments must precede --run.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools._auth_token import verify_user_auth_token
from tools._google_auth import get_google_access_token
from integrations.google_docs_skill_source import GoogleDocsSource, SourceError, all_tabs, read_tab, build_requests, parse_markdown, block_key


async def main(args):
    if not verify_user_auth_token(args.auth_token, args.user_email):
        raise ValueError("Invalid or expired personal auth token")
    source = GoogleDocsSource(await get_google_access_token(args.user_email))
    created = await source._request("POST", "https://docs.googleapis.com/v1/documents", json={"title": "Loma disposable skill sync verification"})
    doc_id = created["documentId"]
    try:
        doc = await source.read(doc_id)
        tab_id = all_tabs(doc)[0]["tabProperties"]["tabId"]
        await source._request("POST", f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate", json={"requests": [
            {"insertText": {"location": {"index": 1, "tabId": tab_id}, "text": "Initial instructions"}},
            {"addDocumentTab": {"tabProperties": {"title": "Untouched control"}}},
        ]})
        before = await source.read(doc_id)
        control = all_tabs(before)[1]
        snapshot = read_tab(before, tab_id)
        desired = "# Playbook 😀\n\nDo **bold** and *italic* work.\n- First task\n- Second task\n\n1. Ordered task\n\nSee [reference](https://example.com).\n  keep indentation\n"
        await source.write(doc_id, snapshot, build_requests(snapshot, desired))
        after = await source.read(doc_id)
        actual = read_tab(after, tab_id)
        assert [block_key(b) for b in actual["blocks"]] == [block_key(b) for b in parse_markdown(desired)], "Formatting round-trip mismatch"
        assert all_tabs(after)[1] == control, "Other tab changed"
        assert build_requests(actual, actual["content"]) == [], "No-op round-trip produced edits"
        try:
            await source.write(doc_id, snapshot, [{"insertText": {"location": {"index": 1, "tabId": tab_id}, "text": "stale"}}])
        except SourceError as exc:
            assert exc.code == "conflict", "Unexpected stale-revision response"
        else:
            raise AssertionError("Stale revision was accepted")
        for desired_text in ("One\nTwo\n", "One\n", "One\nTwo\n", "😀\n\n"):
            snap = read_tab(await source.read(doc_id), tab_id)
            await source.write(doc_id, snap, build_requests(snap, desired_text))
            assert read_tab(await source.read(doc_id), tab_id)["content"] == desired_text
        print(json.dumps({"passed": True, "checks": ["rich formatting", "Unicode", "whitespace", "other-tab isolation", "no-op", "stale revision", "terminal paragraph edits"]}))
    except SourceError as exc:
        print("Google API diagnostic:", getattr(exc, "google_message", str(exc)))
        raise
    finally:
        await source._request("PATCH", f"https://www.googleapis.com/drive/v3/files/{doc_id}", json={"trashed": True})
        print("Disposable Google Doc moved to trash.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--auth-token", required=True)
    parser.add_argument("--run", action="store_true", required=True, help="Create, edit, and trash a disposable test document")
    args = parser.parse_args()
    asyncio.run(main(args))
