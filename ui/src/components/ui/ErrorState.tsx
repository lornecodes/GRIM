"use client";

interface ErrorStateProps {
  title?: string;
  message: string;
  onRetry?: () => void;
}

export function ErrorState({
  title = "Unavailable",
  message,
  onRetry,
}: ErrorStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12 gap-3">
      <div className="text-grim-error text-sm font-semibold">{title}</div>
      <div className="text-xs text-grim-text-dim text-center max-w-sm">
        {message}
      </div>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-2 text-xs text-grim-accent hover:text-grim-accent-dim transition-colors px-3 py-1 rounded border border-grim-accent/30 hover:bg-grim-accent/10"
        >
          Retry
        </button>
      )}
    </div>
  );
}
