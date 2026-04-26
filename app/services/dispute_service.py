def route_dispute(dispute_type: str, target_org_id: str | None) -> tuple[str, str | None]:
    """Return (recipient_type, recipient_org_id) based on dispute type."""
    if dispute_type == "verification":
        return "platform_admin", None
    return "org_admin", target_org_id
