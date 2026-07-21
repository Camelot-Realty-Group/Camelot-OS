"""
docgen.py — Concierge Bot Document Generator
Camelot Property Management Services Corp.

Fills a merge-tag master .docx (masters/<file>.docx, containing Jinja2-style
{{ field_key }} tags) with a dict of answers, using docxtpl.

Only templates with has_autofill=True in templates_registry.py have a
master file wired here. Everything else is served as a static branded
docx/pdf, or a genuinely-fillable PDF the user types directly into.
"""

import io
from pathlib import Path
from typing import Any, Dict

from templates_registry import MASTERS_DIR, get_template

BOT_DIR = Path(__file__).parent.resolve()


class TemplateNotAutofillable(Exception):
    """Raised when generate_docx() is called on a template with no merge-tag master."""


def generate_docx(template_id: str, answers: Dict[str, Any]) -> bytes:
    """
    Render a merge-tag master .docx with the given answers and return the
    filled document as bytes.

    Args:
        template_id: Registry key (e.g. "work-order-request-form").
        answers: Dict of field_key -> value. Missing keys render as blank.

    Returns:
        Filled .docx file content as bytes.

    Raises:
        TemplateNotAutofillable: If the template has no master_docx wired.
        FileNotFoundError: If the master file is missing on disk.
    """
    meta = get_template(template_id)
    if not meta or not meta.get("has_autofill"):
        raise TemplateNotAutofillable(
            f"Template '{template_id}' does not have a merge-tag master wired for autofill. "
            "It can still be downloaded as a static docx/pdf or fillable PDF."
        )

    master_path = BOT_DIR / MASTERS_DIR / meta["master_docx"]
    if not master_path.exists():
        raise FileNotFoundError(f"Master file not found: {master_path}")

    # Imported lazily so the rest of the bot works even before docxtpl is
    # installed (e.g. during local review without a full pip install).
    from docxtpl import DocxTemplate

    doc = DocxTemplate(str(master_path))

    # Fill every declared field; unanswered fields render as an empty string
    # rather than leaving the literal {{ tag }} in the output.
    context = {f["key"]: "" for f in meta.get("fields", [])}
    context.update({k: v for k, v in answers.items() if v is not None})

    doc.render(context)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
