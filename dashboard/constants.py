"""
Business constants for the CEO dashboard.

Only the 01 Loan Pipeline and 02 Processing Pipeline count as loan deals (confirmed
by Rod). The same GHL opportunity moves from 01 to 02, so the opportunity id is the
stable deal id for the whole life of a loan.
"""

LOAN_PIPELINE_NAME = "01 Loan Pipeline"
PROCESSING_PIPELINE_NAME = "02 Processing Pipeline"
PIPELINE_KEYS = {
    LOAN_PIPELINE_NAME: "loan",
    PROCESSING_PIPELINE_NAME: "processing",
}

# Ordered deal flow across both pipelines: (stage_key, pipeline name, GHL stage name).
STAGE_FLOW = [
    ("quick_app", LOAN_PIPELINE_NAME, "QUICK APP SUBMITTED"),
    ("under_review", LOAN_PIPELINE_NAME, "UNDER REVIEW"),
    ("term_sheet_sent", LOAN_PIPELINE_NAME, "TERMSHEET SENT"),
    ("term_sheet_accepted", LOAN_PIPELINE_NAME, "TERM SHEET ACCEPTED/LOAN MOVED TO PROCESSING"),
    ("qc_review", PROCESSING_PIPELINE_NAME, "QC REVIEW"),
    ("documents_uploaded", PROCESSING_PIPELINE_NAME, "DOCUMENT UPLOADED"),
    ("onboarded", PROCESSING_PIPELINE_NAME, "ONBOARDED"),
    ("processing", PROCESSING_PIPELINE_NAME, "PROCESSING"),
    ("underwriting", PROCESSING_PIPELINE_NAME, "UNDERWRITING"),
    ("clear_to_close", PROCESSING_PIPELINE_NAME, "CLEAR TO CLOSE"),
    ("funded", PROCESSING_PIPELINE_NAME, "FUNDED"),
    ("post_closing", PROCESSING_PIPELINE_NAME, "POST CLOSING"),
]
STAGE_KEYS = [s[0] for s in STAGE_FLOW]
STAGE_INDEX = {key: i for i, key in enumerate(STAGE_KEYS)}
STAGE_LABELS = {
    "quick_app": "Quick App Submitted",
    "under_review": "Under Review",
    "term_sheet_sent": "Term Sheet Sent",
    "term_sheet_accepted": "Term Sheet Accepted",
    "qc_review": "QC Review",
    "documents_uploaded": "Documents Uploaded",
    "onboarded": "Onboarded",
    "processing": "Processing",
    "underwriting": "Underwriting",
    "clear_to_close": "Clear to Close",
    "funded": "Funded",
    "post_closing": "Post Closing",
}
FUNDED_INDEX = STAGE_INDEX["funded"]

# Business milestones mapped onto stages (see brief modules 1-2).
#   application received  = quick app submitted (opportunity created)
#   qualified / quote     = term sheet sent
#   application complete  = onboarded ("deals onboarded")
#   submitted to UW       = underwriting
#   approved              = clear to close (GHL has no separate approval decision)
MILESTONE_TERM_SHEET = "term_sheet_sent"
MILESTONE_APP_COMPLETE = "onboarded"
MILESTONE_SUBMITTED_UW = "underwriting"
MILESTONE_APPROVED = "clear_to_close"
MILESTONE_FUNDED = "funded"

# GHL opportunity statuses. "abandoned" counts as lost (confirmed by Rod).
STATUS_OPEN = "open"
STATUS_WON = "won"
LOST_STATUSES = ("lost", "abandoned")

# Opportunity custom field display names read from GHL.
FIELD_AE = "Account Executive"
FIELD_AE_NAME = "Account Executive Name"
FIELD_ANALYST = "Loan Analysts"
FIELD_ANALYST_NAME = "Loan Analysts Full Name"
FIELD_ANALYST_2 = "Loan Analyst 2"
FIELD_LOAN_ID = "Loan ID"
FIELD_TOTAL_LOAN_AMOUNT = "Total Loan Amount"
FIELD_ORIGINATION_FEE = "Origination Fee"
FIELD_ORIGINATION_POINTS = "Origination Points"
FIELD_PROCESSING_FEE = "Processing Fee"
FIELD_UNDERWRITING_FEE = "Underwriting Fee"
FIELD_BROKER_FEE = "Broker Fee"
FIELD_BROKER_NAME = "Broker Name"
FIELD_BROKER_EMAIL = "Broker Email"
FIELD_TERMSHEET_STATUS = "Termsheet Status"
FIELD_ENTITY_NAME = "Entity Name"
FIELD_PROPERTY_ADDRESS = "Property Address"
FIELD_EXPECTED_CLOSE = "Expected Closing Date"
FIELD_AE_COMMISSION = "AE Commission"
FIELD_LOSS_REASON = "Loss Reason"
FIELD_LOSS_NOTES = "Loss Notes"
FIELD_REFERRAL_SOURCE = "Referral Source"
FIELD_PROCESSOR_1 = "Processor 1"
FIELD_PROCESSOR_2 = "Processor 2"
FIELD_DEAL_SOURCED_BY = "Deal Sourced By"
FIELD_BROKER_PAID_BY = "Broker Paid By"

# Rod (Nov 2026): processors are separate from loan analysts, up to 2 per loan.
PROCESSOR_NAMES = ["Shan Iqbal", "Pranav Bora"]
# Rod: AE commission is 30% of gross profit when Flip Funding assigned the lead,
# 50% when the AE sourced the deal (rates editable in Settings).
SOURCED_BY_COMPANY = "Flip Funding"
SOURCED_BY_AE = "AE"
DEAL_SOURCED_BY_OPTIONS = [SOURCED_BY_COMPANY, SOURCED_BY_AE]
# Rod: the broker is paid either by Flip Funding (out of gross profit) or by the title company.
BROKER_PAID_BY_COMPANY = "Flip Funding"
BROKER_PAID_BY_TITLE = "Title Company"
BROKER_PAID_BY_OPTIONS = [BROKER_PAID_BY_COMPANY, BROKER_PAID_BY_TITLE]

LOSS_REASONS = [
    "Rate or points",
    "Fees or cash to close",
    "Leverage or loan amount",
    "Credit or borrower eligibility",
    "Experience",
    "Property or program eligibility",
    "Appraisal or valuation",
    "Title or legal issue",
    "Incomplete documentation",
    "Borrower stopped responding",
    "Property contract canceled",
    "Borrower withdrew or postponed",
    "Competitor selected",
    "Internal delay or communication failure",
    "Capital partner declined or unavailable",
    "Other",
]
LOSS_OUTCOMES = ["declined", "withdrawn", "abandoned", "lost_to_competitor"]

# Fields the dashboard needs that do not exist in GHL yet. Created only by
# `manage.py dashboard_setup_ghl_fields --apply`.
NEW_GHL_FIELDS = [
    {"name": FIELD_EXPECTED_CLOSE, "data_type": "DATE"},
    {"name": FIELD_AE_COMMISSION, "data_type": "MONETORY"},
    {"name": FIELD_ANALYST_2, "data_type": "SINGLE_OPTIONS", "options_from": FIELD_ANALYST},
    {"name": FIELD_LOSS_REASON, "data_type": "SINGLE_OPTIONS", "options": LOSS_REASONS},
    {"name": FIELD_LOSS_NOTES, "data_type": "LARGE_TEXT"},
    {"name": FIELD_REFERRAL_SOURCE, "data_type": "TEXT"},
    {"name": FIELD_PROCESSOR_1, "data_type": "SINGLE_OPTIONS", "options": PROCESSOR_NAMES},
    {"name": FIELD_PROCESSOR_2, "data_type": "SINGLE_OPTIONS", "options": PROCESSOR_NAMES},
    {"name": FIELD_DEAL_SOURCED_BY, "data_type": "SINGLE_OPTIONS", "options": DEAL_SOURCED_BY_OPTIONS},
    {"name": FIELD_BROKER_PAID_BY, "data_type": "SINGLE_OPTIONS", "options": BROKER_PAID_BY_OPTIONS},
]

# Default probability (%) that an open deal in each stage funds. These are labelled
# management assumptions until enough history exists; CEO can edit them in settings.
DEFAULT_STAGE_PROBABILITIES = {
    "quick_app": 5,
    "under_review": 10,
    "term_sheet_sent": 20,
    "term_sheet_accepted": 40,
    "qc_review": 45,
    "documents_uploaded": 50,
    "onboarded": 55,
    "processing": 65,
    "underwriting": 75,
    "clear_to_close": 90,
}

ROLE_CEO = "ceo"
ROLE_AE = "ae"
ROLE_PROCESSOR = "processor"
ROLE_ANALYST = "analyst"
ROLE_POST_CLOSING = "post_closing"
ROLE_CHOICES = [
    (ROLE_CEO, "CEO / Admin"),
    (ROLE_AE, "Account Executive"),
    (ROLE_PROCESSOR, "Processor"),
    (ROLE_ANALYST, "Analyst"),
    (ROLE_POST_CLOSING, "Post Closing Specialist"),
]
# Roles that only see deals assigned to them.
SCOPED_ROLES = (ROLE_AE, ROLE_PROCESSOR)

ISSUE_STALLED = "stalled_stage"
ISSUE_NO_PROGRESS = "no_progress"
ISSUE_CLOSING_MISSED = "closing_date_missed"
ISSUE_INCOMPLETE_APP = "incomplete_application"
ISSUE_MISSING_LOSS = "missing_loss_info"
ISSUE_PAUSE_OVERDUE = "paused_review_overdue"
ISSUE_MISSING_FINANCIALS = "missing_financials"
ISSUE_TYPE_CHOICES = [
    (ISSUE_STALLED, "Stalled stage"),
    (ISSUE_NO_PROGRESS, "Missing meaningful progress"),
    (ISSUE_CLOSING_MISSED, "Closing date missed or moved"),
    (ISSUE_INCOMPLETE_APP, "Incomplete application"),
    (ISSUE_MISSING_LOSS, "Missing loss information"),
    (ISSUE_PAUSE_OVERDUE, "Paused review overdue"),
    (ISSUE_MISSING_FINANCIALS, "Missing loan financials"),
]
