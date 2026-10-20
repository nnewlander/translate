"""Run the one exact-span correction for the immutable failed D051 attempt."""

import run_m53_ch11_p03804_second_pass_recovery as recovery


recovery.OUT = recovery.CHAPTER / "second_pass_recovery" / "body_p_03804_v2"
recovery.EVIDENCE = recovery.CHAPTER / "explicit_numeric_representation_equivalence_evidence_v2.json"
base_d051_evidence = recovery.d051_evidence


def exact_span_evidence(source_sha: str) -> dict:
    payload = base_d051_evidence(source_sha)
    payload["scope"] = "One reviewed Chapter 11 recovery case only; exact converted-DOCX source span includes two spaces; no numeric parsing or automatic conversion."
    payload["equivalences"][0]["exact_source_span"] = "25  million"
    return payload


recovery.d051_evidence = exact_span_evidence


if __name__ == "__main__":
    recovery.main()
