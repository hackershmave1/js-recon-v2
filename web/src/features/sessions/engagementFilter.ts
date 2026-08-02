import { createContext, useContext } from "react";

// Shared engagement-filter state for the Sessions surface. Kept in its own leaf module
// (it imports no Shell/page) so SessionsView, the sidebar switcher, and the card grid
// can all read it without an import cycle. The selected id is also mirrored to
// localStorage so the standalone New Run page (a different route) can attach a new
// session to the active engagement.
export const ENGAGEMENT_STORAGE_KEY = "recon.engagementId";

export type EngagementFilter = {
  engagementId: string | null;
  setEngagementId: (id: string | null) => void;
};

export const EngagementFilterContext = createContext<EngagementFilter>({
  engagementId: null,
  setEngagementId: () => {},
});

export const useEngagementFilter = (): EngagementFilter =>
  useContext(EngagementFilterContext);
