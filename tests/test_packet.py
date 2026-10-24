"""Packet renders only from month summaries and evidence, with working source links."""
import re
from pathlib import Path

from web import packet

DOCS = Path(__file__).resolve().parent.parent / "demo" / "data" / "documents"


def test_demo_packet_uses_may_then_april():
    p = packet.demo_packet()
    assert p["months"]["2026-05"]["floor_met_by"] == "income"
    assert p["months"]["2026-04"]["floor_met_by"] == "combination"
    assert p["months"]["2026-03"]["floor_met_by"] == "none"
    assert p["months"]["2026-03"]["gross_verified_cents"] == 53100


def test_every_source_link_points_to_a_real_file():
    html = packet.render(packet.demo_packet(), doc_base="")
    links = re.findall(r'<a href="([^"]+\.png)"', html)
    assert links and all((DOCS / l).exists() for l in links)


def test_packet_says_the_county_decides_and_is_marked_sample():
    html = packet.render(packet.demo_packet())
    assert "The county decides" in html and "SAMPLE" in html and "Generated" in html


def test_duplicate_screenshot_is_not_evidence():
    html = packet.render(packet.demo_packet(), doc_base="")
    assert "earn-2026-03-w2b" not in html


def test_money_formatting():
    assert packet.money(72000) == "$720.00"
    assert packet.money(-5800) == "−$58.00"
    assert packet.money(None) == "–"
