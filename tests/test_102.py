"""DEV-DEBT §102.3-7: the CLI write surface (tasks, projects, contacts,
companies, deletes), against REPORT-SERVER-102.txt, transport faked."""
from __future__ import annotations

import datetime as dt

import pytest

from tests._fake import C1, CO1, L1, P1, T1, U1, ok, ok_list

TAG_ROUTE = f"/api/entities/task/{T1}/tags"


def _users(api):
    api.add("GET", "/api/users", ok_list([{"id": U1, "username": "alder", "displayName": "Alder"}]))


def _projects(api):
    api.add("GET", "/api/projects", ok_list([{"id": P1, "name": "Board", "code": "BRD"}]))


# ─── tasks ──────────────────────────────────────────────────────────

def test_tasks_create_with_due_assignee_tags_parent(api, run):
    _users(api)
    _projects(api)
    parent = "bbbbbbbb-0000-0000-0000-000000000099"
    api.add("POST", "/api/tasks", ok({"id": T1, "title": "Ship", "dueDate": "2026-10-24",
                                      "assigneeUserId": U1}))   # the server echoes it
    api.add("POST", TAG_ROUTE, ok({}))
    api.add("GET", TAG_ROUTE, ok_list([{"id": "tg1", "name": "release"}, {"id": "tg2", "name": "cli"}]))
    r = run("tasks", "create", "--project", "Board", "--title", "Ship", "--priority", "High",
            "--due", "2026-10-24", "--assignee", "@alder", "--tags", "release,cli",
            "--parent", parent)
    assert r.rc == 0, r.err
    body = api.body(api.one("POST", "/api/tasks"))
    assert body == {"projectId": P1, "title": "Ship", "priority": "High",
                    "dueDate": "2026-10-24", "assigneeUserId": U1, "parentTaskId": parent}
    names = [api.body(q)["name"] for q in api.calls("POST", TAG_ROUTE)]
    assert names == ["release", "cli"]
    assert "due: 2026-10-24" in r.out


def test_tasks_create_due_time_is_local_and_aware(api, run):
    api.add("POST", "/api/tasks", ok({"id": T1, "title": "X"}))
    run("tasks", "create", "--project", P1, "--title", "X", "--due", "2026-10-24 17:00",
        "--tz", "Australia/Sydney")
    body = api.body(api.one("POST", "/api/tasks"))
    assert body["dueAt"] == "2026-10-24T17:00:00+11:00" and "dueDate" not in body


def test_tasks_update_edits_in_place(api, run):
    _users(api)
    api.add("PATCH", f"/api/tasks/{T1}", ok({"id": T1, "title": "Renamed", "assigneeUserId": U1}))
    r = run("tasks", "update", T1, "--title", "Renamed", "--priority", "Urgent", "--status",
            "InProgress", "--due", "2026-11-01", "--assignee", "Alder", "--description", "")
    assert r.rc == 0, r.err
    body = api.body(api.one("PATCH", f"/api/tasks/{T1}"))
    assert body == {"title": "Renamed", "priority": "Urgent", "status": "InProgress",
                    "dueDate": "2026-11-01", "dueAt": None, "assigneeUserId": U1,
                    "description": None}
    assert not api.calls("POST", "/api/tasks")          # no complete-and-recreate


def test_tasks_update_clears_due_and_assignee(api, run):
    api.add("PATCH", f"/api/tasks/{T1}", ok({"id": T1}))
    run("tasks", "update", T1, "--no-due", "--unassign")
    assert api.body(api.one("PATCH", f"/api/tasks/{T1}")) == {
        "dueDate": None, "dueAt": None, "assigneeUserId": None}


def test_tasks_update_conflicting_flags(api, run):
    assert run("tasks", "update", T1, "--due", "2026-11-01", "--no-due").rc == 2
    assert run("tasks", "update", T1, "--assignee", "x", "--unassign").rc == 2
    assert run("tasks", "update", T1).rc == 2           # nothing to update
    assert not api.calls("PATCH", f"/api/tasks/{T1}")


def test_tasks_list_shows_priority_due_and_sorts(api, run):
    api.add("GET", "/api/tasks", ok_list([
        {"id": "t2", "title": "Later", "status": "NotStarted", "priority": "Low",
         "dueDate": "2026-11-30"},
        {"id": "t1", "title": "Soon", "status": "InProgress", "priority": "Urgent",
         "dueDate": "2026-10-20"},
        {"id": "t3", "title": "Someday", "status": "NotStarted", "priority": "Normal"},
    ], total=3))
    r = run("tasks", "list", "--sort", "due")
    assert r.rc == 0, r.err
    assert api.query(api.one("GET", "/api/tasks"))["sort"] == "due"
    lines = [x for x in r.out.splitlines() if x.strip().startswith("[")]
    assert [x.split()[-2] for x in lines] == ["Soon", "Later", "Someday"]  # undated last
    assert "Urgent" in lines[0] and "due 2026-10-20" in lines[0]
    assert "3 of 3" in r.out


def test_tasks_list_resolves_project_and_assignee_names(api, run):
    _users(api)
    _projects(api)
    api.add("GET", "/api/tasks", ok_list([]))
    run("tasks", "list", "--project", "BRD", "--assignee", "alder", "--status", "Blocked")
    q = api.query(api.one("GET", "/api/tasks"))
    assert (q["projectId"], q["assigneeId"], q["status"]) == (P1, U1, "Blocked")


def test_tasks_list_mine(api, run):
    """--mine combines with the other filters (a first cut used the SDK's
    list_mine(), which silently dropped --project and --search)."""
    _projects(api)
    api.add("GET", "/api/me", ok({"user": {"id": U1, "username": "alder"}}))
    api.add("GET", "/api/tasks", ok_list([]))
    run("tasks", "list", "--mine", "--project", "Board", "--search", "ship")
    q = api.query(api.one("GET", "/api/tasks"))
    assert (q["assigneeId"], q["projectId"], q["search"]) == (U1, P1, "ship")
    assert run("tasks", "list", "--mine", "--assignee", "x").rc == 2


def test_tasks_show(api, run):
    api.add("GET", f"/api/tasks/{T1}", ok({"id": T1, "title": "Ship", "status": "Review",
                                            "priority": "High", "dueAt": "2026-10-24T06:00:00+00:00",
                                            "assignees": [{"displayName": "Alder"}]}))
    r = run("tasks", "show", T1)
    assert r.rc == 0, r.err
    assert "Review" in r.out and "Alder" in r.out


# ─── projects ───────────────────────────────────────────────────────

def test_projects_update(api, run):
    _projects(api)
    _users(api)
    api.add("PATCH", f"/api/projects/{P1}", ok({"id": P1, "name": "Board v2"}))
    r = run("projects", "update", "Board", "--name", "Board v2", "--status", "OnHold",
            "--owner", "alder", "--target-end-date", "2026-12-01", "--code", "")
    assert r.rc == 0, r.err
    assert api.body(api.one("PATCH", f"/api/projects/{P1}")) == {
        "name": "Board v2", "code": None, "status": "OnHold", "ownerUserId": U1,
        "targetEndDate": "2026-12-01"}


@pytest.mark.parametrize("flag,status", [([], "Complete"), (["--cancelled"], "Cancelled")])
def test_projects_archive_is_a_closed_status(api, run, flag, status):
    api.add("PATCH", f"/api/projects/{P1}", ok({"id": P1, "name": "Board", "status": status}))
    r = run("projects", "archive", P1, *flag)
    assert r.rc == 0, r.err
    assert api.body(api.one("PATCH", f"/api/projects/{P1}")) == {"status": status}
    assert f"status {status}" in r.out
    assert not api.calls("DELETE", f"/api/projects/{P1}")


def test_projects_member_add_list_remove(api, run):
    _users(api)
    api.add("POST", f"/api/projects/{P1}/members",
            ok({"projectId": P1, "userId": U1, "displayName": "Alder",
                "roleOnProject": "Reviewer", "created": True}))
    r = run("projects", "member", "add", P1, "@alder", "--role", "Reviewer")
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", f"/api/projects/{P1}/members")) == {
        "userId": U1, "roleOnProject": "Reviewer"}
    assert "Added Alder as Reviewer" in r.out
    api.add("GET", f"/api/projects/{P1}/members",
            ok_list([{"userId": U1, "displayName": "Alder", "roleOnProject": "Reviewer"}]))
    assert "Alder" in run("projects", "member", "list", P1).out
    api.add("DELETE", f"/api/projects/{P1}/members/{U1}", ok({"removed": True}))
    assert run("projects", "member", "remove", P1, U1).rc == 0
    assert api.one("DELETE", f"/api/projects/{P1}/members/{U1}")


def test_projects_member_add_existing_member(api, run):
    api.add("POST", f"/api/projects/{P1}/members",
            ok({"displayName": "Alder", "roleOnProject": "Member", "created": False}))
    r = run("projects", "member", "add", P1, U1)
    assert "Already a member" in r.out


# ─── contacts / companies ───────────────────────────────────────────

def test_contacts_create_with_102_fields(api, run):
    api.add("POST", "/api/contacts", ok({"id": C1, "displayName": "Jane Doe"}))
    api.add("POST", f"/api/entities/contact/{C1}/tags", ok({}))
    api.add("GET", f"/api/entities/contact/{C1}/tags", ok_list([{"name": "vip"}]))
    r = run("contacts", "create", "--first", "Jane", "--last", "Doe", "--email", "j@x.test",
            "--phone", "+61 400 000 000", "--title", "Dr", "--role", "CTO",
            "--notes", "met at the expo", "--tags", "vip")
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", "/api/contacts")) == {
        "firstName": "Jane", "lastName": "Doe", "primaryEmail": "j@x.test",
        "primaryPhone": "+61 400 000 000", "title": "Dr", "jobTitle": "CTO",
        "description": "met at the expo"}
    assert api.body(api.one("POST", f"/api/entities/contact/{C1}/tags")) == {"name": "vip"}


def test_contacts_update(api, run):
    api.add("PATCH", f"/api/contacts/{C1}", ok({"id": C1, "displayName": "Jane Doe"}))
    r = run("contacts", "update", C1, "--job-title", "CEO", "--phone", "")
    assert r.rc == 0, r.err
    assert api.body(api.one("PATCH", f"/api/contacts/{C1}")) == {"primaryPhone": None,
                                                                  "jobTitle": "CEO"}


def test_contacts_show_by_email(api, run):
    api.add("GET", "/api/contacts", ok_list([{"id": C1, "displayName": "Jane Doe",
                                              "primaryEmail": "j@x.test"}]))
    api.add("GET", f"/api/contacts/{C1}", ok({"id": C1, "displayName": "Jane Doe",
                                               "primaryPhone": "+61"}))
    r = run("contacts", "show", "j@x.test")
    assert r.rc == 0, r.err
    assert "+61" in r.out


def test_companies_show_and_update(api, run):
    api.add("GET", f"/api/companies/{CO1}", ok({"id": CO1, "name": "Acme", "website": "a.test"}))
    r = run("companies", "show", CO1)
    assert r.rc == 0 and "a.test" in r.out
    api.add("PATCH", f"/api/companies/{CO1}", ok({"id": CO1, "name": "Acme Pty"}))
    r = run("companies", "update", CO1, "--name", "Acme Pty", "--industry", "")
    assert r.rc == 0, r.err
    assert api.body(api.one("PATCH", f"/api/companies/{CO1}")) == {"name": "Acme Pty",
                                                                   "industry": None}


def test_leads_create_by_stage_name_in_default_pipeline(api, run):
    api.add("GET", "/api/pipelines", ok_list([{
        "id": "pp1", "name": "Sales", "isDefault": True,
        "stages": [{"id": "s1", "name": "New"}, {"id": "s2", "name": "Qualified"}]}]))
    api.add("POST", "/api/leads", ok({"id": L1, "title": "Deal"}))
    r = run("leads", "create", "--title", "Deal", "--stage", "qualified")
    assert r.rc == 0, r.err
    body = api.body(api.one("POST", "/api/leads"))
    assert (body["pipelineId"], body["stageId"]) == ("pp1", "s2")


# ─── deletes: ids only, routes the server has ───────────────────────

@pytest.mark.parametrize("group,path", [
    ("contacts", f"/api/contacts/{C1}"),
    ("companies", f"/api/companies/{CO1}"),
    ("leads", f"/api/leads/{L1}"),
    ("tasks", f"/api/tasks/{T1}"),
    ("projects", f"/api/projects/{P1}"),
])
def test_delete_verbs(api, run, group, path):
    ident = path.rsplit("/", 1)[1]
    api.add("DELETE", path, ok({"id": ident, "deleted": True}))
    r = run(group, "delete", ident)
    assert r.rc == 0, r.err
    assert api.one("DELETE", path)
    assert "mesh:" in r.out


@pytest.mark.parametrize("group", ["contacts", "companies", "leads", "tasks", "projects"])
def test_delete_refuses_a_name(api, run, group):
    r = run(group, "delete", "Jane Doe")
    assert r.rc == 2
    assert "Deletes never resolve names" in r.err
    assert not api.calls("DELETE")


def test_log_date_is_local_not_utc(api, run):
    api.add("POST", "/api/task-time-logs", ok({}))
    run("tasks", "log", T1, "--hours", "1")
    assert api.body(api.one("POST", "/api/task-time-logs"))["loggedForDate"] == \
        dt.date.today().isoformat()
