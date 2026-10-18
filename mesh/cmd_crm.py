"""CRM verbs: contacts, companies, leads, custom fields, saved views."""
from __future__ import annotations

import json
from typing import Any

from meshbook import UNSET

from ._core import CliError, Ctx, need_uuid, split_tags

# ─── contacts ───────────────────────────────────────────────────────────


def _contact_line(c: dict[str, Any]) -> str:
    company = c.get("primaryCompanyName") or c.get("companyName") or ""
    line = f"  {c.get('displayName')}"
    if company:
        line += f"  ({company})"
    if c.get("jobTitle"):
        line += f"  - {c.get('jobTitle')}"
    if c.get("primaryEmail"):
        line += f"  <{c['primaryEmail']}>"
    return line + f"   {c.get('id')}"


def cmd_contacts_list(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.contacts.list(a.search, limit=a.limit, offset=a.offset)
    return ctx.listing(rows, _contact_line, empty="  (no contacts)")


def cmd_contacts_show(ctx: Ctx) -> int:
    c = ctx.client
    cid = c.contacts.resolve(ctx.args.contact)["id"]
    data = c.contacts.get(str(cid))
    if ctx.json:
        ctx.emit(data)
        return 0
    print(_contact_line(data).strip())
    for label, key in (("title", "title"), ("job title", "jobTitle"), ("email", "primaryEmail"),
                       ("phone", "primaryPhone"), ("company", "primaryCompanyName"),
                       ("notes", "description")):
        if data.get(key):
            print(f"  {label + ':':<11}{data[key]}")
    tags = data.get("tags") or []
    if tags:
        print("  tags:      " + ", ".join(str(t.get("name") if isinstance(t, dict) else t) for t in tags))
    return 0


def cmd_contacts_create(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.contacts.create(
        a.first, a.last, a.email, a.company,
        phone=a.phone, title=a.title, role=a.role, notes=a.notes, tags=split_tags(a.tags),
    )
    lines = [f"Created: {data.get('displayName')} ({data.get('id')})"]
    if data.get("primaryCompanyId"):
        lines.append(f"  Linked to: {data.get('primaryCompanyName')}")
    elif a.company:
        lines.append(f"  ! '{a.company}' not matched: saved without a company link")
    ctx.wrote(*lines, data=data)
    return 0


def _clearable(value: str | None) -> Any:
    """CLI convention for update verbs: flag absent = leave alone; an empty
    string ("") = clear the field; anything else = set it."""
    if value is None:
        return UNSET
    return None if value == "" else value


def cmd_contacts_update(ctx: Ctx) -> int:
    a = ctx.args
    c = ctx.client
    cid = str(c.contacts.resolve(a.contact)["id"])
    data = c.contacts.update(
        cid,
        first_name=_clearable(a.first), last_name=_clearable(a.last), email=_clearable(a.email),
        phone=_clearable(a.phone), title=_clearable(a.title), role=_clearable(a.role),
        notes=_clearable(a.notes), company_id=_clearable(a.company_id), tags=split_tags(a.tags),
    )
    ctx.wrote(f"Updated contact: {data.get('displayName')} ({cid})", data=data)
    return 0


def cmd_contacts_delete(ctx: Ctx) -> int:
    cid = need_uuid(ctx.args.contact_id, "contact id")
    data = ctx.client.contacts.delete(cid)
    ctx.wrote(f"Deleted contact {cid} (soft delete; restorable from the recycle bin)", data=data)
    return 0


# ─── companies ──────────────────────────────────────────────────────────


def _company_line(co: dict[str, Any]) -> str:
    ind = f"  ({co.get('industry')})" if co.get("industry") else ""
    return f"  {co.get('name')}{ind}   {co.get('id')}"


def cmd_companies_list(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.companies.list(a.search, limit=a.limit, offset=a.offset)
    return ctx.listing(rows, _company_line, empty="  (no companies)")


def cmd_companies_show(ctx: Ctx) -> int:
    data = ctx.client.companies.get(ctx.args.company_id)
    if ctx.json:
        ctx.emit(data)
        return 0
    print(_company_line(data).strip())
    for key in ("website", "industry", "email", "phone", "description", "legalName", "abn"):
        if data.get(key):
            print(f"  {key + ':':<13}{data[key]}")
    return 0


def cmd_companies_create(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.companies.create(
        a.name, website=a.website, industry=a.industry, email=a.email, phone=a.phone,
        description=a.description,
    )
    ctx.wrote(f"Created company: {data.get('name')} ({data.get('id')})", data=data)
    return 0


def cmd_companies_update(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.companies.update(
        a.company_id,
        name=_clearable(a.name), website=_clearable(a.website), industry=_clearable(a.industry),
        email=_clearable(a.email), phone=_clearable(a.phone),
        description=_clearable(a.description),
    )
    ctx.wrote(f"Updated company: {data.get('name')} ({a.company_id})", data=data)
    return 0


def cmd_companies_delete(ctx: Ctx) -> int:
    cid = need_uuid(ctx.args.company_id, "company id")
    data = ctx.client.companies.delete(cid)
    ctx.wrote(f"Deleted company {cid} (soft delete; restorable)", data=data)
    return 0


# ─── leads ──────────────────────────────────────────────────────────────


def _lead_line(ld: dict[str, Any]) -> str:
    val = ld.get("valueAmount")
    money = f"  {val} {ld.get('currency') or ''}".rstrip() if val is not None else ""
    stage = ld.get("stageName") or ld.get("stageId") or ""
    return f"  {ld.get('title')}{money}  [{stage}]   {ld.get('id')}"


def cmd_leads_list(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.leads.list(
        limit=a.limit, pipeline_id=a.pipeline, company_id=a.company, offset=a.offset,
        stage=a.stage,
    )
    return ctx.listing(rows, _lead_line, empty="  (no leads)")


def cmd_leads_create(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.leads.create(
        a.title, pipeline_id=a.pipeline, value=a.value, description=a.description,
        stage=a.stage, contact=a.contact,
    )
    ctx.wrote(f"Created lead: {data.get('title')} ({data.get('id')})", data=data)
    return 0


def cmd_leads_move(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.leads.move_stage(a.lead_id, stage=a.stage, note=a.note)
    ctx.wrote(f"Moved lead {a.lead_id} -> stage {data.get('stageName') or a.stage}", data=data)
    return 0


def cmd_leads_delete(ctx: Ctx) -> int:
    lid = need_uuid(ctx.args.lead_id, "lead id")
    data = ctx.client.leads.delete(lid)
    ctx.wrote(f"Deleted lead {lid}", data=data)
    return 0


# ─── custom fields / saved views ────────────────────────────────────────


def cmd_custom_fields_list(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.custom_fields.list(a.entity, limit=a.limit, offset=a.offset)

    def row(cf: dict[str, Any]) -> str:
        req = " *required" if cf.get("isRequired") else ""
        return (f"  {cf.get('entityType')}.{cf.get('fieldKey')} ({cf.get('fieldType')}) - "
                f"{cf.get('label')}{req}   {cf.get('id')}")

    return ctx.listing(rows, row, empty="  (no custom fields)")


def cmd_saved_views_list(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.saved_views.list(a.entity, limit=a.limit, offset=a.offset)
    return ctx.listing(
        rows, lambda sv: f"  {sv.get('entityType') or sv.get('module')}: {sv.get('name')}   {sv.get('id')}",
        empty="  (no saved views)",
    )


def cmd_saved_views_create(ctx: Ctx) -> int:
    """NOTE the create field is `module` (leads / contacts / tasks ...), not entityType."""
    a = ctx.args
    filt = None
    if a.filter:
        try:
            filt = json.loads(a.filter)
        except json.JSONDecodeError as e:
            raise CliError("bad_argument", f"--filter must be valid JSON: {e}") from None
    data = ctx.client.saved_views.create(a.module, a.name, filt, a.shared)
    ctx.wrote(f"Created saved view '{a.name}' for {a.module}  ({data.get('id')})", data=data)
    return 0


# ─── wiring ─────────────────────────────────────────────────────────────


def _paging(s: Any, default: int | None = 50) -> None:
    s.add_argument("--limit", type=int, default=default, help=f"page size (default {default})")
    s.add_argument("--offset", type=int, default=None, help="skip this many rows (paging)")


def _contact_fields(s: Any, *, update: bool) -> None:
    clear = ' ("" clears)' if update else ""
    s.add_argument("--first", required=not update, help="first name")
    s.add_argument("--last", help=f"last name{clear}")
    s.add_argument("--email", help=f"primary email{clear}")
    s.add_argument("--phone", help=f"primary phone{clear}")
    s.add_argument("--title", help=f"honorific: Mr, Ms, Dr ...{clear}")
    s.add_argument("--role", "--job-title", dest="role", help=f"job title / role{clear}")
    s.add_argument("--notes", help=f"free-text notes (the contact's description){clear}")
    s.add_argument("--tags", action="append", metavar="A,B",
                   help="tags to add, comma-separated (repeatable; new names are created)"
                        + ("; tags are only ever added here" if update else ""))


def register(sub: Any, add: Any) -> None:
    sc = add(sub, "contacts", help="CRM contacts")
    scs = sc.add_subparsers(dest="contacts_cmd", required=True, metavar="ACTION")
    s = add(scs, "list", help="list contacts")
    s.add_argument("--search", help="search term")
    _paging(s)
    s.set_defaults(func=cmd_contacts_list)
    s = add(scs, "show", help="one contact in full")
    s.add_argument("contact", help="contact id, exact name, or email")
    s.set_defaults(func=cmd_contacts_show)
    s = add(scs, "create", help="create a contact")
    _contact_fields(s, update=False)
    s.add_argument("--company", help="company name (resolved server-side, §22c)")
    s.set_defaults(func=cmd_contacts_create)
    s = add(scs, "update", help="edit a contact (only the flags you pass are changed)")
    s.add_argument("contact", help="contact id, exact name, or email")
    _contact_fields(s, update=True)
    s.add_argument("--company-id", help='primary company UUID ("" clears)')
    s.set_defaults(func=cmd_contacts_update)
    s = add(scs, "delete", help="soft-delete a contact (by id)")
    s.add_argument("contact_id", help="contact UUID")
    s.set_defaults(func=cmd_contacts_delete)

    sco = add(sub, "companies", help="CRM companies")
    scos = sco.add_subparsers(dest="companies_cmd", required=True, metavar="ACTION")
    s = add(scos, "list", help="list companies")
    s.add_argument("--search", help="search term")
    _paging(s)
    s.set_defaults(func=cmd_companies_list)
    s = add(scos, "show", help="one company in full")
    s.add_argument("company_id", help="company UUID")
    s.set_defaults(func=cmd_companies_show)
    for verb in ("create", "update"):
        upd = verb == "update"
        s = add(scos, verb, help="create a company" if not upd
                else "edit a company (only the flags you pass are changed)")
        if upd:
            s.add_argument("company_id", help="company UUID")
        clear = ' ("" clears)' if upd else ""
        s.add_argument("--name", required=not upd)
        s.add_argument("--website", help=f"website{clear}")
        s.add_argument("--industry", help=f"industry{clear}")
        s.add_argument("--email", help=f"email{clear}")
        s.add_argument("--phone", help=f"phone{clear}")
        s.add_argument("--description", help=f"description{clear}")
        s.set_defaults(func=cmd_companies_update if upd else cmd_companies_create)
    s = add(scos, "delete", help="soft-delete a company (by id)")
    s.add_argument("company_id", help="company UUID")
    s.set_defaults(func=cmd_companies_delete)

    sl = add(sub, "leads", help="CRM leads")
    sls = sl.add_subparsers(dest="leads_cmd", required=True, metavar="ACTION")
    s = add(sls, "list", help="list leads")
    _paging(s)
    s.add_argument("--pipeline", help="filter by pipeline UUID")
    s.add_argument("--stage", help="filter by stage name ('Pipeline/Stage' to disambiguate) or UUID")
    s.add_argument("--company", help="filter by company UUID")
    s.set_defaults(func=cmd_leads_list)
    s = add(sls, "create", help="create a lead (default pipeline, first stage unless given)")
    s.add_argument("--title", required=True)
    s.add_argument("--pipeline", help="pipeline UUID (default: the mesh's default pipeline)")
    s.add_argument("--stage", help="stage name or UUID (default: the pipeline's first stage)")
    s.add_argument("--value", type=float, help="value amount")
    s.add_argument("--contact", help="contact id, exact name, or email")
    s.add_argument("--description")
    s.set_defaults(func=cmd_leads_create)
    s = add(sls, "move", help="move a lead to another pipeline stage")
    s.add_argument("lead_id", help="lead UUID")
    s.add_argument("stage", help="target stage name or UUID")
    s.add_argument("--note", help="optional note recorded with the move")
    s.set_defaults(func=cmd_leads_move)
    s = add(sls, "delete", help="delete a lead (by id)")
    s.add_argument("lead_id", help="lead UUID")
    s.set_defaults(func=cmd_leads_delete)

    scf = add(sub, "custom-fields", help="custom field definitions (read)")
    scfs = scf.add_subparsers(dest="custom_fields_cmd", required=True, metavar="ACTION")
    s = add(scfs, "list", help="list custom field definitions")
    s.add_argument("--entity", help="filter by entity type (contact/company/lead/...)")
    _paging(s, None)
    s.set_defaults(func=cmd_custom_fields_list)

    ssv = add(sub, "saved-views", help="saved views")
    ssvs = ssv.add_subparsers(dest="saved_views_cmd", required=True, metavar="ACTION")
    s = add(ssvs, "list", help="list saved views")
    s.add_argument("--entity", help="filter by entity type")
    _paging(s, None)
    s.set_defaults(func=cmd_saved_views_list)
    s = add(ssvs, "create", help="create a saved view")
    s.add_argument("--module", required=True,
                   help="entity grouping the view belongs to (leads/contacts/tasks/...)")
    s.add_argument("--name", required=True, help="view name")
    s.add_argument("--filter", help="filter as a JSON object string")
    s.add_argument("--shared", action="store_true", help="share with the whole mesh")
    s.set_defaults(func=cmd_saved_views_create)
