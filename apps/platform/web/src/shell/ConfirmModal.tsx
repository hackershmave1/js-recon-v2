import { useEffect, useRef, useState } from "react";
import "./confirmModal.css";

// An in-app confirmation / prompt dialog. It replaces window.confirm and window.prompt,
// which Chrome can SUPPRESS ("prevent this page from creating additional dialogs") so the
// guarded action silently no-ops — the root cause of the QA "Cancel/Delete does nothing"
// reports. Controlled: the caller renders it while an action is pending and owns the
// confirm/cancel handlers. Pass `input` to collect a value (a window.prompt replacement).
export interface ConfirmModalProps {
  title: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  input?: { label: string; initialValue?: string };
  onConfirm: (value: string) => void;
  onCancel: () => void;
}

export function ConfirmModal({
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  danger = false,
  input,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  const [value, setValue] = useState(input?.initialValue ?? "");
  const confirmRef = useRef<HTMLButtonElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // On open, focus the input (prompt) or a button, and let Esc cancel. For a danger
  // dialog focus the SAFE (cancel) button, not the destructive one, so a stray Enter
  // doesn't confirm — the native window.confirm gave these affordances too.
  useEffect(() => {
    (input ? inputRef.current : danger ? cancelRef.current : confirmRef.current)?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [input, danger, onCancel]);

  const disabled = input != null && value.trim() === "";
  function confirm() {
    if (!disabled) onConfirm(value.trim());
  }

  return (
    <div className="cfm-scrim" onClick={onCancel}>
      <div className="cfm" role="dialog" aria-modal="true" aria-label={title}
        onClick={(event) => event.stopPropagation()}>
        <h2 className="cfm-title">{title}</h2>
        {message && <p className="cfm-msg">{message}</p>}
        {input && (
          <label className="cfm-field">
            <span className="cfm-field-label">{input.label}</span>
            <input ref={inputRef} className="cfm-input" value={value}
              onChange={(event) => setValue(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") confirm(); }} />
          </label>
        )}
        <div className="cfm-foot">
          <button type="button" ref={cancelRef} className="cfm-btn cfm-cancel" onClick={onCancel}>{cancelLabel}</button>
          <button type="button" ref={confirmRef} disabled={disabled}
            className={"cfm-btn cfm-confirm" + (danger ? " danger" : "")} onClick={confirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
