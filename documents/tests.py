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
