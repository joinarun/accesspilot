import { useId, useState, type FocusEvent, type KeyboardEvent, type ReactNode } from "react";

import { InfoIcon } from "./Icons";

interface TooltipProps {
  label: string;
  children: ReactNode;
}

export function Tooltip({ label, children }: TooltipProps) {
  const tooltipId = useId();
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  const closeWhenFocusLeaves = (event: FocusEvent<HTMLSpanElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget)) {
      setOpen(false);
      setDismissed(false);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Escape") {
      setOpen(false);
      setDismissed(true);
    }
  };

  return (
    <span
      className={`tooltip${dismissed ? " is-dismissed" : ""}`}
      onBlur={closeWhenFocusLeaves}
    >
      <button
        className="tooltip-trigger"
        type="button"
        aria-label={`Help: ${label}`}
        aria-describedby={open ? tooltipId : undefined}
        aria-expanded={open}
        onClick={() => {
          setDismissed(false);
          setOpen(true);
        }}
        onFocus={() => {
          setDismissed(false);
          setOpen(true);
        }}
        onKeyDown={handleKeyDown}
      >
        <InfoIcon />
      </button>
      <span
        id={tooltipId}
        className={`tooltip-content${open ? " is-open" : ""}`}
        role="tooltip"
      >
        {children}
      </span>
    </span>
  );
}
