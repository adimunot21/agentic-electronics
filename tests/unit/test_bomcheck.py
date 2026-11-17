"""MPN consistency check: decoders + negative controls (incl. the exact D1 swap)."""
import pytest

from ae import bomcheck as b

F = lambda mpn, tol="5%", diel="C0G", volt="", status="verified": {"MPN": mpn, "MPN_Status": status, "Tolerance": tol, "Dielectric": diel, "Voltage": volt}
CAP = "Capacitor_SMD:C_0805_2012Metric"


def test_decoders_match_manufacturer_sheets_read_earlier():
    c = b.decode("C0805C183J5GACTU")
    assert (c["size"], round(c["value"] * 1e9, 3), c["tol"], c["volt"], c["diel"]) == ("0805", 18.0, 5.0, 50.0, "C0G")
    c = b.decode("C1206C106K8RACTU")
    assert (round(c["value"] * 1e6, 3), c["tol"], c["volt"], c["diel"]) == (10.0, 10.0, 10.0, "X7R")
    assert round(b.decode("C0805C331J5GACTU")["value"] * 1e12) == 330
    assert b.decode("RC0603FR-0764K9L")["value"] == pytest.approx(64900) and b.decode("RC0603FR-07475RL")["value"] == pytest.approx(475)
    assert b.decode("RC0603FR-076K49L")["value"] == pytest.approx(6490) and b.decode("RC0603FR-0717K8L")["tol"] == 1.0
    r = b.decode("RT0603BRD0710KL")
    assert (r["value"], r["tol"], r["tcr_ppm"]) == (10000.0, 0.1, 25)


def test_consistent_part_passes():
    assert b.check_line("C1", F("C0805C183J5GACTU"), "18n", CAP, 3.4)["outcome"] == "passed"


def test_d1_swapped_value_is_caught():
    c1 = b.check_line("C1", F("C0805C183J5GACTU"), "1n", CAP, 3.4)      # schematic says 1n, MPN is the 18 nF part
    c2 = b.check_line("C2", F("C0805C102J5GACTU"), "18n", CAP, 3.4)
    assert c1["outcome"] == c2["outcome"] == "failed" and "VALUE" in c1["problems"][0]


def test_package_tolerance_dielectric_and_rating_mismatches_are_caught():
    assert "PACKAGE" in b.check_line("C1", F("C0805C183J5GACTU"), "18n", "Capacitor_SMD:C_0603_1608Metric", 3.4)["problems"][0]
    assert any("TOLERANCE" in p for p in b.check_line("C1", F("C0805C183J5GACTU", tol="1%"), "18n", CAP, 3.4)["problems"])
    assert any("DIELECTRIC" in p for p in b.check_line("C1", F("C0805C183J5GACTU", diel="X7R"), "18n", CAP, 3.4)["problems"])
    assert any("RATING" in p for p in b.check_line("C7", F("C1206C106K8RACTU", tol="20%", diel="X7R"), "10u", "Capacitor_SMD:C_1206_3216Metric", 12.0)["problems"])
    assert any("TOLERANCE" in p for p in b.check_line("R1", F("RC0603FR-0764K9L", tol="0.1%", diel=""), "64.9k", "Resistor_SMD:R_0603_1608Metric", 3.4)["problems"])


def test_unresolved_is_never_a_pass():
    for mpn in ("", "UNRESOLVED", "unselected", "generic header"):
        assert b.check_line("J1", F(mpn), "x", "Connector", 3.4)["outcome"] == "unresolved"


def test_derating_warning_is_reported_not_hidden():
    line = b.check_line("C7", F("C1206C106K8RACTU", tol="20%", diel="X7R"), "10u", "Capacitor_SMD:C_1206_3216Metric", 5.25)
    assert line["outcome"] == "passed" and any("derating warning" in n for n in line["notes"])
