"""Synthetic tool used by the AICT observability demo agent."""

_POLICIES = {
    "external vendor risk policy": {
        "status": "review_required",
        "owner": "Demo Risk Team",
        "next_review": "2026-12-15",
    },
    "model monitoring standard": {
        "status": "compliant",
        "owner": "Demo AI Governance Team",
        "next_review": "2027-01-31",
    },
}


def get_demo_policy_status(policy_name: str) -> dict[str, str]:
    """Return synthetic governance status for a named demo policy.

    Args:
        policy_name: Policy name to look up. Supported examples are
            "external vendor risk policy" and "model monitoring standard".

    Returns:
        A synthetic status dictionary. No production or customer data is used.
    """
    key = " ".join(policy_name.lower().split())
    return {"policy": key, **_POLICIES.get(key, {"status": "not_found"})}
