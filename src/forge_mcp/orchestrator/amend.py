from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from forge_mcp.artifacts import RunLayout
from forge_mcp.convergence import fingerprint
from forge_mcp.models import GapTriage, ProposedAmendment
from forge_mcp.state import durable_append, durable_replace, light_replace
from forge_mcp.triage import amendment_target_present, is_valid_citation


@dataclass
class AmendOutcome:
    """Result of apply_amendments — applied/rejected lists plus derived artifacts.

    Design: §5.3 the orchestrator commits amendments at the wave boundary;
        this dataclass carries the final spec state and churn signal for the
        caller to act on.
    Implementation: plain mutable dataclass; caller reads fields after the
        function returns.
    Example: AmendOutcome(applied=[...], rejected=[], new_spec='...', ...).
    """

    applied: list[ProposedAmendment]
    rejected: list[ProposedAmendment]
    new_spec: str
    new_fingerprint: str
    churn_fingerprint: frozenset[str]


def _proposal_signature(amendment: ProposedAmendment, fault_kind: str | None) -> str:
    """Return a stable per-proposal string for churn fingerprinting.

    Design: §6.7 the churn fingerprint must include both applied and rejected
        proposals so a repeatedly-rejected amendment still trips churn detection.
    Implementation: sort cited_sections for order-independence, join with '|',
        then append '|' and fault_kind (or '' if None).
    Example: _proposal_signature(pa, 'contradiction') -> 'Sec A|Sec B|contradiction'.
    """
    sections_key = "|".join(sorted(amendment.cited_sections))
    return f"{sections_key}|{fault_kind or ''}"


def apply_amendments(
    layout: RunLayout,
    *,
    spec_text: str,
    spec_fingerprint: str,
    proposed: list[tuple[str, GapTriage]],
    now: str,
) -> AmendOutcome:
    """Apply validated design-fault amendments to spec.md at a wave boundary.

    Design: §5.3 amendments are applied serially so each subsequent amendment
        sees the updated spec from earlier ones; the citation gate is re-run
        against the then-current spec to guard against stale text; rejected
        proposals still contribute to the churn fingerprint (§6.7).
    Implementation: iterate proposed in order; for each triage that carries a
        proposed_amendment, re-validate all cited_sections via is_valid_citation
        against the current spec AND verify the 'before' text is still present
        via amendment_target_present; on pass, replace the first occurrence of
        'before' with 'after', recompute SHA-256 fingerprint, durably write the
        updated spec and fingerprint, and append a structured entry to
        spec_amendments.md via durable_append; on failure, add to rejected only.
        All proposals (applied or rejected) feed churn_fingerprint.
        inputs/design.md is never touched (I4).
    Example: apply_amendments(lay, spec_text='old', ..., proposed=[('p1', t)])
        returns AmendOutcome with new_spec containing the amended text.
    """
    current_spec = spec_text
    current_fp = spec_fingerprint
    applied: list[ProposedAmendment] = []
    rejected: list[ProposedAmendment] = []
    churn_keys: list[str] = []

    for plan_id, triage in proposed:
        pa = triage.proposed_amendment
        if pa is None:
            continue

        churn_keys.append(_proposal_signature(pa, triage.fault_kind))

        citations_valid = all(is_valid_citation(c, current_spec) for c in pa.cited_sections)
        target_present = amendment_target_present(pa.before, current_spec)

        if citations_valid and target_present:
            new_spec = current_spec.replace(pa.before, pa.after, 1)
            new_fp = hashlib.sha256(new_spec.encode()).hexdigest()

            durable_replace(layout.spec_md, new_spec)
            light_replace(layout.spec_fingerprint, new_fp)

            entry = json.dumps(
                {
                    "plan_id": plan_id,
                    "now": now,
                    "fault_kind": triage.fault_kind,
                    "cited_sections": pa.cited_sections,
                    "before": pa.before,
                    "after": pa.after,
                    "rationale": pa.rationale,
                },
                sort_keys=True,
            )
            durable_append(layout.spec_amendments, entry + "\n")

            current_spec = new_spec
            current_fp = new_fp
            applied.append(pa)
        else:
            rejected.append(pa)

    return AmendOutcome(
        applied=applied,
        rejected=rejected,
        new_spec=current_spec,
        new_fingerprint=current_fp,
        churn_fingerprint=fingerprint(churn_keys),
    )
