from datetime import date

from lxml import html as lh

from .diary import AmbiguousEntry, find_entries, no_matching_entry, resolve_entry
from .mfp_web import get_page, post_legacy_delete, site_url


def get_exercise(client, day: date) -> dict:
    sections = {}
    for section in client._get_exercises(day):
        sections[section.name.lower()] = section.get_as_list()
    return {"day": day.isoformat(), "exercise": sections}


def _exercise_diary_url(client, day: date | None = None) -> str:
    return site_url(client, f"exercise/diary/{client.effective_username}", day)


def exercise_page(client, day: date) -> tuple[lh.HtmlElement, str]:
    return get_page(client, _exercise_diary_url(client, day))


def _field_key(heading: str) -> str:
    words = "".join(c if c.isalnum() else " " for c in heading.lower()).split()
    return "_".join(words)


def _cell_number(cell) -> float | None:
    try:
        return float(cell.text_content().strip().replace(",", ""))
    except ValueError:
        return None


def _exercise_name(cell) -> str:
    for anchor in cell.xpath(".//a"):
        name = anchor.text_content().strip()
        if name:
            return name
    return cell.text_content().strip()


def exercise_entries(doc) -> list[dict]:
    entries = []
    for table in doc.xpath("//table[contains(@class, 'table0')]"):
        headings = table.xpath("./thead/tr[1]/td")
        if not headings:
            continue
        section = headings[0].text_content().strip().lower()
        field_keys = [_field_key(h.text_content()) for h in headings[1:]]
        for tr in table.xpath("./tbody/tr[not(@class)]"):
            delete_links = tr.xpath("./td[contains(@class, 'delete')]//a/@href")
            if not delete_links:
                continue
            cells = tr.xpath("./td")
            entry = {
                "entry_id": delete_links[0].split("?")[0].rstrip("/").split("/")[-1],
                "section": section,
                "name": _exercise_name(cells[0]),
            }
            for key, cell in zip(field_keys, cells[1:], strict=False):
                if key:
                    entry[key] = _cell_number(cell)
            entries.append(entry)
    return entries


def remove_exercise_entry(client, entry_id: str, token: str) -> None:
    post_legacy_delete(
        client,
        "exercise/remove",
        entry_id,
        token,
        referer=_exercise_diary_url(client),
    )


def delete_exercise(client, day: date, query: str, all_matches: bool = False) -> dict:
    doc, token = exercise_page(client, day)
    entries = exercise_entries(doc)
    if all_matches:
        targets = find_entries(entries, query)
        if not targets:
            raise no_matching_entry(entries, query, None, day)
    else:
        try:
            targets = [resolve_entry(entries, query, None, day)]
        except AmbiguousEntry as ambiguous:
            raise AmbiguousEntry(
                query,
                None,
                day,
                ambiguous.candidates,
                advice=(
                    "Use a more specific query to pick one, or pass "
                    "all_matches=True to remove every match (e.g. duplicate "
                    "rows from a sync)."
                ),
            ) from None
    for entry in targets:
        remove_exercise_entry(client, entry["entry_id"], token)
    return {"day": day.isoformat(), "removed": targets, "count": len(targets)}
