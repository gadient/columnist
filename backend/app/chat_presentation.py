from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ChatPresentation:
    format: str
    title: str | None = None
    text: str | None = None
    bullets: list[str] | None = None
    table_columns: list[str] | None = None
    table_rows: list[list[str]] | None = None


# Presentation is purely heuristic (below): no model chooses the format.


def _heuristic_format(response: str, tool_called: str | None) -> str:
    if tool_called in {
        "get_due_dates_by_user",
        "get_high_priority_tasks",
        "get_card_details",
        "get_tomorrows_due_dates",
        "get_high_priority_due_dates_in_board",
        "get_upcoming_due_dates",
        "get_overdue_tasks",
    }:
        return "table"
    if response.count("\n- ") >= 1:
        return "bullets"
    return "text"


def _parse_line_items(response: str) -> tuple[str | None, list[str]]:
    lines = [line.strip() for line in response.splitlines() if line.strip()]
    if not lines:
        return None, []
    title = lines[0]
    items = [line[2:].strip() for line in lines[1:] if line.startswith("- ")]
    return title, items


def _parse_kv_item(item: str) -> tuple[str, dict[str, str]]:
    parts = [part.strip() for part in item.split("|")]
    if not parts:
        return item, {}
    title = parts[0]
    fields: dict[str, str] = {}
    for part in parts[1:]:
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        fields[key.strip()] = value.strip()
    return title, fields


def _table_from_items(title: str | None, items: list[str]) -> ChatPresentation:
    columns = ["Task", "Status", "Due", "Priority", "Board", "Assignees"]
    rows: list[list[str]] = []

    for item in items:
        task_title, fields = _parse_kv_item(item)
        rows.append(
            [
                task_title,
                fields.get("Status", ""),
                fields.get("Due", ""),
                fields.get("Priority", ""),
                fields.get("Board", ""),
                fields.get("Assignees", ""),
            ]
        )

    if not rows:
        return ChatPresentation(format="text", title=title, text=title or "No results.")

    # A table is only meaningful if the items actually carry the `Title | Status: … | Due: …`
    # pipe format. A model answering in prose produces bullets with no
    # fields, which fill column one and leave the other five blank — a table that looks broken
    # rather than an answer that reads. Decline the table and let the caller fall through.
    if not any(any(cell for cell in row[1:]) for row in rows):
        return ChatPresentation(format="text", title=title, text=None)

    return ChatPresentation(
        format="table",
        title=title,
        table_columns=columns,
        table_rows=rows,
    )


def _bullets_from_items(title: str | None, items: list[str], response: str) -> ChatPresentation:
    if items:
        return ChatPresentation(format="bullets", title=title, bullets=items)

    list_like = [line.strip() for line in response.splitlines() if line.strip().startswith("- ")]
    if list_like:
        cleaned = [re.sub(r"^-+\s*", "", line).strip() for line in list_like]
        return ChatPresentation(format="bullets", title=title, bullets=cleaned)

    return ChatPresentation(format="text", title=title, text=response)


def build_chat_presentation(message: str, response: str, tool_called: str | None) -> ChatPresentation:
    title, items = _parse_line_items(response)
    selected = _heuristic_format(response, tool_called)

    if selected == "table":
        table = _table_from_items(title, items)
        if table.format == "table":
            return table
        return _bullets_from_items(title, items, response)

    if selected == "bullets":
        return _bullets_from_items(title, items, response)

    return ChatPresentation(format="text", title=title, text=response)
