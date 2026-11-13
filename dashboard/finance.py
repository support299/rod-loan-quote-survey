"""
Gross profit and contribution profit per deal.

Rules (Rod, Nov 2026, plus the brief):
  gross profit         = the GHL Opportunity Value (Rod: "Opportunity Value = gross profit
                         on that loan"). If it is blank/0, fall back to the Origination Fee,
                         else Origination Points x Total Loan Amount; both fallbacks are
                         flagged as estimated.
  AE commission        = the loan's "AE Commission" field ($), else a % of gross profit by
                         the loan's "Deal Sourced By": Flip Funding → 30%, AE → 50% (editable
                         in Settings), else the AE's own % (Team), else the company default %.
  net revenue          = gross profit - AE commission
  broker fee           = deducted only when Flip Funding pays the broker ("Broker Paid By").
                         Title Company → no cost to Flip Funding. Blank → the old rule: a deal
                         with a broker costs its Broker Fee, a deal without a broker costs 0.
  contribution profit  = net revenue - broker fee
Missing inputs are reported as missing, never silently treated as zero.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from .constants import BROKER_PAID_BY_COMPANY, BROKER_PAID_BY_TITLE, SOURCED_BY_AE, SOURCED_BY_COMPANY
from .models import CompanySettings, DashboardProfile

HUNDRED = Decimal("100")
CENT = Decimal("0.01")


def _is(value, option):
    return (value or "").strip().lower() == option.lower()


def sourced_by_ae(deal):
    return _is(deal.deal_sourced_by, SOURCED_BY_AE)


def sourced_by_company(deal):
    return _is(deal.deal_sourced_by, SOURCED_BY_COMPANY)


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


def _gross_profit(deal):
    if deal.monetary_value:  # GHL stores 0 when the value was never entered
        return deal.monetary_value, "opportunity_value"
    if deal.origination_fee is not None:
        return deal.origination_fee, "origination_fee"
    if deal.origination_points is not None and deal.total_loan_amount:
        return (deal.origination_points / HUNDRED * deal.total_loan_amount).quantize(CENT), "points_estimate"
    return None, ""


def _commission(deal, revenue, fctx):
    if deal.ae_commission is not None:
        return deal.ae_commission, "field", None
    if revenue is None:
        return None, "", None
    s = fctx.settings
    if sourced_by_company(deal):
        pct, source = s.company_sourced_commission_pct, "sourced_company"
    elif sourced_by_ae(deal):
        pct, source = s.ae_sourced_commission_pct, "sourced_ae"
    else:
        pct, source = fctx.commission_pct(credited_ae(deal)), "rate"
    if pct is None:
        return None, "", None
    return (revenue * pct / HUNDRED).quantize(CENT), source, pct


def _broker_cost(deal):
    if _is(deal.broker_paid_by, BROKER_PAID_BY_TITLE):
        return Decimal("0"), "title_company"
    if _is(deal.broker_paid_by, BROKER_PAID_BY_COMPANY):
        return deal.broker_fee, "flip_funding"
    has_broker = bool(deal.broker_name or deal.broker_email)
    if deal.broker_fee is not None:
        return deal.broker_fee, ""  # "Broker Paid By" blank: assumed paid by Flip Funding
    return (Decimal("0"), "") if not has_broker else (None, "")


def deal_financials(deal, fctx):
    missing = []
    revenue, revenue_source = _gross_profit(deal)
    if revenue is None:
        missing.append("opportunity_value")

    commission, commission_source, commission_pct = _commission(deal, revenue, fctx)
    if commission is None:
        missing.append("ae_commission")

    broker_cost, broker_paid_by = _broker_cost(deal)
    if broker_cost is None:
        missing.append("broker_fee")

    net = revenue - commission if revenue is not None and commission is not None else None
    profit = net - broker_cost if net is not None and broker_cost is not None else None
    return {
        "loan_amount": deal.total_loan_amount,
        "revenue": revenue,
        "revenue_source": revenue_source,
        "revenue_estimated": revenue_source in ("origination_fee", "points_estimate"),
        "ae_commission": commission,
        "commission_source": commission_source,
        "commission_pct": commission_pct,
        "broker_cost": broker_cost,
        "broker_paid_by": broker_paid_by,
        "net_revenue": net,
        "profit": profit,
        "complete": not missing,
        "missing": missing,
    }
