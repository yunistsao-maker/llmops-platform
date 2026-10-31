"""Request gates. One rule set shared by the portal form preview and request-api.

Rule (tentative, waiting for confirmation in the design doc):
  G0  intake                              always (automatic)
  G2  compliance review                   data class Security B, Security A/AA or Other, or an external service
  G8  two-week trial                      external service, or 10,000 or more requests a day
  G9  launch (key provisioning)           always, after every required review gate is approved
"""
from __future__ import annotations

__all__ = ["DATA_CLASSES", "GATE_NAMES", "REVIEW_GATES", "required_gates", "next_gate"]

DATA_CLASSES = ("other", "property", "sec-c", "sec-b", "sec-a")
GATE_NAMES = {
    "G0": ("登記", "Intake"),
    "G1": ("研究", "Research"),
    "G2": ("法遵審查", "Compliance"),
    "G3": ("相容測試", "Compatibility"),
    "G4": ("效能測試", "Performance"),
    "G5": ("品質評測", "Quality"),
    "G6": ("安全測試", "Security"),
    "G7": ("成本審查", "Cost review"),
    "G8": ("試用", "Beta"),
    "G9": ("上線", "Launch"),
    "G10": ("退役", "Retirement"),
}
REVIEW_GATES = ("G2", "G8")   # need a human decision


def required_gates(request: dict) -> list[str]:
    gates = ["G0"]
    data_class = request.get("data_class")
    external = bool(request.get("external"))
    if data_class in ("sec-b", "sec-a", "other") or external:
        gates.append("G2")
    try:
        rpd = int(request.get("rpd") or 0)
    except (TypeError, ValueError):
        rpd = 0
    if external or rpd >= 10000:
        gates.append("G8")
    gates.append("G9")
    return gates


def next_gate(required: list[str], approved: set[str]) -> str | None:
    """First review gate still waiting for approval, else None (ready for G9)."""
    for gate in required:
        if gate in REVIEW_GATES and gate not in approved:
            return gate
    return None
