import { useEffect, useState } from 'react';
import { CheckCircle2, XCircle } from 'lucide-react';

interface MutationStatusProps {
  isSuccess: boolean;
  isError: boolean;
  successMessage: string;
  errorMessage: string;
  autoDismissMs?: number;
}

export function MutationStatus({
  isSuccess,
  isError,
  successMessage,
  errorMessage,
  autoDismissMs = 4000,
}: MutationStatusProps) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!isSuccess && !isError) return;
    setVisible(true);
    const t = window.setTimeout(() => setVisible(false), autoDismissMs);
    return () => window.clearTimeout(t);
  }, [isSuccess, isError, autoDismissMs]);

  if (!visible) return null;

  return (
    <span
      className="chip anim-fade-up"
      role="status"
      style={
        isError
          ? { color: '#FF9DB2', background: 'rgba(255,107,138,0.10)', borderColor: 'rgba(255,107,138,0.35)' }
          : { color: '#7CF3C8', background: 'rgba(52,229,163,0.10)', borderColor: 'rgba(52,229,163,0.35)' }
      }
    >
      {isError ? <XCircle className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
      {isError ? errorMessage : successMessage}
    </span>
  );
}

export function extractErrorMessage(error: unknown, fallback: string): string {
  const anyErr = error as { response?: { status?: number; data?: { detail?: string } }; message?: string } | undefined;
  const status = anyErr?.response?.status;
  const detail = anyErr?.response?.data?.detail;
  if (status === 409) return '任务已在运行中，请稍后再试';
  if (status === 504) return '任务超时，请稍后重试';
  if (detail) return detail;
  return anyErr?.message || fallback;
}
