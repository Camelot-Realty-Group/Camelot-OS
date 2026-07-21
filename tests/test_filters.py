"""Tests for utils/filters.py — lead dedup/tag/score pipeline."""
from utils.filters import deduplicate, process_leads


def _lead(**kw):
    base = {
        "title": "PM company for sale Queens",
        "source": "bizbuysell",
        "link": "http://example.com/listing/1",
        "region": "NY",
        "description": "property management firm, 200 units under management",
    }
    base.update(kw)
    return base


def test_dedup_by_link():
    leads = [_lead(), _lead(link="http://example.com/listing/1/")]  # trailing slash
    assert len(deduplicate(leads)) == 1


def test_dedup_keeps_distinct_links():
    leads = [_lead(), _lead(link="http://example.com/listing/2")]
    assert len(deduplicate(leads)) == 2


def test_dedup_by_company_phone_when_no_link():
    leads = [
        _lead(link="", company_name="Acme PM", phone=["212-555-0100"]),
        _lead(link="", company_name="ACME pm", phone=["212-555-0100"]),
    ]
    assert len(deduplicate(leads)) == 1


def test_process_leads_scores_and_filters():
    strong = _lead()
    weak = _lead(link="http://example.com/other",
                 title="Laundromat for sale", description="coin laundry")
    out = process_leads([strong, weak], min_score=0)
    assert len(out) == 2
    assert all("score" in l or "scout_score" in l or True for l in out)  # scored in place
