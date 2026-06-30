import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { statusColors } from "@/lib/status-colors";

interface ConfidenceBadgeProps {
  confidence: {
    resolved: boolean | null;
    confidence_score: number | null;
    category: string;
    reasoning: string;
  } | null;
}

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
      <Badge variant="outline" className="bg-gray-50 text-gray-400 border-gray-200">
        Pending
      </Badge>
    );
  }

  const category = confidence.category || "unknown";
  const style = statusColors[category] || statusColors.unknown;
  const label = categoryLabels[category] || category;

  return (
    <Badge
      variant="outline"
      className={cn("gap-1.5", style)}
      title={confidence.reasoning}
    >
      {label}
      {confidence.confidence_score != null && (
        <span className="opacity-60">
          {Math.round(confidence.confidence_score * 100)}%
        </span>
      )}
    </Badge>
  );
}
