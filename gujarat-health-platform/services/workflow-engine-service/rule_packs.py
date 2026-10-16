"""
rule_packs.py - swappable rule packs for the Workflow Recommendation Engine (CM-02).

The Master Step Library (step_library.py) holds the sequencing logic, which does not change between
rule regimes. A rule pack only changes the legal `basis` citation shown for a step, in the response
only (the seeded `steps` table is never mutated). Steps without an override keep their default
(central) citation.

Packs:
  GFR - General Financial Rules 2017 (Government of India): the default citations in step_library.py.
  CVC - Central Vigilance Commission guidelines (Compilation of guidelines on tenders and contracts).
  GOG - Government of Gujarat purchase rules. NOT verified for this demo: every citation is labelled
        "illustrative - to be confirmed by the Department", as the specification requires, and the
        pack is configured only after the Department confirms the rules.

This replaces the earlier West Bengal (WBFR) overlay. A legacy request with rule_regime="WBFR"
is served with the GOG pack (state-rules slot) so older callers keep working.
"""

TBC = "illustrative - to be confirmed by the Department"

RULE_PACKS = {
    "GFR": {
        "name": "General Financial Rules 2017 (Government of India)",
        "status": "configured",
        "applies_to": "Default; centrally sponsored schemes (e.g. NHM) and central funds",
        "overrides": {},
    },
    "CVC": {
        "name": "CVC guidelines (Compilation of guidelines on tenders and contracts)",
        "status": "configured",
        "applies_to": "Vigilance-sensitive steps across all agencies",
        "overrides": {
            "PQC": "CVC guidelines on pre-qualification criteria (fair, non-restrictive PQC)",
            "IPACT": "CVC guidelines on the Integrity Pact and Independent External Monitors",
            "PUB": "CVC guidelines on transparency: tender documents on the website / e-procurement portal",
            "VVERIFY": "CVC guidelines: verification of bidder credentials and debarment status before acceptance",
            "COI": "CVC guidelines on conflict-of-interest declarations by evaluation committee members",
            "NEGGUARD": "CVC guidelines: no post-tender negotiation except with L1 in exceptional cases (CVC circular 4/3/07)",
            "CVCDISC": "CVC guidelines on monthly disclosure of contracts awarded",
            "AIANOMALY": "CVC guidelines on detecting cartelisation and collusive bidding",
        },
    },
    "GOG": {
        "name": "Government of Gujarat purchase rules (to be confirmed)",
        "status": TBC,
        "applies_to": "State-funded procurement by GMSCL, the Commissionerate and GMERS",
        "overrides": {
            "APP": f"Government of Gujarat budget and annual procurement plan provisions ({TBC})",
            "COST": f"Government of Gujarat estimate and rate-reasonableness provisions; GMSCL last-purchase-price benchmark ({TBC})",
            "METHOD": f"Government of Gujarat purchase rules: method by value - GeM direct, L1 bidding, open tender on the "
                      f"State e-procurement portal, or rate-contract draw-down ({TBC})",
            "EMD": f"Government of Gujarat EMD provisions with MSE / Startup exemption ({TBC})",
            "PUB": f"Publication on the State e-procurement portal (Gujarat) or GeM; the platform imports and exports the record ({TBC})",
            "VVERIFY": f"GMSCL / Department debarment register and blacklisting procedure ({TBC})",
            "PBG": f"Government of Gujarat performance-security provisions ({TBC})",
            "STAMP": f"Gujarat Stamp Act 1958 schedule; e-stamping ({TBC} for the applicable article and rate)",
            "DLP": f"Government of Gujarat works / equipment defect-liability provisions ({TBC})",
            "MSE": f"Public Procurement Policy for MSEs Order 2012 as adopted by Government of Gujarat ({TBC})",
            "CVCDISC": f"Gujarat Vigilance Commission disclosure norms ({TBC})",
            "NABL": f"GMSCL quality-assurance policy: NABL testing of every batch before payment ({TBC})",
            "RCMATCH": f"GMSCL practice of parallel rate contracts at the L1 rate ({TBC})",
        },
    },
}

ALIASES = {"GFR": "GFR", "CVC": "CVC", "GOG": "GOG", "GUJARAT": "GOG", "GOVT_GUJARAT": "GOG", "WBFR": "GOG"}


def resolve_pack(regime: str | None) -> str:
    return ALIASES.get((regime or "GFR").strip().upper(), "GFR")
