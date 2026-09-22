import { createContext } from "react";

/**
 * Shell affordances a routed page may borrow from the app frame.
 *
 * Why this exists: on a phone, a workspace page (the sessions transcript is
 * the first one) wants to own the whole viewport instead of stacking the app
 * header, the page header and its own toolbar — three bars eating half the
 * screen. `App.tsx` owns the state because it renders both the app header and
 * the routed pages; a page asks for the takeover, and reads back the flag to
 * render the affordances that would otherwise disappear with the chrome it
 * hides (the navigation drawer's hamburger).
 *
 * The takeover is below `lg` only — the flag is a request, and every consumer
 * applies it inside a `max-lg:` variant so a desktop viewport, or a page left
 * mounted across a resize, keeps the full app frame.
 *
 * The default value is a no-op rather than a throw: pages are rendered on
 * their own in tests and must not require the app frame.
 */
export interface AppShellContextValue {
  /** Below lg the page owns the viewport: no app header, no page header. */
  immersive: boolean;
  /** Ask for (or give back) that takeover. */
  setImmersive: (immersive: boolean) => void;
  /** Open the app-wide navigation drawer — the app header's hamburger. */
  openNavigation: () => void;
}

export const AppShellContext = createContext<AppShellContextValue>({
  immersive: false,
  setImmersive: () => {},
  openNavigation: () => {},
});
