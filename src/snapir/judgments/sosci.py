import xml.etree.ElementTree as ET
from pathlib import Path

DOCTYPE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<!DOCTYPE surveyContent SYSTEM "
    '"https://www.soscisurvey.de/templates/doctype.survey.dtd">\n'
)

# The grading scale lives in the answer labels (SoSci renders **bold**), so
# the task pages carry no separate grading guide. Edit the wording here, not
# in the generated XML — regenerating overwrites hand edits.
RELEVANCE_ITEMS = [
    "**Highly relevant (2):** The content fully meets the information need, "
    "i.e., it directly answers the question in the description. The website "
    "does not have to contain all the information, but it must provide an "
    "answer on its own.",
    "**Relevant (1):** The website answers part of the information need, but "
    "would need to be combined with other information to provide a complete "
    "answer.",
    "**Not relevant (0):** Everything else.",
]

# Same grades for AI overviews (values stay comparable), reworded so the
# labels talk about the overview rather than a website.
AI_RELEVANCE_ITEMS = [item.replace("The website", "The overview") for item in RELEVANCE_ITEMS]

RELEVANCE_TITLE = "Is this website relevant for the topic?"
RELEVANCE_EXPLANATION = (
    "Please rate how well the website shown in the screenshot answers the user's information need."
)
AI_RELEVANCE_TITLE = "Is this AI overview relevant for the topic?"
AI_RELEVANCE_EXPLANATION = (
    "Please rate how well the AI overview shown above answers the user's information need."
)
RATIONALE_TITLE = "What were the main reasons for your relevance ratings on this topic?"
RATIONALE_EXPLANATION = (
    "Please summarise in 2-3 sentences what mainly made the websites you "
    "just rated relevant or irrelevant for this topic."
)


def survey(title: str) -> ET.Element:
    root = ET.Element("surveyContent", {"version": "2.4"})
    ET.SubElement(root, "program").text = "sosci"
    ET.SubElement(root, "version").text = "3.8.08"
    ET.SubElement(root, "title").text = title
    ET.SubElement(root, "description")
    ET.SubElement(root, "language").text = "eng"
    attrs = ET.SubElement(root, "attributes.specific", {"program": "ofb"})
    ET.SubElement(attrs, "attr", {"id": "language"}).text = "eng"
    return root


def section(root: ET.Element, section_id: str, title: str) -> ET.Element:
    sec = ET.SubElement(root, "section")
    ET.SubElement(sec, "title").text = title
    attrs = ET.SubElement(sec, "attributes.specific", {"program": "ofb"})
    ET.SubElement(attrs, "attr", {"id": "id"}).text = section_id
    return sec


def _attrs(elem: ET.Element, **values: str) -> None:
    attrs = ET.SubElement(elem, "attributes.specific", {"program": "ofb"})
    for key, value in values.items():
        ET.SubElement(attrs, "attr", {"id": key.replace("__", ".")}).text = value


def question(
    sec: ET.Element,
    description: str,
    number: int,
    title: str,
    explanation: str,
    items: list[str],
    cls: str = "select",
) -> None:
    q = ET.SubElement(sec, "question")
    ET.SubElement(q, "description").text = description
    ET.SubElement(q, "class").text = cls
    ET.SubElement(q, "title").text = title
    ET.SubElement(q, "explanation").text = explanation or None
    ET.SubElement(q, "inputPosition").text = "right"
    ET.SubElement(q, "order").text = "default"
    _attrs(q, id=str(number), pos=str(number), restrictions__answered="yes")
    for i, text in enumerate(items, start=1):
        item = ET.SubElement(q, "item")
        ET.SubElement(item, "text").text = text or None
        _attrs(item, id=str(i), pos=str(i))


def write(root: ET.Element, path: Path) -> None:
    ET.indent(root, space="    ")
    path.write_text(DOCTYPE + ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")
    print(f"{sum(1 for _ in root.iter('question')):>3} question(s) → {path}")


def create_questions(relevance, ai_overviews, rationale, output):
    # One flat pool for every document page — set and SERP section are
    # recorded in the questionnaire's manifest.csv, not in the question.
    # AI overviews keep their own description because their wording differs.
    web = (RELEVANCE_TITLE, RELEVANCE_EXPLANATION, RELEVANCE_ITEMS)
    ai = (AI_RELEVANCE_TITLE, AI_RELEVANCE_EXPLANATION, AI_RELEVANCE_ITEMS)
    slots = [
        ("relevance", relevance, *web),
        ("relevance-ai", ai_overviews, *ai),
    ]
    # Validate before writing anything — a section over the limit would leave
    # a file whose later questions have no usable reference.
    for name, count in (
        ("relevance", sum(c for _, c, *_ in slots)),
        ("rationale", rationale),
    ):
        if count > 99:
            raise ValueError(
                f"{count} {name} questions exceed SoSci's 99 per section — "
                "question ids are 2-digit, so the surplus would get "
                "unusable references. Lower the counts or split the section."
            )

    output.mkdir(parents=True, exist_ok=True)
    root = survey("Relevance")
    sec = section(root, "R0", "Relevance")
    number = 0
    for description, count, title, explanation, items in slots:
        for _ in range(count):
            number += 1
            question(sec, description, number, title, explanation, items)
    write(root, output / "questions-relevance.xml")

    root = survey("Rationale")
    sec = section(root, "G0", "Rationale")
    for number in range(1, rationale + 1):
        question(
            sec,
            "Rationale",
            number,
            RATIONALE_TITLE,
            RATIONALE_EXPLANATION,
            [""],
            cls="text",
        )
    write(root, output / "questions-rationale.xml")

    print(
        "Re-import these files into SoSci. Changing any slot count renumbers "
        "the later questions, so do it before collecting responses."
    )
    return output
