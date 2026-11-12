from datetime import date

from lxml import html as lh

from .diary_tables import column_name, parse_number, rows_until_classed
from .entry_matching import find_entries, no_matching_entry, resolve_entry
from .mfp_web import get_document, get_page, post_legacy_delete, site_url

ALL_MATCHES_ADVICE = (
    "Use a more specific query to pick one, or pass all_matches=True to remove "
    "every match (e.g. duplicate rows from a sync)."
)


def get_exercise(client, day: date) -> dict:
    doc = get_document(client, _exercise_diary_url(client, day))
    return {"day": day.isoformat(), "exercise": exercise_sections(doc)}


def _logged_value(cell) -> float | None:
    if cell.text is None or "N/A" in cell.text:
        return None
    return parse_number(cell.text)


def exercise_sections(doc: lh.HtmlElement) -> dict[str, list[dict]]:
    sections = {}
    for table in doc.xpath("//table[@class='table0']"):
        headings = table.xpath("./thead/tr[1]/td")
        if not headings:
            continue
        section, *columns = map(column_name, headings)
        first_rows = table.xpath("./tbody/tr[1]")
        rows = rows_until_classed(first_rows[0]) if first_rows else ()
        sections[section] = [
            {
                "name": _exercise_name(row.xpath("./td")[0]),
                "nutrition_information": {
                    name: _logged_value(cell)
                    for name, cell in zip(columns, row.xpath("./td")[1:], strict=False)
                    if name
                },
            }
            for row in rows
        ]
    return sections


def _exercise_diary_url(client, day: date | None = None) -> str:
    return site_url(client, f"exercise/diary/{client.effective_username}", day)


def exercise_page(client, day: date) -> tuple[lh.HtmlElement, str]:
    return get_page(client, _exercise_diary_url(client, day))


def _field_key(heading: str) -> str:
    words = "".join(
        character if character.isalnum() else " " for character in heading.lower()
    ).split()
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


def exercise_entries(doc: lh.HtmlElement) -> list[dict]:
    entries = []
    for table in doc.xpath("//table[contains(@class, 'table0')]"):
        headings = table.xpath("./thead/tr[1]/td")
        if not headings:
            continue
        section = headings[0].text_content().strip().lower()
        field_keys = [_field_key(heading.text_content()) for heading in headings[1:]]
        for row in table.xpath("./tbody/tr[not(@class)]"):
            delete_links = row.xpath("./td[contains(@class, 'delete')]//a/@href")
            if not delete_links:
                continue
            cells = row.xpath("./td")
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


def delete_exercise(client, day: date, query: str, all_matches: bool = False) -> dict:
    doc, token = exercise_page(client, day)
    entries = exercise_entries(doc)
    if all_matches:
        targets = find_entries(entries, query)
        if not targets:
            raise no_matching_entry(entries, query, None, day)
    else:
        targets = [resolve_entry(entries, query, None, day, advice=ALL_MATCHES_ADVICE)]
    for entry in targets:
        post_legacy_delete(
            client,
            "exercise/remove",
            entry["entry_id"],
            token,
            referer=_exercise_diary_url(client),
        )
    return {"day": day.isoformat(), "removed": targets, "count": len(targets)}
