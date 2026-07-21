"""
templates_registry.py — Concierge Bot Document Template Registry
Camelot Property Management Services Corp.

Central catalog of every branded document template in the Camelot
Template Library. Each entry describes: where its pre-built files live
(Word / PDF / genuinely-fillable PDF), what category it belongs to, and
— for templates with a merge-tag master under masters/ — the field
schema used to auto-fill it via docgen.generate_docx().

`has_autofill: True` means a merge-tag master .docx exists under
masters/ and POST /templates/{id}/generate will work. Everything else
can still be listed and downloaded (branded docx/pdf, or a genuinely
fillable PDF the user types straight into) — it just isn't wired for
guided auto-fill yet. See README.md for how to add autofill to a
template that doesn't have it.
"""

from typing import Any, Dict, List, Optional

LIBRARY_DIR = "library"
MASTERS_DIR = "masters"


def _f(key: str, label: str, type_: str = "text", options: Optional[List[str]] = None,
       required: bool = False) -> Dict[str, Any]:
    field: Dict[str, Any] = {"key": key, "label": label, "type": type_}
    if options:
        field["options"] = options
    if required:
        field["required"] = True
    return field


TEMPLATES: Dict[str, Dict[str, Any]] = {

    # ---------------- Property Management Agreements ----------------
    "condo-coop-management-agreement": {
        "title": "Condo/Co-op Management Agreement",
        "category": "property_management_agreements",
        "description": "Full management agreement for condominium and cooperative buildings, "
                        "with Schedule A fee sheet and insurance exhibit.",
        "docx": "Camelot_Condo_Coop_Management_Agreement_Template.docx",
        "pdf": "Camelot_Condo_Coop_Management_Agreement_Template.pdf",
        "fillable_pdf": None,
        "fields": [], "has_autofill": False,
    },
    "rental-management-agreement": {
        "title": "Rental Management Agreement",
        "category": "property_management_agreements",
        "description": "Management agreement for rental (non-condo/co-op) residential and "
                        "commercial buildings.",
        "docx": "Camelot_Rental_Management_Agreement_Template.docx",
        "pdf": "Camelot_Rental_Management_Agreement_Template.pdf",
        "fillable_pdf": None,
        "fields": [], "has_autofill": False,
    },
    "office-management-agreement": {
        "title": "Office/Commercial Building Management Agreement",
        "category": "property_management_agreements",
        "description": "Management agreement for commercial office and mixed office/retail properties.",
        "docx": "Camelot_Office_Commercial_Building_Management_Agreement_Template.docx",
        "pdf": "Camelot_Office_Commercial_Building_Management_Agreement_Template.pdf",
        "fillable_pdf": None,
        "fields": [], "has_autofill": False,
    },
    "new-construction-rollout-agreement": {
        "title": "New Construction Condo Rollout Agreement",
        "category": "property_management_agreements",
        "description": "Sponsor management agreement for new-construction condominiums, "
                        "pre-closing through Board assignment.",
        "docx": "Camelot_New_Construction_Condo_Rollout_Agreement_Template.docx",
        "pdf": "Camelot_New_Construction_Condo_Rollout_Agreement_Template.pdf",
        "fillable_pdf": None,
        "fields": [], "has_autofill": False,
    },
    "insurance-coverage-summary": {
        "title": "Camelot Insurance Coverage Summary",
        "category": "property_management_agreements",
        "description": "Standalone summary of Camelot's own corporate insurance coverage, "
                        "also embedded as an exhibit in each management agreement.",
        "docx": "Camelot_Insurance_Coverage_Summary.docx",
        "pdf": "Camelot_Insurance_Coverage_Summary.pdf",
        "fillable_pdf": None,
        "fields": [], "has_autofill": False,
    },

    # ---------------- Admin & Compliance ----------------
    "coi-tracking-form": {
        "title": "Certificate of Insurance (COI) Tracking Form",
        "category": "admin_compliance",
        "description": "Track vendor/contractor COIs before work begins at a managed property.",
        "docx": "Camelot_COI_Tracking_Form.docx",
        "pdf": "Camelot_COI_Tracking_Form.pdf",
        "fillable_pdf": "Camelot_COI_Tracking_Form_FILLABLE.pdf",
        "fields": [
            _f("vendor_name", "Vendor / Contractor Name", required=True),
            _f("type_of_work", "Type of Work", required=True),
            _f("property", "Property / Building", required=True),
            _f("coi_received_date", "COI Received Date", "date"),
            _f("policy_expiration_date", "Policy Expiration Date", "date"),
            _f("gl_limits", "General Liability Limits"),
            _f("additional_insured", "Camelot Listed as Additional Insured", "select", ["Y", "N"]),
            _f("workers_comp", "Workers' Compensation on File", "select", ["Y", "N"]),
            _f("auto_liability", "Auto Liability on File", "select", ["Y", "N"]),
        ],
        "has_autofill": False,
    },
    "w9-request-cover-sheet": {
        "title": "W-9 Request Cover Sheet",
        "category": "admin_compliance",
        "description": "Request and track a completed IRS Form W-9 from a vendor before "
                        "payment or 1099 filing.",
        "docx": "Camelot_W9_Request_Cover_Sheet.docx",
        "pdf": "Camelot_W9_Request_Cover_Sheet.pdf",
        "fillable_pdf": "Camelot_W9_Request_Cover_Sheet_FILLABLE.pdf",
        "fields": [
            _f("vendor_name", "Vendor / Payee Name", required=True),
            _f("requested_by", "Requested By"),
            _f("date_requested", "Date Requested", "date"),
            _f("date_received", "Date Received", "date"),
            _f("tin_on_file", "TIN / EIN on File", "select", ["Y", "N"]),
        ],
        "has_autofill": False,
    },
    "bank-questionnaire-cover-sheet": {
        "title": "Bank/Lender Questionnaire Cover Sheet",
        "category": "admin_compliance",
        "description": "Track lender questionnaires for a unit sale, refinance, or mortgage. "
                        "$200 processing fee applies.",
        "docx": "Camelot_Bank_Questionnaire_Cover_Sheet.docx",
        "pdf": "Camelot_Bank_Questionnaire_Cover_Sheet.pdf",
        "fillable_pdf": "Camelot_Bank_Questionnaire_Cover_Sheet_FILLABLE.pdf",
        "fields": [
            _f("lender_name", "Lender / Bank Name", required=True),
            _f("loan_officer", "Loan Officer Contact"),
            _f("borrower", "Borrower / Unit Owner", required=True),
            _f("property_unit", "Property / Unit #", required=True),
            _f("date_received", "Date Received", "date"),
            _f("date_completed", "Date Completed", "date"),
        ],
        "has_autofill": False,
    },
    "rpie-abatement-filing-tracker": {
        "title": "RPIE / Tax Abatement Filing Tracker",
        "category": "admin_compliance",
        "description": "Track annual RPIE, RPIE-Exempt, and Co-op/Condo Tax Abatement filings.",
        "docx": "Camelot_RPIE_Abatement_Filing_Tracker.docx",
        "pdf": "Camelot_RPIE_Abatement_Filing_Tracker.pdf",
        "fillable_pdf": "Camelot_RPIE_Abatement_Filing_Tracker_FILLABLE.pdf",
        "fields": [
            _f("property", "Property", required=True),
            _f("filing_type", "Filing Type", "select", ["RPIE", "RPIE-Exempt", "Co-op/Condo Abatement", "Other"]),
            _f("filing_year", "Filing Year"),
            _f("due_date", "Due Date", "date"),
            _f("filed_date", "Filed Date", "date"),
        ],
        "has_autofill": False,
    },

    # ---------------- Leasing & Sales ----------------
    "rental-application-165-e62": {
        "title": "165 East 62nd Street Rental Application (2026)",
        "category": "leasing_sales",
        "description": "Full rental application package for 165 East 62nd Street, refreshed with "
                        "current branding, benchmarked fees, FARE Act compliance, and required "
                        "legal notices/disclosures.",
        "docx": "Camelot_165_E62_Rental_Application_2026.docx",
        "pdf": "Camelot_165_E62_Rental_Application_2026.pdf",
        "fillable_pdf": None,
        "fields": [], "has_autofill": False,
    },
    "sales-package-cover-sheet": {
        "title": "Sales Package Cover Sheet",
        "category": "leasing_sales",
        "description": "Cover sheet for a condo/co-op purchaser board package.",
        "docx": "Camelot_Sales_Package_Cover_Sheet.docx",
        "pdf": "Camelot_Sales_Package_Cover_Sheet.pdf",
        "fillable_pdf": "Camelot_Sales_Package_Cover_Sheet_FILLABLE.pdf",
        "fields": [
            _f("applicant_name", "Applicant Name(s)", required=True),
            _f("unit_number", "Unit #", required=True),
            _f("purchase_price", "Purchase Price"),
            _f("board_interview_date", "Board Interview Date", "date"),
        ],
        "has_autofill": False,
    },
    "rental-package-cover-sheet": {
        "title": "Rental Package Cover Sheet",
        "category": "leasing_sales",
        "description": "Cover sheet for a condo/co-op rental applicant package.",
        "docx": "Camelot_Rental_Package_Cover_Sheet.docx",
        "pdf": "Camelot_Rental_Package_Cover_Sheet.pdf",
        "fillable_pdf": "Camelot_Rental_Package_Cover_Sheet_FILLABLE.pdf",
        "fields": [
            _f("applicant_name", "Applicant Name(s)", required=True),
            _f("unit_number", "Unit #", required=True),
            _f("lease_term", "Lease Term"),
            _f("monthly_rent", "Monthly Rent"),
        ],
        "has_autofill": False,
    },
    "unit-alteration-agreement": {
        "title": "Unit Alteration Agreement",
        "category": "leasing_sales",
        "description": "Short-form agreement for a Unit Holder undertaking a renovation or alteration.",
        "docx": "Camelot_Unit_Alteration_Agreement.docx",
        "pdf": "Camelot_Unit_Alteration_Agreement.pdf",
        "fillable_pdf": "Camelot_Unit_Alteration_Agreement_FILLABLE.pdf",
        "fields": [
            _f("unit_owner_name", "Unit Owner Name", required=True),
            _f("unit_number", "Unit #", required=True),
            _f("property_address", "Property Address", required=True),
            _f("contractor_name", "Contractor Name"),
            _f("estimated_start_date", "Estimated Start Date", "date"),
            _f("estimated_completion_date", "Estimated Completion Date", "date"),
        ],
        "has_autofill": False,
    },

    # ---------------- Board & Governance ----------------
    "board-meeting-proxy-form": {
        "title": "Board Meeting Proxy Form",
        "category": "board_governance",
        "description": "Proxy appointment for a Board/shareholder meeting.",
        "docx": "Camelot_Board_Meeting_Proxy_Form.docx",
        "pdf": "Camelot_Board_Meeting_Proxy_Form.pdf",
        "fillable_pdf": "Camelot_Board_Meeting_Proxy_Form_FILLABLE.pdf",
        "fields": [
            _f("owner_name", "Unit Owner / Shareholder Name", required=True),
            _f("unit_number", "Unit #", required=True),
            _f("property_address", "Property Address", required=True),
            _f("proxy_holder_name", "Proxy Holder Name", required=True),
            _f("meeting_type", "Meeting Type", "select", ["Annual", "Special"]),
            _f("meeting_date", "Meeting Date", "date"),
        ],
        "has_autofill": False,
    },
    "annual-special-meeting-notice": {
        "title": "Annual/Special Meeting Notice",
        "category": "board_governance",
        "description": "Notice of an annual or special meeting, distributed to Unit Owners/Shareholders.",
        "docx": "Camelot_Annual_Special_Meeting_Notice.docx",
        "pdf": "Camelot_Annual_Special_Meeting_Notice.pdf",
        "fillable_pdf": "Camelot_Annual_Special_Meeting_Notice_FILLABLE.pdf",
        "fields": [
            _f("meeting_type", "Meeting Type", "select", ["Annual", "Special"]),
            _f("meeting_date", "Date", "date", required=True),
            _f("meeting_time", "Time"),
            _f("location", "Location / Virtual Link"),
            _f("agenda", "Agenda Items", "textarea"),
        ],
        "has_autofill": False,
    },
    "board-meeting-minutes": {
        "title": "Board Meeting Minutes Template",
        "category": "board_governance",
        "description": "Minutes template for a Board meeting.",
        "docx": "Camelot_Board_Meeting_Minutes_Template.docx",
        "pdf": "Camelot_Board_Meeting_Minutes_Template.pdf",
        "fillable_pdf": "Camelot_Board_Meeting_Minutes_Template_FILLABLE.pdf",
        "fields": [
            _f("meeting_date", "Meeting Date", "date", required=True),
            _f("call_to_order_time", "Call to Order Time"),
            _f("members_present", "Board Members Present", "textarea"),
            _f("members_absent", "Board Members Absent"),
            _f("discussion_summary", "Discussion Summary", "textarea"),
            _f("adjournment_time", "Adjournment Time"),
        ],
        "has_autofill": False,
    },

    # ---------------- Reports & Financials ----------------
    "monthly-management-report-cover-sheet": {
        "title": "Monthly Management Report Cover Sheet",
        "category": "reports_financials",
        "description": "Cover sheet for the monthly financial package.",
        "docx": "Camelot_Monthly_Management_Report_Cover_Sheet.docx",
        "pdf": "Camelot_Monthly_Management_Report_Cover_Sheet.pdf",
        "fillable_pdf": "Camelot_Monthly_Management_Report_Cover_Sheet_FILLABLE.pdf",
        "fields": [
            _f("property", "Property", required=True),
            _f("reporting_month", "Reporting Month", required=True),
            _f("financial_highlights", "Financial Highlights", "textarea"),
            _f("occupancy_arrears_summary", "Occupancy / Arrears Summary", "textarea"),
        ],
        "has_autofill": False,
    },
    "purchase-order-form": {
        "title": "Purchase Order Form",
        "category": "reports_financials",
        "description": "Purchase order for goods/services on behalf of a managed property.",
        "docx": "Camelot_Purchase_Order_Form.docx",
        "pdf": "Camelot_Purchase_Order_Form.pdf",
        "fillable_pdf": "Camelot_Purchase_Order_Form_FILLABLE.pdf",
        "fields": [
            _f("po_number", "PO #", required=True),
            _f("vendor", "Vendor", required=True),
            _f("property", "Property", required=True),
            _f("description", "Description of Goods / Services", "textarea"),
            _f("total_amount", "Total Amount"),
        ],
        "has_autofill": False,
    },
    "transition-manifest-checklist": {
        "title": "Management Transition Manifest Checklist",
        "category": "reports_financials",
        "description": "Checklist for onboarding a new property or transitioning management "
                        "from a prior agent.",
        "docx": "Camelot_Transition_Manifest_Checklist.docx",
        "pdf": "Camelot_Transition_Manifest_Checklist.pdf",
        "fillable_pdf": "Camelot_Transition_Manifest_Checklist_FILLABLE.pdf",
        "fields": [
            _f("property", "Property", required=True),
            _f("prior_agent", "Prior Managing Agent"),
            _f("transition_date", "Transition Date", "date"),
        ],
        "has_autofill": False,
    },

    # ---------------- Project & Property Management ----------------
    "work-order-request-form": {
        "title": "Work Order Request Form",
        "category": "project_property_management",
        "description": "Resident/board work order request routed to the property management office.",
        "docx": "Camelot_Work_Order_Request_Form.docx",
        "pdf": "Camelot_Work_Order_Request_Form.pdf",
        "fillable_pdf": "Camelot_Work_Order_Request_Form_FILLABLE.pdf",
        "fields": [
            _f("date_of_request", "Date of Request", "date", required=True),
            _f("property_building", "Property / Building", required=True),
            _f("unit_location", "Unit / Location", required=True),
            _f("requested_by", "Requested By", required=True),
            _f("contact_phone", "Contact Phone"),
            _f("contact_email", "Contact Email"),
            _f("priority", "Priority", "select", ["Routine", "Urgent", "Emergency"], required=True),
            _f("category", "Category", "select", ["Plumbing", "Electrical", "HVAC", "General", "Other"]),
            _f("description", "Description of Work Needed", "textarea", required=True),
            _f("access_notes", "Access Instructions", "textarea"),
        ],
        "has_autofill": True,
        "master_docx": "work-order-request.docx",
    },
    "amenity-reservation-request-form": {
        "title": "Amenity & Common Area Reservation Request",
        "category": "project_property_management",
        "description": "Resident request to reserve an amenity or common area.",
        "docx": "Camelot_Amenity_Reservation_Request_Form.docx",
        "pdf": "Camelot_Amenity_Reservation_Request_Form.pdf",
        "fillable_pdf": "Camelot_Amenity_Reservation_Request_Form_FILLABLE.pdf",
        "fields": [
            _f("date_of_request", "Date of Request", "date", required=True),
            _f("building_property", "Building / Property", required=True),
            _f("unit_number", "Unit #", required=True),
            _f("resident_name", "Resident Name", required=True),
            _f("amenity_requested", "Amenity Requested", "select",
               ["Roof Deck", "Party Room", "Gym", "Bike Room", "Storage", "Guest Suite", "Other"]),
            _f("requested_dates", "Requested Date(s)"),
            _f("requested_times", "Requested Time(s)"),
            _f("number_of_guests", "Number of Guests", "number"),
        ],
        "has_autofill": False,
    },
    "capital-project-status-report": {
        "title": "Capital Project Status Report",
        "category": "project_property_management",
        "description": "Monthly status report for an active capital project, for Board distribution.",
        "docx": "Camelot_Capital_Project_Status_Report.docx",
        "pdf": "Camelot_Capital_Project_Status_Report.pdf",
        "fillable_pdf": "Camelot_Capital_Project_Status_Report_FILLABLE.pdf",
        "fields": [
            _f("project_name", "Project Name", required=True),
            _f("property", "Property", required=True),
            _f("start_date", "Start Date", "date"),
            _f("target_completion_date", "Target Completion Date", "date"),
            _f("approved_budget", "Approved Budget"),
            _f("amount_spent", "Amount Spent to Date"),
            _f("percent_complete", "% Complete"),
            _f("issues_risks", "Issues / Risks", "textarea"),
        ],
        "has_autofill": False,
    },
    "vendor-work-authorization": {
        "title": "Vendor / Contractor Work Authorization",
        "category": "project_property_management",
        "description": "Authorization for a vendor/contractor to begin work at a managed property.",
        "docx": "Camelot_Vendor_Work_Authorization.docx",
        "pdf": "Camelot_Vendor_Work_Authorization.pdf",
        "fillable_pdf": "Camelot_Vendor_Work_Authorization_FILLABLE.pdf",
        "fields": [
            _f("vendor_name", "Vendor / Contractor Name", required=True),
            _f("property", "Property", required=True),
            _f("scope_of_work", "Scope of Work", "textarea", required=True),
            _f("estimated_cost", "Estimated Cost"),
            _f("coi_on_file", "COI on File", "select", ["Y", "N"]),
        ],
        "has_autofill": False,
    },
}


def get_template(template_id: str) -> Optional[Dict[str, Any]]:
    return TEMPLATES.get(template_id)


def list_templates(category: Optional[str] = None) -> List[Dict[str, Any]]:
    out = []
    for tid, meta in TEMPLATES.items():
        if category and meta["category"] != category:
            continue
        out.append({
            "id": tid,
            "title": meta["title"],
            "category": meta["category"],
            "description": meta["description"],
            "has_docx": bool(meta.get("docx")),
            "has_pdf": bool(meta.get("pdf")),
            "has_fillable_pdf": bool(meta.get("fillable_pdf")),
            "has_autofill": meta.get("has_autofill", False),
            "field_count": len(meta.get("fields", [])),
        })
    return out


def list_categories() -> List[str]:
    seen: List[str] = []
    for meta in TEMPLATES.values():
        if meta["category"] not in seen:
            seen.append(meta["category"])
    return seen
