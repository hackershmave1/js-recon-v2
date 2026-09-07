import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

export type TuningLever = "spec" | "base-url" | "wrapper";

interface TuningRailContextValue {
  isOpen: boolean;
  focusedLever: TuningLever | null;
  open: () => void;
  openWith: (lever: TuningLever) => void;
  close: () => void;
}

const TuningRailContext = createContext<TuningRailContextValue | null>(null);

export function TuningRailProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const [focusedLever, setFocusedLever] = useState<TuningLever | null>(null);

  const open = useCallback(() => { setIsOpen(true); setFocusedLever(null); }, []);
  const openWith = useCallback((lever: TuningLever) => { setIsOpen(true); setFocusedLever(lever); }, []);
  const close = useCallback(() => { setIsOpen(false); setFocusedLever(null); }, []);

  return (
    <TuningRailContext.Provider value={{ isOpen, focusedLever, open, openWith, close }}>
      {children}
    </TuningRailContext.Provider>
  );
}

// No-op context returned when the component is rendered outside TuningRailProvider
// (e.g. in tests that only wrap with TenantProvider). Handlers become no-ops so
// no crash occurs; the rail simply doesn't open.
const NO_OP: TuningRailContextValue = {
  isOpen: false, focusedLever: null,
  open: () => {}, openWith: () => {}, close: () => {},
};

export function useTuningRail(): TuningRailContextValue {
  return useContext(TuningRailContext) ?? NO_OP;
}
