"""One wire-shape test for every remaining verb, so the contract step's
recorded calls (MESH_CONTRACT_CALLS) cover every SDK endpoint the CLI can
reach, not only the ones the feature suites happened to touch."""
from __future__ import annotations

import pytest

from tests._fake import A1, CH1, CO1, E1, MESH, P1, U1, ok, ok_list
from tests.conftest import read_config, write_config

MSG = "12121212-0000-0000-0000-000000000001"


def test_agent_register_lands_in_the_lobby(api, run, _isolated_env):
    pytest.importorskip("cryptography")
    write_config({"base": "https://mesh.test"})
    api.add("POST", "/api/register/agent", ok({
        "enrolled": True, "username": "newbie", "user_id": U1, "lobby": True,
        "tokenEndpoint": "https://auth.example/t/", "assertionAud": "https://auth.example/i/",
        "clientId": "meshbook-agents"}))
    r = run("agent", "register", "Newbie", "--display-name", "New Bie", "--substrate", "pi-3b")
    assert r.rc == 0, r.err
    req = api.one("POST", "/api/register/agent")
    body = api.body(req)
    assert body["username"] == "newbie" and body["displayName"] == "New Bie"
    assert body["publicKey"]["kid"].startswith("mesh-agent-")
    assert api.header(req, "Authorization") is None          # unauthenticated route
    assert read_config()["auth_mode"] == "agent"
    assert (_isolated_env / "agent-key.pem").exists()
    assert "lobby" in r.out


def test_members_pending_remove_leave(api, run):
    api.add("GET", "/api/meshes/my-pending", ok_list([{"id": MESH, "name": "Invited", "meshType": "x"}]))
    r = run("members", "pending")
    assert "Invited" in r.out and "accept with" in r.out
    api.add("GET", "/api/users", ok_list([{"id": U1, "username": "rook"}]))
    api.add("POST", f"/api/meshes/{MESH}/remove", ok({}))
    assert run("members", "remove", "rook").rc == 0
    assert api.body(api.calls("POST", f"/api/meshes/{MESH}/remove")[-1]) == {"userId": U1}
    api.add("GET", "/api/me", ok({"user": {"id": "me-id", "username": "me"}}))
    assert run("members", "leave").rc == 0
    assert api.body(api.calls("POST", f"/api/meshes/{MESH}/remove")[-1]) == {"userId": "me-id"}


def test_companies_create(api, run):
    api.add("POST", "/api/companies", ok({"id": CO1, "name": "Acme"}))
    r = run("companies", "create", "--name", "Acme", "--website", "acme.test", "--phone", "1")
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", "/api/companies")) == {"name": "Acme", "website": "acme.test",
                                                          "phone": "1"}


def test_leads_list_by_stage_name(api, run):
    api.add("GET", "/api/pipelines", ok_list([{"id": "pp", "name": "Sales", "isDefault": True,
                                               "stages": [{"id": "s9", "name": "Won"}]}]))
    api.add("GET", "/api/leads", ok_list([{"id": "l1", "title": "Deal", "valueAmount": 5,
                                           "currency": "AUD", "stageName": "Won"}]))
    r = run("leads", "list", "--stage", "won")
    assert api.query(api.one("GET", "/api/leads"))["stageId"] == "s9"
    assert "Deal  5 AUD  [Won]" in r.out


def test_projects_create_and_show(api, run):
    api.add("POST", "/api/projects", ok({"id": P1, "name": "Board"}))
    r = run("projects", "create", "--name", "Board", "--code", "BRD", "--status", "Active")
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", "/api/projects")) == {"name": "Board", "code": "BRD",
                                                         "status": "Active"}
    api.add("GET", f"/api/projects/{P1}", ok({"id": P1, "name": "Board", "status": "Active",
                                               "members": [{"displayName": "Alder",
                                                            "roleOnProject": "Owner"}]}))
    r = run("projects", "show", P1)
    assert "Alder  Owner" in r.out


def test_chat_attach_react_unreact(api, run, tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hi", encoding="utf-8")
    api.add("POST", f"/api/chat-messages/{MSG}/attachments/json",
            ok({"id": A1, "filename": "note.txt", "byteSize": 2, "mimeType": "text/plain"}))
    assert run("chat", "attach", MSG, str(f)).rc == 0
    api.add("POST", f"/api/chat-messages/{MSG}/reactions", ok({}))
    assert run("chat", "react", MSG, "✅").rc == 0
    assert api.body(api.one("POST", f"/api/chat-messages/{MSG}/reactions")) == {"emoji": "✅"}
    api.add("DELETE", f"/api/chat-messages/{MSG}/reactions/%E2%9C%85", ok({}))
    r = run("chat", "unreact", MSG, "✅")
    assert r.rc == 0, r.err


def test_chat_attach_missing_file_is_a_local_error(api, run, tmp_path):
    r = run("chat", "attach", MSG, str(tmp_path / "nope.txt"))
    assert r.rc == 2
    assert "No such file" in r.err


def test_channels_post_reply_remove_member(api, run):
    api.add("GET", f"/api/meshes/{MESH}/channels", ok_list([{"id": CH1, "name": "general"}]))
    api.add("POST", f"/api/channels/{CH1}/messages", ok({"id": "m9"}))
    r = run("channels", "post", "#general", "hello")
    assert r.rc == 0 and "Posted to #general: m9" in r.out
    api.add("GET", f"/api/chat-messages/{MSG}", ok({"id": MSG, "channelId": CH1}))
    r = run("channels", "reply", MSG, "re")
    assert r.rc == 0, r.err
    assert api.body(api.calls("POST", f"/api/channels/{CH1}/messages")[-1]) == {
        "bodyMd": "re", "parentMessageId": MSG}
    api.add("DELETE", f"/api/channels/{CH1}/members/{U1}", ok({}))
    assert run("channels", "remove-member", "general", U1).rc == 0


def test_channels_reply_to_a_feed_message_is_refused(api, run):
    api.add("GET", f"/api/chat-messages/{MSG}", ok({"id": MSG, "channelId": None}))
    r = run("channels", "reply", MSG, "re")
    assert r.rc == 1
    assert "chat.post" in r.err or "not in a channel" in r.err


def test_dm_send_and_list(api, run):
    api.add("GET", "/api/users", ok_list([{"id": U1, "username": "rook", "displayName": "Rook"}]))
    api.add("POST", f"/api/meshes/{MESH}/dms/with/{U1}", ok({"id": CH1}))
    api.add("POST", f"/api/channels/{CH1}/messages", ok({"id": "m1"}))
    r = run("dm", "send", "@rook", "psst")
    assert r.rc == 0, r.err
    assert "Sent DM to Rook" in r.out
    api.add("GET", f"/api/meshes/{MESH}/dms", ok_list([{"id": CH1, "partner": {"displayName": "Rook"},
                                                       "unreadCount": 2}]))
    assert "Rook  (2 unread)" in run("dm", "list").out


def test_files_link_list_delete(api, run):
    api.add("POST", f"/api/entities/project/{P1}/attachments/links",
            ok({"id": A1, "externalUrl": "https://x.test"}))
    assert run("file", "link", "project", P1, "https://x.test", "--filename", "spec").rc == 0
    assert api.body(api.one("POST", f"/api/entities/project/{P1}/attachments/links")) == {
        "url": "https://x.test", "filename": "spec"}
    api.add("GET", f"/api/entities/project/{P1}/attachments", ok_list([
        {"id": A1, "kind": "link", "filename": "spec", "externalUrl": "https://x.test"}]))
    assert "-> https://x.test" in run("file", "list", "project", P1).out
    api.add("DELETE", f"/api/entity-attachments/{A1}", ok({}))
    assert run("file", "delete", A1).rc == 0


def test_custom_fields_and_saved_views_list(api, run):
    api.add("GET", "/api/custom-fields", ok_list([{"entityType": "contact", "fieldKey": "tier",
                                                   "fieldType": "text", "label": "Tier",
                                                   "isRequired": True, "id": "cf1"}]))
    r = run("custom-fields", "list", "--entity", "contact")
    assert api.query(api.one("GET", "/api/custom-fields"))["entityType"] == "contact"
    assert "contact.tier (text) - Tier *required" in r.out
    api.add("GET", "/api/saved-views", ok_list([{"entityType": "leads", "name": "Hot", "id": "sv"}]))
    assert "leads: Hot" in run("saved-views", "list").out


def test_notifications_unread_filter(api, run):
    api.add("GET", "/api/notifications", ok_list([{"kind": "Mention", "summary": "hi",
                                                   "createdAt": "2026-10-19T01:00:00"}], total=4))
    r = run("notifications", "--unread", "--limit", "1")
    q = api.query(api.one("GET", "/api/notifications"))
    assert q["unreadOnly"] == "true" and q["limit"] == "1"
    assert "1 of 4" in r.out


def test_events_attendee_free_series_edit_keeps_wire_minimal(api, run):
    """`events update --repeat` goes through PATCH (recurrence upsert), never a
    second call: one request per verb."""
    api.add("PATCH", f"/api/calendar-events/{E1}", ok({"id": E1, "title": "S"}))
    run("events", "update", E1, "--repeat", "yearly", "--until", "2030-01-01")
    assert len(api.requests) == 2      # PATCH + the mesh-name lookup for the write note
