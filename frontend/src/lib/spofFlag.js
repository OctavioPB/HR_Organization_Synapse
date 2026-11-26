// SPOF risk flag → badge class + label.
//
// Covers every value allowed by the risk_scores.flag CHECK constraint
// (migration 017).  A missing flag renders as "unknown" — never as "normal":
// absence of a score is not evidence of low structural risk.

const FLAGS = {
  critical:           { cls: "badge badge-critical", label: "critical" },
  critical_uncertain: { cls: "badge badge-critical-uncertain", label: "critical · review" },
  warning:            { cls: "badge badge-warning",  label: "warning" },
  elevated:           { cls: "badge badge-elevated", label: "elevated" },
  withdrawing:        { cls: "badge badge-warning",  label: "withdrawing" },
  normal:             { cls: "badge badge-normal",   label: "normal" },
};

const UNKNOWN = { cls: "badge badge-unknown", label: "unknown" };

export function spofFlagStyle(flag) {
  return (flag && FLAGS[flag]) || UNKNOWN;
}
