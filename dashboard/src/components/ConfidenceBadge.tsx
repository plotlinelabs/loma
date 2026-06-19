interface ConfidenceBadgeProps {
  confidence: {
    resolved: boolean | null;
    confidence_score: number | null;
    category: string;
    reasoning: string;
  } | null;
}

const categoryStyles: Record<string, string> = {
  resolved: "bg-green-50 text-green-700 border-green-200",
  partial: "bg-yellow-50 text-yellow-700 border-yellow-200",
  unresolved: "bg-red-50 text-red-700 border-red-200",
  escalation_needed: "bg-orange-50 text-orange-700 border-orange-200",
  unknown: "bg-gray-50 text-gray-500 border-gray-200",
};

const categoryLabels: Record<string, string> = {
  resolved: "Resolved",
  partial: "Partial",
  unresolved: "Unresolved",
  escalation_needed: "Escalation Needed",
  unknown: "Unknown",
};

export default function ConfidenceBadge({ confidence }: ConfidenceBadgeProps) {
  if (!confidence) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs border bg-gray-50 text-gray-400 border-gray-200">
        Pending
      </span>
    );
  }

  const category = confidence.category || "unknown";
  const style = categoryStyles[category] || categoryStyles.unknown;
  const label = categoryLabels[category] || category;

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border ${style}`}
      title={confidence.reasoning}
    >
      {label}
      {confidence.confidence_score != null && (
        <span className="opacity-60">
          {Math.round(confidence.confidence_score * 100)}%
        </span>
      )}
    </span>
  );
}
