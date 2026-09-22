import { useContext } from "react";

import { AppShellContext, type AppShellContextValue } from "./app-shell-context";

/** The app frame's shell affordances. Safe to call outside `App.tsx`. */
export function useAppShell(): AppShellContextValue {
  return useContext(AppShellContext);
}
