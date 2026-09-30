export const sidebarStorageKey = "vibepod-board.sidebar";

export const narrowSidebarQuery = "(max-width: 980px)";

// Storage can be unavailable (privacy modes, sandboxed frames), so reading and writing
// the choice never throws; the sidebar then simply starts expanded.
export const readSidebarCollapsed = (): boolean => {
  try {
    return window.localStorage.getItem(sidebarStorageKey) === "collapsed";
  } catch {
    return false;
  }
};

export const writeSidebarCollapsed = (collapsed: boolean) => {
  try {
    window.localStorage.setItem(
      sidebarStorageKey,
      collapsed ? "collapsed" : "expanded",
    );
  } catch {
    // Keep the in-memory choice for this session.
  }
};

export const isTypingTarget = (target: EventTarget | null): boolean =>
  target instanceof HTMLInputElement ||
  target instanceof HTMLTextAreaElement ||
  target instanceof HTMLSelectElement ||
  (target instanceof HTMLElement && target.isContentEditable);

export const isSidebarShortcut = (event: KeyboardEvent): boolean =>
  event.key === "[" &&
  !event.defaultPrevented &&
  !event.ctrlKey &&
  !event.metaKey &&
  !event.altKey &&
  !isTypingTarget(event.target);
