"""Tests for concierge_bot/templates_registry.py — catalog integrity."""
from pathlib import Path

import templates_registry as tr

CONCIERGE_DIR = Path(__file__).parent.parent / "concierge_bot"


def test_catalog_size():
    assert len(tr.TEMPLATES) == 23


def test_every_referenced_file_exists_on_disk():
    missing = []
    for tid, meta in tr.TEMPLATES.items():
        for key in ("docx", "pdf", "fillable_pdf"):
            fn = meta.get(key)
            if fn and not (CONCIERGE_DIR / tr.LIBRARY_DIR / fn).exists():
                missing.append((tid, key, fn))
        if meta.get("has_autofill"):
            if not (CONCIERGE_DIR / tr.MASTERS_DIR / meta["master_docx"]).exists():
                missing.append((tid, "master_docx", meta["master_docx"]))
    assert not missing, f"registry references missing files: {missing}"


def test_autofill_templates_have_fields():
    for tid, meta in tr.TEMPLATES.items():
        if meta.get("has_autofill"):
            assert meta.get("fields"), f"{tid} is autofillable but has no field schema"
            assert meta.get("master_docx"), f"{tid} is autofillable but has no master"


def test_field_keys_unique_per_template():
    for tid, meta in tr.TEMPLATES.items():
        keys = [f["key"] for f in meta.get("fields", [])]
        assert len(keys) == len(set(keys)), f"duplicate field keys in {tid}"


def test_list_templates_and_categories():
    all_t = tr.list_templates()
    assert len(all_t) == 23
    cats = tr.list_categories()
    assert len(cats) == 6
    for cat in cats:
        subset = tr.list_templates(category=cat)
        assert subset, f"category {cat} has no templates"
        assert all(t["category"] == cat for t in subset)


def test_get_template():
    assert tr.get_template("work-order-request-form")["has_autofill"] is True
    assert tr.get_template("nope") is None
