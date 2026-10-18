"""Work verbs: tasks (with due, assignee, tags, parent, edit-in-place and
sorting, §102.3-5) and projects (update, archive, members, §102.6)."""
from __future__ import annotations

import datetime as _dt
from typing import Any

from meshbook import UNSET
from meshbook.resources import PRIORITIES, PROJECT_ROLES, STATUSES, TASK_SORTS

from . import _when
from ._core import CliError, Ctx, need_uuid, split_tags

PROJECT_STATUSES = ("Planning", "Active", "OnHold", "Complete", "Cancelled")

# ─── tasks ──────────────────────────────────────────────────────────────


def _due_value(text: str, tz: _dt.tzinfo | None) -> tuple[Any, str | None]:
    """``--due`` → (SDK value, zone note). A bare date stays a date (dueDate);
    a time without an offset is the local wall clock (dueAt)."""
    raw = text.strip()
    if len(raw) == 10:
        return _when.parse_date(raw, "due"), None
    when, _ = _when.parse_when(raw, tz=tz, field="due")
    return when, _when.zone_label(tz, when)


def _due_text(t: dict[str, Any]) -> str:
    if t.get("dueAt"):
        return _when.show(t["dueAt"], None, "%Y-%m-%d %H:%M")
    return str(t.get("dueDate") or "")


def _task_line(t: dict[str, Any]) -> str:
    due = _due_text(t)
    due_col = f"due {due}" if due else ""
    prio = str(t.get("priority") or "")
    return (f"  [{t.get('status') or '?'}] {prio:<6} {due_col:<20} {t.get('title')}   "
            f"{t.get('id')}")


def cmd_tasks_list(ctx: Ctx) -> int:
    a = ctx.args
    c = ctx.client
    assignee = a.assignee
    if a.mine:
        if a.assignee:
            raise CliError("bad_argument", "Pass --mine or --assignee, not both.")
        # Your own id, so --mine combines with --project/--search/--status
        # (the SDK's list_mine() takes no project or search filter).
        assignee = c.whoami().id
    rows = c.tasks.list(
        limit=a.limit, status=a.status, offset=a.offset, project=a.project,
        assignee=assignee, search=a.search, sort=a.sort,
    )
    return ctx.listing(rows, _task_line, empty="  (no tasks)")


def cmd_tasks_show(ctx: Ctx) -> int:
    t = ctx.client.tasks.get(ctx.args.task_id)
    if ctx.json:
        ctx.emit(t)
        return 0
    print(f"{t.get('title')}   {t.get('id')}")
    print(f"  status:    {t.get('status')}")
    print(f"  priority:  {t.get('priority') or '-'}")
    print(f"  due:       {_due_text(t) or '-'}")
    if t.get("projectName") or t.get("projectId"):
        print(f"  project:   {t.get('projectName') or t.get('projectId')}")
    assignees = t.get("assignees") or []
    names = [str(x.get("displayName") or x.get("username") or x.get("userId")) for x in assignees
             if isinstance(x, dict)]
    if names:
        print(f"  assignees: {', '.join(names)}")
    elif t.get("assigneeUserId"):
        print(f"  assignee:  {t.get('assigneeUserId')}")
    if t.get("parentTaskId"):
        print(f"  parent:    {t.get('parentTaskId')}")
    if t.get("description"):
        print(f"  {t.get('description')}")
    return 0


def cmd_tasks_create(ctx: Ctx) -> int:
    a = ctx.args
    tz = _when.zone(a.tz)
    due, note = _due_value(a.due, tz) if a.due else (None, None)
    data = ctx.client.tasks.create(
        None, a.title, a.priority, a.description,
        project=a.project, due=due, assignee=a.assignee, tags=split_tags(a.tags),
        parent=a.parent, status=a.status,
    )
    lines = [f"Created task: {data.get('title')} ({data.get('id')})"]
    if a.due:
        lines.append(f"  due: {_due_text(data) or a.due}" + (f"  (times in {note})" if note else ""))
    ctx.wrote(*lines, data=data)
    return 0


def cmd_tasks_update(ctx: Ctx) -> int:
    """Edit a task in place, keeping its history (§102.4: the old only edit was
    complete-and-recreate)."""
    a = ctx.args
    tz = _when.zone(a.tz)
    if a.due and a.no_due:
        raise CliError("bad_argument", "Pass --due or --no-due, not both.")
    if a.assignee and a.unassign:
        raise CliError("bad_argument", "Pass --assignee or --unassign, not both.")
    due: Any = UNSET
    note = None
    if a.due:
        due, note = _due_value(a.due, tz)
    elif a.no_due:
        due = None
    assignee: Any = UNSET
    if a.assignee:
        assignee = a.assignee
    elif a.unassign:
        assignee = None
    data = ctx.client.tasks.update(
        a.task_id,
        title=a.title if a.title is not None else UNSET,
        priority=a.priority if a.priority is not None else UNSET,
        description=(None if a.description == "" else a.description)
        if a.description is not None else UNSET,
        due=due, assignee=assignee,
        status=a.status if a.status is not None else UNSET,
        parent=a.parent if a.parent is not None else UNSET,
        tags=split_tags(a.tags),
    )
    lines = [f"Updated task: {data.get('title')} ({a.task_id})"]
    if note:
        lines.append(f"  due: {_due_text(data)}  (times in {note})")
    ctx.wrote(*lines, data=data)
    return 0


def cmd_tasks_complete(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.tasks.done(a.task_id, a.status)
    ctx.wrote(f"Task {a.task_id} -> {a.status}", data=data)
    return 0


def cmd_tasks_delete(ctx: Ctx) -> int:
    tid = need_uuid(ctx.args.task_id, "task id")
    data = ctx.client.tasks.delete(tid)
    ctx.wrote(f"Deleted task {tid} (soft delete; restorable)", data=data)
    return 0


def cmd_tasks_log(ctx: Ctx) -> int:
    """Log hours. The date defaults to TODAY ON THIS MACHINE'S CLOCK: 0.11 used
    the UTC date, so a Sydney morning was logged against yesterday."""
    a = ctx.args
    day = _when.parse_date(a.date, "date") if a.date else _dt.date.today()
    data = ctx.client.tasks.log_time(a.task_id, a.hours, day, a.note)
    ctx.wrote(f"Logged {a.hours}h on task {a.task_id} for {day.isoformat()}", data=data)
    return 0


# ─── projects ───────────────────────────────────────────────────────────


def _project_id(ctx: Ctx, ref: str) -> str:
    return str(ctx.client.projects.resolve(ref)["id"])


def _project_line(pr: dict[str, Any]) -> str:
    code = f"  ({pr.get('code')})" if pr.get("code") else ""
    return f"  [{pr.get('status') or '?'}] {pr.get('name')}{code}   {pr.get('id')}"


def cmd_projects_list(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.projects.list(
        limit=a.limit, portfolio_id=a.portfolio, status=a.status, offset=a.offset, search=a.search,
    )
    return ctx.listing(rows, _project_line, empty="  (no projects)")


def cmd_projects_show(ctx: Ctx) -> int:
    pr = ctx.client.projects.get(_project_id(ctx, ctx.args.project))
    if ctx.json:
        ctx.emit(pr)
        return 0
    print(_project_line(pr).strip())
    if pr.get("description"):
        print(f"  {pr.get('description')}")
    members = pr.get("members") or []
    if members:
        print(f"  members ({len(members)}):")
        for m in members:
            role = m.get("roleOnProject") or m.get("role") or ""
            print(f"    {m.get('displayName') or m.get('username') or m.get('userId')}  {role}")
    tasks = pr.get("tasks") or []
    if tasks:
        print(f"  tasks: {len(tasks)}")
    return 0


def cmd_projects_create(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.projects.create(a.name, a.code, a.description, a.portfolio,
                                      owner=a.owner, status=a.status)
    ctx.wrote(f"Created project: {data.get('name')} ({data.get('id')})", data=data)
    return 0


def cmd_projects_update(ctx: Ctx) -> int:
    a = ctx.args

    def opt(v: Any) -> Any:
        if v is None:
            return UNSET
        return None if v == "" else v

    start = UNSET if a.start_date is None else (
        None if a.start_date == "" else _when.parse_date(a.start_date, "start-date"))
    target = UNSET if a.target_end_date is None else (
        None if a.target_end_date == "" else _when.parse_date(a.target_end_date, "target-end-date"))
    pid = _project_id(ctx, a.project)
    data = ctx.client.projects.update(
        pid, name=opt(a.name), code=opt(a.code), description=opt(a.description),
        status=opt(a.status), owner=opt(a.owner), start_date=start, target_end_date=target,
    )
    ctx.wrote(f"Updated project: {data.get('name')} ({pid})", data=data)
    return 0


def cmd_projects_archive(ctx: Ctx) -> int:
    """meshbook has no Archived state: archive = a closed status (Complete, or
    Cancelled with --cancelled). Not a delete; `projects delete` is that."""
    pid = _project_id(ctx, ctx.args.project)
    status = "Cancelled" if ctx.args.cancelled else "Complete"
    data = ctx.client.projects.archive(pid, status)
    ctx.wrote(f"Archived project {data.get('name') or pid}: status {status} "
              "(hidden from the active board; `projects update --status Active` reopens it)",
              data=data)
    return 0


def cmd_projects_delete(ctx: Ctx) -> int:
    pid = need_uuid(ctx.args.project_id, "project id")
    data = ctx.client.projects.delete(pid)
    ctx.wrote(f"Deleted project {pid} (soft delete; restorable)", data=data)
    return 0


def cmd_projects_member_list(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.projects.members(_project_id(ctx, a.project), limit=a.limit, offset=a.offset)
    return ctx.listing(
        rows,
        lambda m: f"  {m.get('displayName') or m.get('userId')}  {m.get('roleOnProject') or ''}   "
                  f"{m.get('userId')}",
        empty="  (no explicit members)",
    )


def cmd_projects_member_add(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.projects.add_member(_project_id(ctx, a.project), a.user, a.role)
    verb = "Added" if data.get("created", True) else "Already a member; role now"
    ctx.wrote(f"{verb} {data.get('displayName') or a.user} as {data.get('roleOnProject') or a.role}",
              data=data)
    return 0


def cmd_projects_member_remove(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.projects.remove_member(_project_id(ctx, a.project), a.user)
    ctx.wrote(f"Removed {a.user} from the project", data=data)
    return 0


# ─── wiring ─────────────────────────────────────────────────────────────


def _paging(s: Any, default: int | None = 50) -> None:
    s.add_argument("--limit", type=int, default=default, help=f"page size (default {default})")
    s.add_argument("--offset", type=int, default=None, help="skip this many rows (paging)")


def _task_fields(s: Any, *, update: bool) -> None:
    s.add_argument("--title", required=not update)
    s.add_argument("--priority", choices=PRIORITIES, help="/".join(PRIORITIES))
    s.add_argument("--description", help='free text' + (' ("" clears)' if update else ""))
    s.add_argument("--due", help="YYYY-MM-DD (a date) or 'YYYY-MM-DD HH:MM' (local time, or --tz)")
    s.add_argument("--assignee", help="@username, display name, or UUID")
    s.add_argument("--tags", action="append", metavar="A,B",
                   help="tags to add, comma-separated (repeatable)")
    s.add_argument("--parent", help="parent task UUID (makes this a subtask)")
    s.add_argument("--status", choices=STATUSES, help="/".join(STATUSES))
    s.add_argument("--tz", help="IANA zone for a --due time without an offset (default: local)")


def register(sub: Any, add: Any) -> None:
    st = add(sub, "tasks", help="project tasks")
    sts = st.add_subparsers(dest="tasks_cmd", required=True, metavar="ACTION")
    s = add(sts, "list", help="list tasks (status, priority, due)")
    _paging(s)
    s.add_argument("--project", help="project name, code, or UUID")
    s.add_argument("--assignee", help="@username, display name, or UUID")
    s.add_argument("--mine", action="store_true", help="tasks assigned to you")
    s.add_argument("--status", choices=STATUSES, help="filter by status")
    s.add_argument("--search", help="search term")
    s.add_argument("--sort", choices=TASK_SORTS,
                   help="due (undated last) / priority (Urgent first) / created / title; "
                        "'-' reverses")
    s.set_defaults(func=cmd_tasks_list)
    s = add(sts, "show", help="one task in full")
    s.add_argument("task_id", help="task UUID")
    s.set_defaults(func=cmd_tasks_show)
    s = add(sts, "create", help="create a task")
    s.add_argument("--project", required=True, help="project name, code, or UUID")
    _task_fields(s, update=False)
    s.set_defaults(func=cmd_tasks_create)
    s = add(sts, "update", help="edit a task in place (only the flags you pass are changed)")
    s.add_argument("task_id", help="task UUID")
    _task_fields(s, update=True)
    s.add_argument("--no-due", action="store_true", help="clear the due date")
    s.add_argument("--unassign", action="store_true", help="clear the assignee")
    s.set_defaults(func=cmd_tasks_update)
    s = add(sts, "complete", help="mark a task done (or another terminal status)")
    s.add_argument("task_id", help="task UUID")
    s.add_argument("--status", default="Done", choices=STATUSES,
                   help="status to set (default Done; e.g. Cancelled)")
    s.set_defaults(func=cmd_tasks_complete)
    s = add(sts, "delete", help="soft-delete a task (by id)")
    s.add_argument("task_id", help="task UUID")
    s.set_defaults(func=cmd_tasks_delete)
    s = add(sts, "log", help="log hours against a task (time tracking)")
    s.add_argument("task_id", help="task UUID")
    s.add_argument("--hours", type=float, required=True, help="hours worked (0 < h <= 24)")
    s.add_argument("--date", help="date worked (YYYY-MM-DD; default today on this machine's clock)")
    s.add_argument("--note", help="optional note")
    s.set_defaults(func=cmd_tasks_log)

    sp = add(sub, "projects", help="task-container projects")
    sps = sp.add_subparsers(dest="projects_cmd", required=True, metavar="ACTION")
    s = add(sps, "list", help="list projects")
    _paging(s)
    s.add_argument("--portfolio", help="filter by portfolio UUID")
    s.add_argument("--status", choices=("active", "completed"),
                   help="active = Planning/Active/OnHold; completed = Complete/Cancelled")
    s.add_argument("--search", help="search name, code, description")
    s.set_defaults(func=cmd_projects_list)
    s = add(sps, "show", help="one project with its members")
    s.add_argument("project", help="project name, code, or UUID")
    s.set_defaults(func=cmd_projects_show)
    s = add(sps, "create", help="create a project")
    s.add_argument("--name", required=True)
    s.add_argument("--code", help="short project code")
    s.add_argument("--description")
    s.add_argument("--portfolio", help="portfolio UUID")
    s.add_argument("--owner", help="@username, display name, or UUID (default: you)")
    s.add_argument("--status", choices=PROJECT_STATUSES)
    s.set_defaults(func=cmd_projects_create)
    s = add(sps, "update", help="edit a project (only the flags you pass are changed)")
    s.add_argument("project", help="project name, code, or UUID")
    s.add_argument("--name")
    s.add_argument("--code", help='short code ("" clears)')
    s.add_argument("--description", help='("" clears)')
    s.add_argument("--status", choices=PROJECT_STATUSES)
    s.add_argument("--owner", help='@username, display name, or UUID ("" clears)')
    s.add_argument("--start-date", help='YYYY-MM-DD ("" clears)')
    s.add_argument("--target-end-date", help='YYYY-MM-DD ("" clears)')
    s.set_defaults(func=cmd_projects_update)
    s = add(sps, "archive", help="close a project: status Complete (or --cancelled); not a delete")
    s.add_argument("project", help="project name, code, or UUID")
    s.add_argument("--cancelled", action="store_true", help="archive as Cancelled instead")
    s.set_defaults(func=cmd_projects_archive)
    s = add(sps, "delete", help="soft-delete a project (by id)")
    s.add_argument("project_id", help="project UUID")
    s.set_defaults(func=cmd_projects_delete)
    spm = add(sps, "member", help="explicit project members (list / add / remove)")
    spms = spm.add_subparsers(dest="project_member_cmd", required=True, metavar="ACTION")
    s = add(spms, "list", help="list a project's explicit members")
    s.add_argument("project", help="project name, code, or UUID")
    _paging(s, None)
    s.set_defaults(func=cmd_projects_member_list)
    s = add(spms, "add", help="add a mesh member to a project (idempotent)")
    s.add_argument("project", help="project name, code, or UUID")
    s.add_argument("user", help="@username, display name, or UUID")
    s.add_argument("--role", default="Member", choices=PROJECT_ROLES, help="default Member")
    s.set_defaults(func=cmd_projects_member_add)
    s = add(spms, "remove", help="remove a member from a project")
    s.add_argument("project", help="project name, code, or UUID")
    s.add_argument("user", help="@username, display name, or UUID")
    s.set_defaults(func=cmd_projects_member_remove)
