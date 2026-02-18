"""
Seed script for compliance rules database.

Populates launchpad.compliance_rules with real regulatory requirements for
CE, UKCA, WEEE, RoHS, ToyEN71, and DPP (Digital Product Passport 2026).

Usage:
    python scripts/seed_compliance_rules.py
    python scripts/seed_compliance_rules.py --clear
    python scripts/seed_compliance_rules.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from repo root: python scripts/seed_compliance_rules.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from dotenv import load_dotenv

from services.db_connection import connect, resolve_dsn


# ---------------------------------------------------------------------------
# Compliance rules seed data
# ---------------------------------------------------------------------------

COMPLIANCE_RULES: list[dict] = [
    # -----------------------------------------------------------------------
    # CE Marking — Electronics
    # -----------------------------------------------------------------------
    {
        "regime": "CE",
        "category_pattern": r"electronic|electrical|device|gadget|charger|adapter|power supply|battery|smartphone|tablet|laptop|computer|headphone|speaker|camera|tv|television|monitor|printer|appliance",
        "requirement_name": "CE Marking — Electronics",
        "requirement_description": (
            "All electrical and electronic equipment placed on the EU market must bear the CE mark. "
            "Applicable directives include the Low Voltage Directive (LVD) 2014/35/EU and the "
            "Electromagnetic Compatibility Directive (EMC) 2014/30/EU. "
            "Manufacturer must compile a Technical File and issue an EU Declaration of Conformity (DoC)."
        ),
        "documentation_required": [
            "EU Declaration of Conformity (DoC)",
            "Technical File",
            "Test Reports (LVD / EMC)",
            "CE Mark Label",
            "Authorised Representative (EU) appointment letter",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2014-04-20",
        "source_url": "https://ec.europa.eu/growth/single-market/ce-marking_en",
    },
    # -----------------------------------------------------------------------
    # CE Marking — Toys
    # -----------------------------------------------------------------------
    {
        "regime": "CE",
        "category_pattern": r"toy|game|doll|puzzle|board game|action figure|stuffed animal|plush|building block|lego|playset",
        "requirement_name": "CE Marking — Toys",
        "requirement_description": (
            "Toys sold in the EU must comply with the Toy Safety Directive 2009/48/EC. "
            "CE marking is mandatory and must be accompanied by a Technical File, "
            "EU Declaration of Conformity, and EN 71 test reports. "
            "Age grading and warning labels are required."
        ),
        "documentation_required": [
            "EU Declaration of Conformity (DoC)",
            "Technical File",
            "EN 71 Test Reports (Parts 1–3 minimum)",
            "CE Mark Label",
            "Age Grading Assessment",
            "Warning Labels",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2011-07-20",
        "source_url": "https://ec.europa.eu/growth/sectors/toys/safety_en",
    },
    # -----------------------------------------------------------------------
    # CE Marking — Machinery
    # -----------------------------------------------------------------------
    {
        "regime": "CE",
        "category_pattern": r"machine|machinery|power tool|drill|saw|grinder|lathe|press|conveyor|robot|industrial equipment|motor|pump|compressor",
        "requirement_name": "CE Marking — Machinery",
        "requirement_description": (
            "Machinery placed on the EU market must comply with the Machinery Directive 2006/42/EC "
            "(replaced by Machinery Regulation (EU) 2023/1230 from January 2027). "
            "A risk assessment must be conducted, and a Technical File compiled. "
            "An EU Declaration of Conformity must be issued before CE marking is applied."
        ),
        "documentation_required": [
            "EU Declaration of Conformity (DoC)",
            "Technical File",
            "Risk Assessment",
            "CE Mark Label",
            "Instructions for Use (in all relevant EU languages)",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2009-12-29",
        "source_url": "https://ec.europa.eu/growth/sectors/mechanical-engineering/machinery_en",
    },
    # -----------------------------------------------------------------------
    # CE Marking — Medical Devices
    # -----------------------------------------------------------------------
    {
        "regime": "CE",
        "category_pattern": r"medical device|medical equipment|diagnostic|surgical|implant|prosthetic|wheelchair|hearing aid|blood pressure|glucose monitor|thermometer|bandage|wound care",
        "requirement_name": "CE Marking — Medical Devices",
        "requirement_description": (
            "Medical devices placed on the EU market must comply with the Medical Device Regulation "
            "(EU) 2017/745 (MDR). A clinical evaluation is required, and devices must be registered "
            "in the EUDAMED database. Notified Body involvement is required for Class IIa, IIb, and III devices."
        ),
        "documentation_required": [
            "EU Declaration of Conformity (DoC)",
            "Technical Documentation",
            "Clinical Evaluation Report",
            "EUDAMED Registration",
            "Notified Body Certificate (Class IIa/IIb/III)",
            "Post-Market Surveillance Plan",
            "Instructions for Use (IFU)",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2021-05-26",
        "source_url": "https://ec.europa.eu/health/medical-devices-sector/new-regulations_en",
    },
    # -----------------------------------------------------------------------
    # CE Marking — Personal Protective Equipment
    # -----------------------------------------------------------------------
    {
        "regime": "CE",
        "category_pattern": r"ppe|helmet|glove|safety glasses|goggles|respirator|mask|safety boot|high.?vis|protective clothing|hard hat|ear protection|face shield",
        "requirement_name": "CE Marking — Personal Protective Equipment (PPE)",
        "requirement_description": (
            "Personal Protective Equipment must comply with PPE Regulation (EU) 2016/425. "
            "Category II and III PPE requires Notified Body involvement. "
            "An EU Declaration of Conformity and Technical File are mandatory."
        ),
        "documentation_required": [
            "EU Declaration of Conformity (DoC)",
            "Technical File",
            "Notified Body Certificate (Category II/III)",
            "CE Mark Label",
            "Instructions for Use",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2018-04-21",
        "source_url": "https://ec.europa.eu/growth/sectors/personal-protective-equipment_en",
    },
    # -----------------------------------------------------------------------
    # UKCA Marking — Electronics
    # -----------------------------------------------------------------------
    {
        "regime": "UKCA",
        "category_pattern": r"electronic|electrical|device|gadget|charger|adapter|power supply|battery|smartphone|tablet|laptop|computer|headphone|speaker|camera|tv|television|monitor|printer|appliance",
        "requirement_name": "UKCA Marking — Electronics",
        "requirement_description": (
            "All electrical and electronic equipment placed on the Great Britain (England, Scotland, Wales) "
            "market must bear the UKCA mark (post-Brexit replacement for CE). "
            "Applicable UK legislation includes the Electrical Equipment (Safety) Regulations 2016 and "
            "the Electromagnetic Compatibility Regulations 2016. "
            "A UK Declaration of Conformity and Technical File are required."
        ),
        "documentation_required": [
            "UK Declaration of Conformity (UK DoC)",
            "Technical File",
            "Test Reports (UK LVD / EMC equivalent)",
            "UKCA Mark Label",
            "UK Responsible Person appointment letter",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2021-01-01",
        "source_url": "https://www.gov.uk/guidance/using-the-ukca-marking",
    },
    # -----------------------------------------------------------------------
    # UKCA Marking — Toys
    # -----------------------------------------------------------------------
    {
        "regime": "UKCA",
        "category_pattern": r"toy|game|doll|puzzle|board game|action figure|stuffed animal|plush|building block|lego|playset",
        "requirement_name": "UKCA Marking — Toys",
        "requirement_description": (
            "Toys sold in Great Britain must comply with the Toys (Safety) Regulations 2011 (as amended). "
            "UKCA marking is mandatory. A UK Declaration of Conformity and Technical File are required, "
            "along with EN 71 test reports and age grading assessments."
        ),
        "documentation_required": [
            "UK Declaration of Conformity (UK DoC)",
            "Technical File",
            "EN 71 Test Reports",
            "UKCA Mark Label",
            "Age Grading Assessment",
            "Warning Labels",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2021-01-01",
        "source_url": "https://www.gov.uk/guidance/toy-safety-regulations",
    },
    # -----------------------------------------------------------------------
    # UKCA Marking — Machinery
    # -----------------------------------------------------------------------
    {
        "regime": "UKCA",
        "category_pattern": r"machine|machinery|power tool|drill|saw|grinder|lathe|press|conveyor|robot|industrial equipment|motor|pump|compressor",
        "requirement_name": "UKCA Marking — Machinery",
        "requirement_description": (
            "Machinery placed on the Great Britain market must comply with the Supply of Machinery "
            "(Safety) Regulations 2008 (as amended post-Brexit). "
            "A risk assessment, Technical File, and UK Declaration of Conformity are required."
        ),
        "documentation_required": [
            "UK Declaration of Conformity (UK DoC)",
            "Technical File",
            "Risk Assessment",
            "UKCA Mark Label",
            "Instructions for Use (in English)",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2021-01-01",
        "source_url": "https://www.gov.uk/guidance/machinery-safety-regulations",
    },
    # -----------------------------------------------------------------------
    # UKCA Marking — Medical Devices
    # -----------------------------------------------------------------------
    {
        "regime": "UKCA",
        "category_pattern": r"medical device|medical equipment|diagnostic|surgical|implant|prosthetic|wheelchair|hearing aid|blood pressure|glucose monitor|thermometer|bandage|wound care",
        "requirement_name": "UKCA Marking — Medical Devices",
        "requirement_description": (
            "Medical devices placed on the Great Britain market must comply with the Medical Devices "
            "Regulations 2002 (as amended). Devices must be registered with the MHRA. "
            "UK Approved Body involvement is required for higher-risk device classes."
        ),
        "documentation_required": [
            "UK Declaration of Conformity (UK DoC)",
            "Technical Documentation",
            "Clinical Evaluation Report",
            "MHRA Registration",
            "UK Approved Body Certificate (Class IIa/IIb/III)",
            "Instructions for Use (IFU) in English",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2021-01-01",
        "source_url": "https://www.gov.uk/guidance/regulating-medical-devices-in-the-uk",
    },
    # -----------------------------------------------------------------------
    # WEEE — All Electrical/Electronic Equipment
    # -----------------------------------------------------------------------
    {
        "regime": "WEEE",
        "category_pattern": r"electronic|electrical|device|gadget|charger|adapter|power supply|battery|smartphone|tablet|laptop|computer|headphone|speaker|camera|tv|television|monitor|printer|appliance|lamp|lighting|tool|toy.*electric|electric.*toy",
        "requirement_name": "WEEE Producer Registration",
        "requirement_description": (
            "Under the Waste Electrical and Electronic Equipment Directive (2012/19/EU), "
            "producers placing EEE on the EU market must register with the national WEEE register "
            "in each member state where they sell. Producers must finance the collection, treatment, "
            "and recycling of WEEE. The crossed-out wheelie bin symbol must appear on products."
        ),
        "documentation_required": [
            "WEEE Producer Registration Certificate (per EU member state)",
            "Crossed-out Wheelie Bin Label on product/packaging",
            "Annual WEEE Compliance Report",
            "Producer Compliance Scheme membership (if applicable)",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2014-02-14",
        "source_url": "https://ec.europa.eu/environment/topics/waste-and-recycling/waste-electrical-and-electronic-equipment-weee_en",
    },
    {
        "regime": "WEEE",
        "category_pattern": r"electronic|electrical|device|gadget|charger|adapter|power supply|battery|smartphone|tablet|laptop|computer|headphone|speaker|camera|tv|television|monitor|printer|appliance|lamp|lighting|tool|toy.*electric|electric.*toy",
        "requirement_name": "WEEE UK Producer Registration",
        "requirement_description": (
            "UK producers of EEE must register with the Environment Agency (England), "
            "SEPA (Scotland), NRW (Wales), or NIEA (Northern Ireland) under the "
            "Waste Electrical and Electronic Equipment Regulations 2013 (as amended). "
            "The crossed-out wheelie bin symbol is required on products sold in the UK."
        ),
        "documentation_required": [
            "UK WEEE Producer Registration Number",
            "Crossed-out Wheelie Bin Label",
            "Annual WEEE Return submission",
            "UK Producer Compliance Scheme membership",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2014-01-01",
        "source_url": "https://www.gov.uk/guidance/electrical-and-electronic-equipment-eee-producer-responsibility",
    },
    # -----------------------------------------------------------------------
    # RoHS — Electronics
    # -----------------------------------------------------------------------
    {
        "regime": "RoHS",
        "category_pattern": r"electronic|electrical|device|gadget|charger|adapter|power supply|battery|smartphone|tablet|laptop|computer|headphone|speaker|camera|tv|television|monitor|printer|appliance|circuit board|pcb|semiconductor|led|lighting",
        "requirement_name": "RoHS Compliance — Restriction of Hazardous Substances",
        "requirement_description": (
            "The RoHS Directive (2011/65/EU, recast) restricts the use of ten hazardous substances "
            "in electrical and electronic equipment: lead (Pb), mercury (Hg), cadmium (Cd), "
            "hexavalent chromium (Cr VI), polybrominated biphenyls (PBB), "
            "polybrominated diphenyl ethers (PBDE), DEHP, BBP, DBP, and DIBP. "
            "Maximum concentration values (MCVs) apply per homogeneous material. "
            "A RoHS Declaration of Conformity and technical documentation are required."
        ),
        "documentation_required": [
            "RoHS Declaration of Conformity",
            "Material Composition Test Reports (ICP-MS or XRF)",
            "Bill of Materials (BOM) with RoHS status per component",
            "Supplier RoHS Declarations",
            "Technical Documentation",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2013-01-02",
        "source_url": "https://ec.europa.eu/environment/topics/waste-and-recycling/rohs-directive_en",
    },
    {
        "regime": "RoHS",
        "category_pattern": r"electronic|electrical|device|gadget|charger|adapter|power supply|battery|smartphone|tablet|laptop|computer|headphone|speaker|camera|tv|television|monitor|printer|appliance|circuit board|pcb|semiconductor|led|lighting",
        "requirement_name": "RoHS UK Compliance",
        "requirement_description": (
            "The UK RoHS Regulations 2012 (as amended) restrict the same ten hazardous substances "
            "as EU RoHS in EEE placed on the Great Britain market. "
            "A UK RoHS Declaration of Conformity and supporting technical documentation are required."
        ),
        "documentation_required": [
            "UK RoHS Declaration of Conformity",
            "Material Composition Test Reports",
            "Bill of Materials (BOM) with RoHS status",
            "Supplier RoHS Declarations",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2013-01-02",
        "source_url": "https://www.gov.uk/guidance/rohs-compliance-and-guidance",
    },
    # -----------------------------------------------------------------------
    # Toy Safety EN 71 — Physical & Mechanical Safety
    # -----------------------------------------------------------------------
    {
        "regime": "ToyEN71",
        "category_pattern": r"toy|game|doll|puzzle|board game|action figure|stuffed animal|plush|building block|lego|playset|rattle|teether|baby toy|infant toy|children.*product|kids.*product",
        "requirement_name": "EN 71-1: Physical and Mechanical Safety",
        "requirement_description": (
            "EN 71-1 specifies physical and mechanical safety requirements for toys, including "
            "tests for small parts (choking hazard), sharp edges, sharp points, tensile strength, "
            "torque, and drop tests. Toys intended for children under 36 months must pass "
            "small parts cylinder test. Age grading must be determined and marked."
        ),
        "documentation_required": [
            "EN 71-1 Test Report from accredited laboratory",
            "Age Grading Assessment",
            "Small Parts Assessment (for toys for under-3s)",
            "Warning Labels (e.g., 'Not suitable for children under 3 years')",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2014-07-01",
        "source_url": "https://www.en-standard.eu/bs-en-71-1-2014-a1-2018-safety-of-toys-mechanical-and-physical-properties/",
    },
    {
        "regime": "ToyEN71",
        "category_pattern": r"toy|game|doll|puzzle|board game|action figure|stuffed animal|plush|building block|lego|playset|rattle|teether|baby toy|infant toy|children.*product|kids.*product",
        "requirement_name": "EN 71-2: Flammability",
        "requirement_description": (
            "EN 71-2 specifies flammability requirements for toys. Toys must not be made of "
            "easily flammable materials. Specific requirements apply to toys intended to be worn "
            "(e.g., costumes, masks) and toys that are intended to be entered by a child. "
            "Flammability test reports from an accredited laboratory are required."
        ),
        "documentation_required": [
            "EN 71-2 Flammability Test Report",
            "Material Composition Declaration",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2011-11-01",
        "source_url": "https://www.en-standard.eu/bs-en-71-2-2011-a1-2014-safety-of-toys-flammability/",
    },
    {
        "regime": "ToyEN71",
        "category_pattern": r"toy|game|doll|puzzle|board game|action figure|stuffed animal|plush|building block|lego|playset|rattle|teether|baby toy|infant toy|children.*product|kids.*product|paint.*set|art.*set|craft.*set|modelling clay|finger paint",
        "requirement_name": "EN 71-3: Migration of Certain Elements (Chemical Safety)",
        "requirement_description": (
            "EN 71-3 limits the migration of 19 chemical elements (including antimony, arsenic, "
            "barium, cadmium, chromium, lead, mercury, selenium) from toy materials. "
            "Three categories of toy materials are tested: dry/brittle/powder-like, liquid/sticky, "
            "and scraped-off. Applies to all accessible toy materials."
        ),
        "documentation_required": [
            "EN 71-3 Chemical Migration Test Report",
            "Material Safety Data Sheets (MSDS) for paints/coatings",
            "Supplier Chemical Compliance Declarations",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2013-07-01",
        "source_url": "https://www.en-standard.eu/bs-en-71-3-2019-a1-2021-safety-of-toys-migration-of-certain-elements/",
    },
    {
        "regime": "ToyEN71",
        "category_pattern": r"electric.*toy|toy.*electric|battery.*toy|toy.*battery|remote.*control.*toy|toy.*remote|electronic.*toy|toy.*electronic|toy.*light|toy.*sound|toy.*motor",
        "requirement_name": "EN 71-8 / EN 62115: Electrical Toy Safety",
        "requirement_description": (
            "Electrical toys must comply with EN 62115 (IEC 62115) for electrical safety. "
            "Requirements cover protection against electric shock, thermal hazards, and "
            "mechanical hazards from electrical components. Battery compartments must be "
            "secured with a tool. Low voltage limits apply for toys for children under 36 months."
        ),
        "documentation_required": [
            "EN 62115 Electrical Safety Test Report",
            "EN 71-1 Physical Safety Test Report",
            "Battery Compartment Assessment",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2006-01-01",
        "source_url": "https://www.en-standard.eu/bs-en-62115-2005-a12-2015-electric-toys-safety/",
    },
    # -----------------------------------------------------------------------
    # DPP — Electronics / Battery Passport (2026)
    # -----------------------------------------------------------------------
    {
        "regime": "DPP",
        "category_pattern": r"battery|batteries|ev battery|electric vehicle battery|industrial battery|portable battery|lithium|li-ion|lifepo4|lead.?acid|nickel",
        "requirement_name": "DPP — Battery Passport (EU Battery Regulation 2023/1542)",
        "requirement_description": (
            "The EU Battery Regulation (2023/1542) requires a Digital Battery Passport for "
            "EV batteries, industrial batteries ≥2 kWh, and LMT batteries from February 2027. "
            "The passport must contain: battery model, manufacturer, carbon footprint, "
            "recycled content, state of health, and supply chain due diligence data. "
            "A QR code linking to the passport must be affixed to the battery."
        ),
        "documentation_required": [
            "Digital Battery Passport (QR code accessible)",
            "Carbon Footprint Declaration",
            "Recycled Content Declaration",
            "Supply Chain Due Diligence Report",
            "State of Health (SoH) data",
            "Battery Management System (BMS) data export",
        ],
        "is_2026_dpp_relevant": True,
        "effective_date": "2027-02-18",
        "source_url": "https://ec.europa.eu/environment/topics/waste-and-recycling/batteries_en",
    },
    {
        "regime": "DPP",
        "category_pattern": r"electronic|electrical|smartphone|tablet|laptop|computer|tv|television|monitor|appliance|washing machine|dishwasher|refrigerator|fridge|freezer|oven|microwave|vacuum|air conditioner",
        "requirement_name": "DPP — Electronics Product Passport (Ecodesign for Sustainable Products)",
        "requirement_description": (
            "The Ecodesign for Sustainable Products Regulation (EU) 2024/1781 introduces Digital "
            "Product Passports for electronics and appliances. Products must carry a QR code "
            "linking to a passport containing: material composition, repairability score, "
            "spare parts availability, energy efficiency data, end-of-life instructions, "
            "and carbon footprint. Phased implementation from 2026–2030."
        ),
        "documentation_required": [
            "Digital Product Passport (QR code accessible)",
            "Material Composition Declaration",
            "Repairability Score Assessment",
            "Spare Parts Availability Declaration",
            "Energy Efficiency Data",
            "End-of-Life / Recycling Instructions",
            "Carbon Footprint Declaration",
        ],
        "is_2026_dpp_relevant": True,
        "effective_date": "2026-01-01",
        "source_url": "https://ec.europa.eu/growth/industry/sustainability/ecodesign-sustainable-products-regulation_en",
    },
    # -----------------------------------------------------------------------
    # DPP — Textiles (2026)
    # -----------------------------------------------------------------------
    {
        "regime": "DPP",
        "category_pattern": r"textile|clothing|apparel|garment|fabric|fashion|shirt|trousers|dress|jacket|coat|shoes|footwear|sock|underwear|sportswear|activewear|knitwear|denim|jeans",
        "requirement_name": "DPP — Textile Product Passport (Ecodesign for Sustainable Products)",
        "requirement_description": (
            "Under the Ecodesign for Sustainable Products Regulation (EU) 2024/1781, textiles "
            "and apparel will require a Digital Product Passport. The passport must include: "
            "fibre composition, country of origin, care instructions, repairability information, "
            "recycled content percentage, chemical substances of concern, and carbon/water footprint. "
            "Expected to apply from 2026–2027 for most textile categories."
        ),
        "documentation_required": [
            "Digital Product Passport (QR code accessible)",
            "Fibre Composition Declaration",
            "Country of Origin Certificate",
            "Recycled Content Certification (e.g., GRS, RCS)",
            "Chemical Substances of Concern Declaration (REACH)",
            "Carbon Footprint / LCA Report",
            "Care and Repair Instructions",
        ],
        "is_2026_dpp_relevant": True,
        "effective_date": "2026-01-01",
        "source_url": "https://ec.europa.eu/growth/industry/sustainability/ecodesign-sustainable-products-regulation_en",
    },
    # -----------------------------------------------------------------------
    # DPP — Furniture (2026)
    # -----------------------------------------------------------------------
    {
        "regime": "DPP",
        "category_pattern": r"furniture|chair|table|sofa|couch|desk|shelf|shelving|cabinet|wardrobe|bed|mattress|bookcase|drawer|cupboard",
        "requirement_name": "DPP — Furniture Product Passport",
        "requirement_description": (
            "Furniture is among the priority product categories under the Ecodesign for Sustainable "
            "Products Regulation (EU) 2024/1781. A Digital Product Passport will be required, "
            "containing: material composition (wood species, recycled content), chemical treatments, "
            "disassembly instructions, repairability information, and end-of-life guidance. "
            "Exact implementation date to be confirmed by delegated act."
        ),
        "documentation_required": [
            "Digital Product Passport (QR code accessible)",
            "Material Composition Declaration (wood species, metals, fabrics)",
            "Recycled/Reclaimed Content Declaration",
            "Chemical Treatment Declaration (formaldehyde, flame retardants)",
            "Disassembly and Repair Instructions",
            "End-of-Life / Recycling Instructions",
        ],
        "is_2026_dpp_relevant": True,
        "effective_date": "2027-01-01",
        "source_url": "https://ec.europa.eu/growth/industry/sustainability/ecodesign-sustainable-products-regulation_en",
    },
    # -----------------------------------------------------------------------
    # Additional: REACH (Chemical Safety) — cross-cutting
    # -----------------------------------------------------------------------
    {
        "regime": "CE",
        "category_pattern": r"chemical|substance|mixture|paint|coating|adhesive|solvent|cleaning product|detergent|cosmetic|personal care|fragrance|perfume",
        "requirement_name": "REACH — Registration, Evaluation, Authorisation of Chemicals",
        "requirement_description": (
            "REACH Regulation (EC) No 1907/2006 requires manufacturers and importers of chemical "
            "substances to register them with ECHA if produced/imported in quantities ≥1 tonne/year. "
            "Substances of Very High Concern (SVHCs) on the Candidate List must be communicated "
            "to customers and consumers. Articles containing SVHCs >0.1% w/w must be notified to ECHA."
        ),
        "documentation_required": [
            "REACH Registration Number (if applicable)",
            "Safety Data Sheet (SDS) — 16-section format",
            "SVHC Declaration",
            "ECHA Notification (if article contains SVHC >0.1%)",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2007-06-01",
        "source_url": "https://echa.europa.eu/regulations/reach/understanding-reach",
    },
    # -----------------------------------------------------------------------
    # Additional: General Product Safety Regulation (GPSR) — EU 2023
    # -----------------------------------------------------------------------
    {
        "regime": "CE",
        "category_pattern": r"consumer product|household|kitchen|garden|sport|outdoor|camping|cycling|fitness|personal care|beauty|baby|infant|child",
        "requirement_name": "General Product Safety Regulation (GPSR) 2023/988",
        "requirement_description": (
            "The General Product Safety Regulation (EU) 2023/988 (replacing GPSD 2001/95/EC from "
            "December 2024) requires all consumer products to be safe. Obligations include: "
            "appointing an EU Responsible Person, maintaining a product safety contact point, "
            "registering on the Safety Gate portal for online marketplaces, and implementing "
            "a product traceability system. Incident reporting within 3 business days."
        ),
        "documentation_required": [
            "EU Responsible Person appointment letter",
            "Product Safety Contact Point details",
            "Safety Gate / RAPEX registration (for online marketplace sellers)",
            "Internal Risk Assessment",
            "Incident Reporting Procedure",
            "Product Traceability Records",
        ],
        "is_2026_dpp_relevant": False,
        "effective_date": "2024-12-13",
        "source_url": "https://ec.europa.eu/info/law/better-regulation/have-your-say/initiatives/12466-General-Product-Safety-Regulation_en",
    },
]


# ---------------------------------------------------------------------------
# Database functions
# ---------------------------------------------------------------------------


def seed_compliance_rules(conn: psycopg.Connection, rules: list[dict]) -> int:
    """Insert compliance rules into launchpad.compliance_rules.

    Uses INSERT ... ON CONFLICT DO NOTHING based on (regime, requirement_name)
    to allow safe re-runs without duplicating data.

    Args:
        conn: Open psycopg connection.
        rules: List of rule dicts matching the compliance_rules schema.

    Returns:
        Number of rows actually inserted (conflicts excluded).
    """
    insert_sql = """
        INSERT INTO launchpad.compliance_rules (
            regime,
            category_pattern,
            requirement_name,
            requirement_description,
            documentation_required,
            is_2026_dpp_relevant,
            effective_date,
            source_url
        ) VALUES (
            %(regime)s,
            %(category_pattern)s,
            %(requirement_name)s,
            %(requirement_description)s,
            %(documentation_required)s,
            %(is_2026_dpp_relevant)s,
            %(effective_date)s,
            %(source_url)s
        )
        ON CONFLICT (regime, requirement_name) DO NOTHING
    """

    # Ensure unique constraint exists (idempotent DDL guard)
    _ensure_unique_constraint(conn)

    inserted = 0
    with conn.cursor() as cur:
        for rule in rules:
            cur.execute(insert_sql, rule)
            inserted += cur.rowcount

    conn.commit()
    return inserted


def _ensure_unique_constraint(conn: psycopg.Connection) -> None:
    """Create a unique constraint on (regime, requirement_name) if not present.

    This allows ON CONFLICT DO NOTHING to work correctly on re-runs.
    The constraint is created as a partial unique index to be non-destructive.
    """
    check_sql = """
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'launchpad'
          AND t.relname = 'compliance_rules'
          AND c.conname = 'uq_compliance_rules_regime_name'
    """
    create_sql = """
        ALTER TABLE launchpad.compliance_rules
        ADD CONSTRAINT uq_compliance_rules_regime_name
        UNIQUE (regime, requirement_name)
    """
    with conn.cursor() as cur:
        cur.execute(check_sql)
        if cur.fetchone() is None:
            try:
                cur.execute(create_sql)
                conn.commit()
            except Exception:
                # Constraint may have been created concurrently — safe to ignore
                conn.rollback()


def clear_compliance_rules(conn: psycopg.Connection) -> int:
    """Delete all rows from launchpad.compliance_rules.

    Also cascades to launch_compliance_checklist via FK (ON DELETE CASCADE
    must be set, otherwise this will raise a FK violation).

    Args:
        conn: Open psycopg connection.

    Returns:
        Number of rows deleted.
    """
    with conn.cursor() as cur:
        cur.execute("DELETE FROM launchpad.compliance_rules")
        deleted = cur.rowcount
    conn.commit()
    return deleted


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed compliance rules into launchpad.compliance_rules.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/seed_compliance_rules.py
  python scripts/seed_compliance_rules.py --dry-run
  python scripts/seed_compliance_rules.py --clear
  python scripts/seed_compliance_rules.py --clear --dry-run
        """,
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete all existing compliance rules before seeding.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print rules that would be inserted without writing to the database.",
    )
    args = parser.parse_args()

    # Load environment variables from .env file
    load_dotenv()

    if args.dry_run:
        print(f"[DRY RUN] Would seed {len(COMPLIANCE_RULES)} compliance rules:")
        print()
        for i, rule in enumerate(COMPLIANCE_RULES, 1):
            dpp_flag = " [DPP 2026]" if rule["is_2026_dpp_relevant"] else ""
            print(
                f"  {i:2d}. [{rule['regime']}]{dpp_flag} {rule['requirement_name']}"
            )
            print(f"       Docs required: {len(rule['documentation_required'])}")
        print()
        print(f"[DRY RUN] Total: {len(COMPLIANCE_RULES)} rules (no DB changes made)")
        return

    # Resolve and normalise DSN
    try:
        dsn = resolve_dsn("LAUNCHPAD_DB_DSN", "MARKET_INTEL_DSN", "PG_DSN")
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Connecting to database...")
    try:
        conn = connect(dsn, role="launchpad_app")
    except Exception as exc:
        print(f"ERROR: Failed to connect to database: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.clear:
            print("Clearing existing compliance rules...")
            deleted = clear_compliance_rules(conn)
            print(f"  Deleted {deleted} existing rule(s).")

        print(f"Seeding {len(COMPLIANCE_RULES)} compliance rules...")
        inserted = seed_compliance_rules(conn, COMPLIANCE_RULES)

        skipped = len(COMPLIANCE_RULES) - inserted
        print()
        print("Summary:")
        print(f"  Total rules in seed data : {len(COMPLIANCE_RULES)}")
        print(f"  Inserted                 : {inserted}")
        print(f"  Skipped (already exist)  : {skipped}")
        print()

        # Print breakdown by regime
        from collections import Counter
        regime_counts: Counter = Counter(r["regime"] for r in COMPLIANCE_RULES)
        dpp_count = sum(1 for r in COMPLIANCE_RULES if r["is_2026_dpp_relevant"])
        print("Rules by regime:")
        for regime, count in sorted(regime_counts.items()):
            print(f"  {regime:<12} {count} rule(s)")
        print(f"  {'DPP 2026':<12} {dpp_count} rule(s) flagged as DPP-relevant")
        print()
        print("Done.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
