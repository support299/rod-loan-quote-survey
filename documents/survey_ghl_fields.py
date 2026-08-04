"""
Loan Quote Survey → GHL contact custom field catalog.

Most answers are stored as TEXT (exact form string).
Date inputs use GHL DATE type.
Dropdown/radio in the HTML form only affects the UI; submitted strings are written as-is.
"""

GHL_TEXT = "TEXT"
GHL_DATE = "DATE"

# Opportunity custom field: set once on first survey submit, never overwrite.
GHL_OPPORTUNITY_LOAN_ID_FIELD_NAME = "Loan ID"
GHL_OPPORTUNITY_LOAN_ID_FIELD_KEY = "opportunity.loan_id"

# key -> GHL contact custom field display name (create-if-missing by name).
LOAN_QUOTE_SURVEY_GHL_FIELDS = {
    "entity_name": "Entity Name",
    "broker_or_borrower": "Are you a Broker or Direct Borrower?",
    "account_executive": "Account Executive",
    "fico_score": "FICO Score",
    "fix_and_hold_properties": "Fix-and-Hold properties currently generating income (past 36 months)",
    "fix_and_flip_properties": "Fix-and-Flip properties sold (past 36 months)",
    "residential_ground_up_projects": "Residential Ground-up projects sold (past 36 months)",
    "subject_property_address": "Subject Property Address",
    "loan_type": "Loan Type",
    "dscr_loan_type": "DSCR Loan Type",
    "property_type": "Property Type",
    "units_residential": "How many units are Residential?",
    "units_commercial": "How many units are Commercial?",
    "number_of_units": "Number of Units",
    "total_number_of_units": "Total Number of Units",
    "residential_sqft_51_percent": "Is Residential Square footage at least 51% of the property sq footage?",
    "commercial_property_type": "Commercial Property Type",
    "please_specify": "Please Specify",
    "lot_owned": "Lot Owned?",
    "payoff": "Payoff",
    "bridge_loan_type": "Bridge Loan Type",
    "purchase_price_original_if_refi": "Purchase Price (Original Purchase Price if Refi)",
    "original_purchase_price": "Original Purchase Price",
    "original_purchase_date": "Original Purchase Date",
    "purchase_price": "Purchase Price",
    "rehab_budget": "Rehab Budget",
    "original_acquisition_cost": "Original Acquisition Cost",
    "original_acquisition_date": "Original Acquisition Date",
    "arv": "ARV (After Repair Value)",
    "estimated_property_value": "Estimated Property Value",
    "annual_insurance": "Annual Insurance",
    "annual_tax": "Annual Tax",
    "monthly_rent": "Monthly Rent",
    "existing_mortgage": "Existing Mortgage (put N/A if Purchase)",
    "exit_strategy": "Exit Strategy",
    "current_property_value": "Current Property Value",
    "as_is_value": "As-Is Value",
    "construction_budget": "Construction Budget",
    "final_value": "Final Value",
    "occupancy": "Occupancy",
    "units_leased": "How many units are leased?",
    "zoning_permit_status": "Zoning & Permit Status",
    "lot_purchase_price": "Lot Purchase Price",
    "intended_exit": "Intended Exit (Sale, Refi)",
    "existing_loan_balance": "Existing Loan Balance (N/A if Purchase Bridge)",
    "sms_consent": "SMS Consent",
}

# Form keys that must be GHL DATE (not TEXT). No datetime fields on this form.
LOAN_QUOTE_SURVEY_DATE_FIELDS = frozenset({
    "original_purchase_date",
    "original_acquisition_date",
})


def field_spec(form_key):
    """Return {name, data_type, db_type} for a form key."""
    name = LOAN_QUOTE_SURVEY_GHL_FIELDS[form_key]
    if form_key in LOAN_QUOTE_SURVEY_DATE_FIELDS:
        return {"name": name, "data_type": GHL_DATE, "db_type": "date"}
    return {"name": name, "data_type": GHL_TEXT, "db_type": "text"}


def normalize_survey_value(key, raw):
    """Return a single string suitable for GHL field_value, or None to skip."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return "Yes" if raw else "No"
    value = str(raw).strip()
    if not value:
        return None
    return value
