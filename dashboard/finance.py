"""
Revenue and contribution profit per deal.

Rules (Rod, Nov 2026, plus the brief):
  company revenue      = Origination Fee (loan principal and pass-through fees excluded).
                         If the fee is blank but Origination Points and Total Loan Amount
                         are set, revenue = points% x amount and is flagged estimated.
  AE commission        = the loan's "AE Commission" field, else the AE's commission %
                         (dashboard settings) x revenue, else the company default %.
  net revenue          = revenue - AE commission  (Rod's "origination fee minus AE commission")
  contribution profit  = net revenue - Broker Fee (referral payout; Rod: "I will reflect as
                         Broker fee"). A deal without a broker has no referral cost.
Missing inputs are reported as missing, never silently treated as zero.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from .models import CompanySettings, DashboardProfile

HUNDRED = Decimal("100")


@dataclass
class FinanceContext:
    settings: CompanySettings
    commission_pct_by_ae: dict = field(default_factory=dict)

    @classmethod
    def build(cls, settings=None):
        settings = settings or CompanySettings.load()
        rates = {
            p.ae_name.strip().lower(): p.commission_pct
            for p in DashboardProfile.objects.exclude(ae_name="").exclude(commission_pct__isnull=True)
        }
        return cls(settings=settings, commission_pct_by_ae=rates)

    def commission_pct(self, ae_name):
        pct = self.commission_pct_by_ae.get((ae_name or "").strip().lower())
        if pct is None:
            pct = self.settings.default_ae_commission_pct
        return pct


def credited_ae(deal):
    """AE assigned to the loan at closing gets the credit (Rod)."""
    return deal.ae_at_funding or deal.ae_name or ""


def deal_financials(deal, fctx):
    missing = []
    revenue, estimated = deal.origination_fee, False
    if revenue is None and deal.origination_points is not None and deal.total_loan_amount:
        revenue = (deal.origination_points / HUNDRED * deal.total_loan_amount).quantize(Decimal("0.01"))
        estimated = True
    if revenue is None:
        missing.append("origination_fee")

    commission, commission_source = deal.ae_commission, "field" if deal.ae_commission is not None else ""
    if commission is None and revenue is not None:
        pct = fctx.commission_pct(credited_ae(deal))
        if pct is not None:
            commission = (revenue * pct / HUNDRED).quantize(Decimal("0.01"))
            commission_source = "rate"
    if commission is None:
        missing.append("ae_commission")

    has_broker = bool(deal.broker_name or deal.broker_email)
    broker_cost = deal.broker_fee if deal.broker_fee is not None else (Decimal("0") if not has_broker else None)
    if broker_cost is None:
        missing.append("broker_fee")

    net = revenue - commission if revenue is not None and commission is not None else None
    profit = net - broker_cost if net is not None and broker_cost is not None else None
    return {
        "loan_amount": deal.total_loan_amount,
        "revenue": revenue,
        "revenue_estimated": estimated,
        "ae_commission": commission,
        "commission_source": commission_source,
        "broker_cost": broker_cost,
        "net_revenue": net,
        "profit": profit,
        "complete": not missing,
        "missing": missing,
    }
