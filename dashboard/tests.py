from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .constants import LOAN_PIPELINE_NAME, PROCESSING_PIPELINE_NAME, STAGE_FLOW
from .finance import FinanceContext, deal_financials
from .issues import refresh_issues
from .metrics import load_dataset, overview, scorecards
from .models import CompanySettings, DashboardProfile, Deal, DealEvent, DealIssue, DealMilestone, PipelineStage
from .periods import business_days, prior_period, resolve_period
from .sync import SyncContext, _load_stages, parse_money, upsert_opportunity

FIELDS = {
    "f_ae": "Account Executive",
    "f_an": "Loan Analysts",
    "f_amt": "Total Loan Amount",
    "f_fee": "Origination Fee",
    "f_pts": "Origination Points",
    "f_bk": "Broker Name",
    "f_bkfee": "Broker Fee",
}
PIPE_IDS = {LOAN_PIPELINE_NAME: "p01", PROCESSING_PIPELINE_NAME: "p02"}


def make_stages():
    for i, (key, pipeline, name) in enumerate(STAGE_FLOW):
        PipelineStage.objects.create(ghl_id=f"s_{key}", pipeline_id=PIPE_IDS[pipeline], pipeline_name=pipeline, name=name, position=i, stage_key=key)
    PipelineStage.objects.create(ghl_id="s_other", pipeline_id="p99", pipeline_name="Leads", name="New", position=0)


def make_ctx():
    ctx = SyncContext(account=SimpleNamespace(location_id="loc1", access_token="tok"))
    _load_stages(ctx)
    ctx.field_names = dict(FIELDS)
    return ctx


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def opp(opp_id="o1", stage="quick_app", status="open", created=None, stage_at=None, **fields):
    created = created or timezone.now() - timedelta(days=20)
    key_to_pipe = {k: p for k, p, _ in STAGE_FLOW}
    return {
        "id": opp_id,
        "name": f"Deal {opp_id}",
        "pipelineId": PIPE_IDS.get(key_to_pipe.get(stage), "p99"),
        "pipelineStageId": f"s_{stage}" if stage in key_to_pipe else "s_other",
        "status": status,
        "contactId": fields.pop("contact", "c1"),
        "contact": {"id": "c1", "name": "Borrower One"},
        "assignedTo": fields.pop("assigned", ""),
        "createdAt": iso(created),
        "updatedAt": iso(timezone.now()),
        "lastStageChangeAt": iso(stage_at or created),
        "lastStatusChangeAt": iso(stage_at or created),
        "customFields": [{"id": fid, "fieldValueString": str(v)} for fid, v in fields.items()],
    }


class ParsingTests(TestCase):
    def test_parse_money(self):
        self.assertEqual(parse_money("$12,500.50"), Decimal("12500.50"))
        self.assertEqual(parse_money(2.5), Decimal("2.5"))
        self.assertIsNone(parse_money(""))
        self.assertIsNone(parse_money("n/a"))

    def test_periods(self):
        p = resolve_period("custom", "2026-10-01", "2026-10-15")
        prior = prior_period(p)
        self.assertEqual((prior.start, prior.end), (date(2026, 9, 16), date(2026, 9, 30)))
        mtd = resolve_period("mtd")
        self.assertEqual(prior_period(mtd).start.day, 1)
        self.assertEqual(business_days(date(2026, 11, 2), date(2026, 11, 8)), 5)


class SyncTests(TestCase):
    def setUp(self):
        make_stages()
        self.ctx = make_ctx()

    def test_lifecycle_records_events_and_milestones(self):
        created = timezone.now() - timedelta(days=30)
        deal = upsert_opportunity(opp(stage="quick_app", created=created, f_ae="Anna"), self.ctx)
        self.assertEqual(deal.stage_key, "quick_app")
        self.assertEqual(deal.ae_name, "Anna")
        self.assertTrue(DealMilestone.objects.filter(deal=deal, key="quick_app").exists())

        t1 = timezone.now() - timedelta(days=10)
        upsert_opportunity(opp(stage="onboarded", created=created, stage_at=t1, f_ae="Anna"), self.ctx)
        t2 = timezone.now() - timedelta(days=2)
        deal = upsert_opportunity(opp(stage="funded", created=created, stage_at=t2, f_ae="Anna", f_amt="500000"), self.ctx)

        self.assertEqual(deal.funded_at, DealMilestone.objects.get(deal=deal, key="funded").reached_at)
        self.assertEqual(deal.ae_at_application, "Anna")
        self.assertEqual(deal.ae_at_funding, "Anna")
        self.assertEqual(deal.total_loan_amount, Decimal("500000"))
        self.assertEqual(DealEvent.objects.filter(deal=deal, kind="stage_change").count(), 2)
        # Skipped stages are not invented.
        self.assertFalse(DealMilestone.objects.filter(deal=deal, key="underwriting").exists())

    def test_lost_then_reopened(self):
        upsert_opportunity(opp(stage="term_sheet_sent"), self.ctx)
        deal = upsert_opportunity(opp(stage="term_sheet_sent", status="abandoned"), self.ctx)
        self.assertTrue(deal.is_lost)
        self.assertIsNotNone(deal.lost_at)
        deal = upsert_opportunity(opp(stage="term_sheet_sent", status="open"), self.ctx)
        self.assertFalse(deal.is_lost)
        self.assertIsNone(deal.lost_at)
        self.assertEqual(DealEvent.objects.filter(deal=deal, kind="status_change").count(), 2)

    def test_out_of_scope_pipeline_ignored_and_left_scope(self):
        self.assertIsNone(upsert_opportunity(opp("o9", stage="leads"), self.ctx))
        upsert_opportunity(opp("o2", stage="under_review"), self.ctx)
        deal = upsert_opportunity(opp("o2", stage="leads"), self.ctx)
        self.assertFalse(deal.in_scope)

    def test_owner_fallback_only_for_known_aes(self):
        from .models import GHLUser

        self.ctx.users = {
            "u_an": GHLUser.objects.create(ghl_id="u_an", name="Leoma Smith"),
            "u_ae": GHLUser.objects.create(ghl_id="u_ae", name="Anna Lee"),
        }
        self.ctx.known_aes = {"anna": "Anna"}
        self.assertEqual(upsert_opportunity(opp("x1", assigned="u_an"), self.ctx).ae_name, "")
        self.assertEqual(upsert_opportunity(opp("x2", assigned="u_ae"), self.ctx).ae_name, "Anna")
        self.assertEqual(upsert_opportunity(opp("x3", f_ae="N/A", assigned="u_an"), self.ctx).ae_name, "")

    def test_backfilled_milestones_excluded_from_cohorts(self):
        from .metrics import milestone_in

        user = get_user_model().objects.create_superuser("su@x.com", password="pw123456")
        upsert_opportunity(opp("b1", stage="onboarded", stage_at=timezone.now() - timedelta(days=1)), self.ctx)
        ms = DealMilestone.objects.get(deal_id="b1", key="onboarded")
        self.assertEqual(ms.source, "ghl_last_stage_change")
        ds = load_dataset(user)
        self.assertEqual(milestone_in(ds, "onboarded", resolve_period("rolling_30")), [])
        self.assertIsNotNone(ds.ms(ds.deals[0], "onboarded"))  # still used for days-in-stage

    def test_partial_webhook_keeps_status_when_absent(self):
        upsert_opportunity(opp(stage="term_sheet_sent", status="open", f_ae="Rod"), self.ctx)
        partial = {"id": "o1", "pipelineId": "p01", "pipelineStageId": "s_term_sheet_accepted"}
        deal = upsert_opportunity(partial, self.ctx, source="webhook", partial=True)
        self.assertEqual(deal.status, "open")
        self.assertEqual(deal.ae_name, "Rod")
        self.assertEqual(deal.stage_key, "term_sheet_accepted")
        self.assertTrue(DealMilestone.objects.filter(deal=deal, key="term_sheet_accepted", source="webhook").exists())


class FinanceTests(TestCase):
    def test_revenue_commission_profit(self):
        settings = CompanySettings.load()
        user = get_user_model().objects.create_user("anna@x.com", password="pw123456")
        DashboardProfile.objects.create(user=user, role="ae", ae_name="Anna", commission_pct=Decimal("25"))
        fctx = FinanceContext.build(settings)

        deal = Deal(ghl_id="d1", ae_name="Anna", total_loan_amount=Decimal("400000"), origination_points=Decimal("2"))
        fin = deal_financials(deal, fctx)
        self.assertEqual(fin["revenue"], Decimal("8000.00"))
        self.assertTrue(fin["revenue_estimated"])
        self.assertEqual(fin["ae_commission"], Decimal("2000.00"))
        self.assertEqual(fin["profit"], Decimal("6000.00"))  # no broker → no referral cost
        self.assertTrue(fin["complete"])

        broker_deal = Deal(ghl_id="d2", ae_name="Anna", origination_fee=Decimal("5000"), broker_name="Acme")
        fin = deal_financials(broker_deal, fctx)
        self.assertIsNone(fin["profit"])
        self.assertIn("broker_fee", fin["missing"])

        unknown_ae = deal_financials(Deal(ghl_id="d3", ae_name="Zed", origination_fee=Decimal("1000")), fctx)
        self.assertIn("ae_commission", unknown_ae["missing"])  # never silently zero


class DashboardDataMixin:
    def build(self):
        make_stages()
        ctx = make_ctx()
        now = timezone.now()
        User = get_user_model()
        self.ceo = User.objects.create_user("rod@x.com", password="pw123456")
        DashboardProfile.objects.create(user=self.ceo, role="ceo", ae_name="Rod")
        self.anna = User.objects.create_user("anna@x.com", password="pw123456")
        self.anna_p = DashboardProfile.objects.create(user=self.anna, role="ae", ae_name="Anna", commission_pct=Decimal("20"))
        self.leoma = User.objects.create_user("leoma@x.com", password="pw123456")
        self.leoma_p = DashboardProfile.objects.create(user=self.leoma, role="processor", analyst_name="Leoma")
        # Anna: funded today; Rod: stalled in underwriting.
        upsert_opportunity(opp("a1", stage="quick_app", created=now - timedelta(days=40), f_ae="Anna"), ctx)
        upsert_opportunity(opp("a1", stage="funded", created=now - timedelta(days=40), stage_at=now - timedelta(minutes=5),
                               f_ae="Anna", f_an="Leoma", f_amt="300000", f_fee="6000"), ctx)
        upsert_opportunity(opp("r1", stage="underwriting", created=now - timedelta(days=60), stage_at=now - timedelta(days=20),
                               f_ae="Rod", f_amt="200000", f_fee="4000", contact="c2"), ctx)
        CompanySettings.objects.filter(pk=1).update(coverage_start=now - timedelta(days=90))


class MetricsTests(DashboardDataMixin, TestCase):
    def setUp(self):
        self.build()

    def test_overview_and_scope(self):
        period = resolve_period("today")
        ceo_view = overview(load_dataset(self.ceo), period)
        self.assertEqual(ceo_view["funded_loans"]["count"], 1)
        self.assertEqual(ceo_view["funded_loans"]["volume"], 300000.0)
        self.assertEqual(ceo_view["funded_revenue"]["actual"], 6000.0)
        self.assertEqual(ceo_view["contribution_profit"]["actual"], 4800.0)
        self.assertEqual(ceo_view["intervention"]["count"], 1)
        self.assertEqual(ceo_view["intervention"]["deal_ids"], ["r1"])
        self.assertEqual(len(ceo_view["trends"]), 12)
        self.assertEqual(ceo_view["trends"][-1]["funded"], 1)

        anna_ds = load_dataset(self.anna)
        self.assertEqual([d.ghl_id for d in anna_ds.deals], ["a1"])
        leoma_ds = load_dataset(self.leoma)
        self.assertEqual([d.ghl_id for d in leoma_ds.deals], ["a1"])

    def test_scorecards_ranked_by_profit(self):
        rows = scorecards(load_dataset(self.ceo), resolve_period("mtd"))["rows"]
        self.assertEqual(rows[0]["ae"], "Anna")
        self.assertEqual(rows[0]["funded_count"], 1)
        self.assertEqual(rows[0]["volume"]["target"], 1_000_000.0)


class IssueTests(DashboardDataMixin, TestCase):
    def setUp(self):
        self.build()

    def test_stalled_issue_lifecycle(self):
        refresh_issues()
        issue = DealIssue.objects.get(deal_id="r1", issue_type="stalled_stage")
        self.assertEqual(issue.severity, "critical")
        self.assertEqual(issue.status, "open")

        # Manual resolve holds while the condition persists.
        issue.status, issue.details = "resolved", {**issue.details, "hold": True}
        issue.save()
        refresh_issues()
        self.assertEqual(DealIssue.objects.filter(deal_id="r1", issue_type="stalled_stage").count(), 1)

        # Deal moves on → nothing re-opens and the hold clears.
        Deal.objects.filter(pk="r1").update(stage_key="clear_to_close", stage_index=9, last_stage_change_at=timezone.now())
        DealMilestone.objects.create(deal_id="r1", key="clear_to_close", reached_at=timezone.now())
        refresh_issues()
        issue.refresh_from_db()
        self.assertNotIn("hold", issue.details)

    def test_dormant_is_low_and_silent(self):
        from .models import Notification

        refresh_issues()  # baseline run: no notifications
        self.assertEqual(Notification.objects.count(), 0)
        upsert_opportunity(opp("old1", stage="term_sheet_sent", created=timezone.now() - timedelta(days=200),
                               stage_at=timezone.now() - timedelta(days=150), f_ae="Anna"), make_ctx())
        refresh_issues()
        issue = DealIssue.objects.get(deal_id="old1", issue_type="stalled_stage")
        self.assertEqual(issue.severity, "low")
        self.assertTrue(issue.details["dormant"])
        self.assertEqual(Notification.objects.filter(issue=issue).count(), 0)
        view = overview(load_dataset(self.ceo), resolve_period("mtd"))
        self.assertIn("old1", view["dormant"]["deal_ids"])
        self.assertNotIn("old1", view["intervention"]["deal_ids"])

    def test_missing_financials_on_funded_deal(self):
        Deal.objects.filter(pk="a1").update(origination_fee=None)
        refresh_issues()
        issue = DealIssue.objects.get(deal_id="a1", issue_type="missing_financials")
        self.assertEqual(issue.owner, self.leoma_p)


class ApiTests(DashboardDataMixin, TestCase):
    def setUp(self):
        self.build()
        self.client = APIClient()

    def login(self, email):
        resp = self.client.post("/api/dashboard/auth/login/", {"username": email, "password": "pw123456"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.json()['access']}")

    def test_requires_auth(self):
        self.assertEqual(self.client.get("/api/dashboard/overview/").status_code, 401)

    def test_role_modules(self):
        self.login("anna@x.com")
        me = self.client.get("/api/dashboard/me/").json()
        self.assertEqual(me["role"], "ae")
        self.assertEqual(self.client.get("/api/dashboard/overview/").status_code, 200)
        self.assertEqual(self.client.get("/api/dashboard/leaderboards/").status_code, 403)
        self.assertEqual(self.client.get("/api/dashboard/settings/").status_code, 403)
        self.assertEqual(self.client.get("/api/dashboard/deals/r1/").status_code, 404)  # not her deal
        self.assertEqual(self.client.get("/api/dashboard/deals/a1/").status_code, 200)

    def test_ceo_modules_and_drill(self):
        self.login("rod@x.com")
        for path in ("overview", "funnel", "loss", "scorecards", "leaderboards", "exceptions", "deals", "settings", "options", "status"):
            self.assertEqual(self.client.get(f"/api/dashboard/{path}/").status_code, 200, path)
        rows = self.client.post("/api/dashboard/deals/lookup/", {"ids": ["r1"]}, format="json").json()
        self.assertEqual(rows["count"], 1)
        self.assertEqual(self.client.get("/api/dashboard/leaderboards/?dimension=broker").status_code, 200)

    @mock.patch("dashboard.views._ghl_token", return_value="tok")
    @mock.patch("dashboard.ghl_client.create_contact_task", return_value={"id": "t1"})
    def test_task_assignment_rules(self, _task, _tok):
        self.login("anna@x.com")
        ok = self.client.post("/api/dashboard/deals/a1/tasks/", {"title": "Call borrower", "due_at": "2026-12-01", "assignee_id": self.leoma_p.id}, format="json")
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertTrue(ok.json()["synced_to_ghl"])
        self.assertTrue(_task.call_args.args[2].endswith("Z"))
        self.assertEqual(self.client.get("/api/dashboard/deals/?ordering=-funded").status_code, 200)
        outsider = DashboardProfile.objects.create(user=get_user_model().objects.create_user("x@x.com", password="pw123456"), role="processor", analyst_name="Mae")
        bad = self.client.post("/api/dashboard/deals/a1/tasks/", {"title": "x", "due_at": "2026-12-01", "assignee_id": outsider.id}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.leoma.dashboard_notifications.count(), 1)

    @mock.patch("dashboard.views._push_loss_to_ghl", return_value=False)
    def test_meta_validation(self, _push):
        self.login("rod@x.com")
        r = self.client.patch("/api/dashboard/deals/r1/meta/", {"paused": True}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.patch("/api/dashboard/deals/r1/meta/", {"loss_primary_reason": "Other"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.patch("/api/dashboard/deals/r1/meta/", {"loss_primary_reason": "Rate or points", "recoverable": "yes"}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_issue_resolve_requires_note(self):
        refresh_issues()
        issue = DealIssue.objects.get(deal_id="r1", issue_type="stalled_stage")
        self.login("rod@x.com")
        listed = self.client.get("/api/dashboard/exceptions/?status=active&period=mtd").json()
        self.assertEqual(listed["headline"]["deals"], 1)  # issue status must not filter deals
        self.assertEqual(self.client.patch(f"/api/dashboard/exceptions/{issue.id}/", {"action": "resolve"}, format="json").status_code, 400)
        r = self.client.patch(f"/api/dashboard/exceptions/{issue.id}/", {"action": "resolve", "resolution_note": "UW call booked"}, format="json")
        self.assertEqual(r.json()["status"], "resolved")
        r = self.client.patch(f"/api/dashboard/exceptions/{issue.id}/", {"action": "snooze", "snooze_reason": "x"}, format="json")
        self.assertEqual(r.status_code, 400)
