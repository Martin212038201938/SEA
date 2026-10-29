"""Aktuelle Kampagnennamen und -status auf die gespeicherte Historie übertragen.

Die Leistungsberichte liefern nur Tage mit Impressionen. Wird eine pausierte Kampagne umbenannt, käme der neue
Name sonst nie an, und nach einer Umbenennung stünden alte und neue Namen nebeneinander. Deshalb liest der
Nachtlauf Name und Status direkt aus der Kampagnenverwaltung und schreibt sie auf alle Zeilen der Kampagne.
"""
from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..models import AdGroupDaily, CampaignDaily


def sync_campaigns(session: Session, platform: str, campaigns: dict[str, dict[str, str]]) -> int:
    """campaigns: {campaign_id: {"name": ..., "status": ..., "bidding_strategy": ... (optional)}}.
    Gibt die Zahl der Kampagnen zurück, deren Name, Status oder Strategie sich geändert hat."""
    changed = 0
    for cid, info in campaigns.items():
        cid = str(cid)
        values = {k: v for k, v in (("campaign_name", info.get("name")), ("status", info.get("status")), ("bidding_strategy", info.get("bidding_strategy"))) if v}
        if not values:
            continue
        res = session.execute(
            update(CampaignDaily)
            .where(CampaignDaily.platform == platform, CampaignDaily.campaign_id == cid)
            .where(
                (CampaignDaily.campaign_name != values.get("campaign_name", CampaignDaily.campaign_name))
                | (CampaignDaily.status != values.get("status", CampaignDaily.status))
                | (CampaignDaily.bidding_strategy != values.get("bidding_strategy", CampaignDaily.bidding_strategy))
            )
            .values(**values)
        )
        if info.get("name"):
            session.execute(
                update(AdGroupDaily)
                .where(AdGroupDaily.platform == platform, AdGroupDaily.campaign_id == cid, AdGroupDaily.campaign_name != info["name"])
                .values(campaign_name=info["name"])
            )
        if res.rowcount:
            changed += 1
    return changed
