from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    AdminDocumentSelection,
    Category,
    Document,
    DocumentRequest,
    OpportunityCardSubmission,
    UserDocumentUpload,
)

SECRET = "test-secret-key"


@override_settings(EXTERNAL_API_SECRET_KEY=SECRET)
class ExternalOpportunityApiTests(TestCase):
    def setUp(self):
        self.opp_id = "opp123"
        self.url = reverse("external-opportunity-details", args=[self.opp_id])
        OpportunityCardSubmission.objects.create(request_id=self.opp_id, form_data={"city": "Austin"})
        doc_request = DocumentRequest.objects.create(request_id=self.opp_id, title_company_name="Acme Title")
        doc = Document.objects.create(name="Bank Statements", category=Category.objects.create(name="Assets"))
        selection = AdminDocumentSelection.objects.create(request=doc_request, section_type="adhoc", document=doc)
        UserDocumentUpload.objects.create(
            admin_selection=selection, ghl_file_url="https://example.com/f.pdf", file_name="f.pdf", accepted=True
        )

    def test_missing_key_rejected(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_wrong_key_rejected(self):
        self.assertEqual(self.client.get(self.url, HTTP_X_API_KEY="nope").status_code, 401)

    @override_settings(EXTERNAL_API_SECRET_KEY="")
    def test_unconfigured_secret_fails_closed(self):
        self.assertEqual(self.client.get(self.url, HTTP_X_API_KEY="").status_code, 503)

    def test_post_not_allowed(self):
        self.assertEqual(self.client.post(self.url, HTTP_X_API_KEY=SECRET).status_code, 405)

    def test_returns_all_opportunity_data(self):
        resp = self.client.get(self.url, HTTP_X_API_KEY=SECRET)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["opportunity_card_submission"]["form_data"], {"city": "Austin"})
        dr = data["document_request"]
        self.assertEqual(dr["title_company"]["name"], "Acme Title")
        self.assertEqual(dr["summary"]["uploads_accepted"], 1)
        selection = dr["requested_documents"][0]
        self.assertEqual(selection["document"]["name"], "Bank Statements")
        self.assertEqual(selection["document"]["category"], "Assets")
        self.assertEqual(selection["uploads"][0]["file_url"], "https://example.com/f.pdf")
        self.assertEqual(selection["uploads"][0]["status"], "accepted")

    def test_bearer_header_accepted(self):
        resp = self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {SECRET}")
        self.assertEqual(resp.status_code, 200)

    def test_unknown_opportunity_404(self):
        url = reverse("external-opportunity-details", args=["missing"])
        self.assertEqual(self.client.get(url, HTTP_X_API_KEY=SECRET).status_code, 404)


from unittest import mock

import requests as _requests

from accounts.models import GHLAuthCredentials
from documents import ghl_service
from documents.ghl_service import PHONE_IN_USE_MESSAGE, UserFacingError

EMAIL_CONTACT = {"id": "c-email", "email": "rod@example.com", "phone": "+14155550108"}


class SurveyContactConflictTests(TestCase):
    """GHL won't let two contacts share a phone; the form must say so clearly."""

    def _upsert(self, phone):
        return ghl_service.upsert_contact_by_email_or_phone(
            "loc", full_name="Rod Test", email="rod@example.com", phone=phone, access_token="t"
        )

    def test_phone_owned_by_another_contact_is_rejected_without_updating(self):
        def lookup(location_id, email=None, phone=None, access_token=None):
            return EMAIL_CONTACT if email else {"id": "c-other", "phone": phone}

        with mock.patch.object(ghl_service, "lookup_duplicate_contact", side_effect=lookup), \
                mock.patch.object(ghl_service, "update_contact") as update:
            with self.assertRaisesMessage(UserFacingError, PHONE_IN_USE_MESSAGE):
                self._upsert("+14155550107")
        update.assert_not_called()

    def test_same_phone_updates_without_extra_lookup(self):
        with mock.patch.object(ghl_service, "lookup_duplicate_contact", return_value=EMAIL_CONTACT) as lookup, \
                mock.patch.object(ghl_service, "update_contact", return_value={"id": "c-email"}):
            result = self._upsert("(415) 555-0108")
        self.assertEqual(result["contact_id"], "c-email")
        self.assertEqual(lookup.call_count, 1)  # email lookup only

    def test_new_phone_not_used_elsewhere_updates_contact(self):
        def lookup(location_id, email=None, phone=None, access_token=None):
            return EMAIL_CONTACT if email else None

        with mock.patch.object(ghl_service, "lookup_duplicate_contact", side_effect=lookup), \
                mock.patch.object(ghl_service, "update_contact", return_value={"id": "c-email"}) as update:
            result = self._upsert("+14155550199")
        self.assertFalse(result["created"])
        update.assert_called_once()


class SurveySubmitErrorMessageTests(TestCase):
    def setUp(self):
        GHLAuthCredentials.objects.create(
            user_id="u", access_token="tok", refresh_token="r", expires_in=3600, location_id="loc"
        )
        self.url = reverse("loan-quote-survey")
        self.post = {"full_name": "Rod Test", "email": "rod@example.com", "phone": "(415) 555-0107"}

    def _submit(self, error):
        with mock.patch("documents.survey_opportunity.ensure_contact_and_opportunity", side_effect=error):
            return self.client.post(self.url, self.post)

    def test_user_facing_error_shown_as_is(self):
        resp = self._submit(UserFacingError(PHONE_IN_USE_MESSAGE))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already linked to a different email address")

    def test_raw_ghl_error_is_hidden(self):
        err = _requests.HTTPError(
            "400 Client Error: Bad Request for url: https://services.leadconnectorhq.com/contacts/abc"
        )
        with self.assertLogs("documents.views", level="ERROR"):
            resp = self._submit(err)
        self.assertContains(resp, "contact us for help")
        self.assertNotContains(resp, "leadconnectorhq")
        self.assertNotContains(resp, "400 Client Error")
