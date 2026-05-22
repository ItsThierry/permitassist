from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
sys.path.insert(0, str(API))


def test_finalize_promotes_permitassist3_exact_name_packet(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    monkeypatch.setenv("PERMITASSIST3_TICKET_PATH", str(tmp_path / "tickets.jsonl"))
    import server  # noqa: WPS433

    legacy = {
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_type": "Permit required — exact permit type needs AHJ verification",
        "permit_name": "Permit required — exact permit type needs AHJ verification",
        "permits_required": [{"permit_type": "Permit required — exact permit type needs AHJ verification", "required": True}],
        "sources": [],
    }
    out = server.finalize_permit_lookup_result(
        legacy,
        "Restaurant tenant improvement",
        "Tampa",
        "FL",
        evidence_allowed=False,
        explicit_vertical="restaurant_ti",
    )
    assert out["final_answer_state"] == server.PA3_FINAL_VERIFIED
    assert "Commercial Alteration Building Permit" in out["permit_type"]
    assert out["permit_type_verified"] is True
    assert out["completion_ticket"] is None
    assert "Manual filing path confirmation in progress" not in str(out)
    assert "exact permit type needs AHJ verification" not in str(out)


def test_finalize_missing_exact_name_is_non_final_ticket(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    ticket_path = tmp_path / "tickets.jsonl"
    monkeypatch.setenv("PERMITASSIST3_TICKET_PATH", str(ticket_path))
    import server  # noqa: WPS433

    legacy = {
        "permit_verdict": "YES",
        "permit_required": True,
        "permit_type": "Permit required — exact permit type needs AHJ verification",
        "permit_name": "Permit required — exact permit type needs AHJ verification",
        "permits_required": [{"permit_type": "Permit required — exact permit type needs AHJ verification", "required": True}],
        "sources": [],
    }
    out = server.finalize_permit_lookup_result(
        legacy,
        "Restaurant tenant improvement",
        "Phoenix",
        "AZ",
        evidence_allowed=False,
        explicit_vertical="restaurant_ti",
    )
    assert out["final_answer_state"] == server.PA3_NON_FINAL
    assert out["permit_verdict"] == "NON_FINAL"
    assert out["permit_type"] is None
    assert out["permit_name"] is None
    assert out["permits_required"] == []
    assert out["completion_ticket"]["ticket_id"].startswith("pa3_")
    assert out["completion_ticket"]["final_answer_state"] == server.PA3_NON_FINAL
    assert ticket_path.exists()
    assert out["completion_ticket"]["ticket_id"] in ticket_path.read_text(encoding="utf-8")
    assert "Manual filing path confirmation in progress" not in str(out)
    assert "exact permit type needs AHJ verification" not in str(out)
