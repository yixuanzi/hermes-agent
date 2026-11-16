import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import type { AuthenticatedUser } from "../types";
import { getStoredUser } from "./auth";

interface CurrentUserContextValue {
  user: AuthenticatedUser | null;
  setUser: (user: AuthenticatedUser | null) => void;
}

const CurrentUserContext = createContext<CurrentUserContextValue | null>(null);

export function CurrentUserProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthenticatedUser | null>(getStoredUser);
  const value = useMemo(() => ({ user, setUser }), [user]);
  return <CurrentUserContext.Provider value={value}>{children}</CurrentUserContext.Provider>;
}

export function useCurrentUser(): AuthenticatedUser | null {
  const context = useContext(CurrentUserContext);
  return context ? context.user : null;
}

export function useSetCurrentUser(): (user: AuthenticatedUser | null) => void {
  const context = useContext(CurrentUserContext);
  if (!context) {
    throw new Error("useSetCurrentUser must be used within a CurrentUserProvider");
  }
  return context.setUser;
}
